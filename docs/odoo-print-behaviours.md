## Odoo IoT printing at a glance

Odoo does **not** have one universal printing pipeline.

It uses **different pipelines depending on the kind of printer and the kind of content**:

* **POS receipt printers**: render the receipt in the frontend, convert it to an image, send that image to the IoT box, and the IoT box converts it to ESC/POS raster commands.
* **Office/report printers**: handle **PDF** and print it on Windows using **SumatraPDF** when available, otherwise **Ghostscript**.
* **Raw printer-native devices**: send bytes directly to the printer, such as ESC/POS, ZPL, and similar formats.
* **Direct Epson printers**: skip the IoT box receipt-image path and send **Epson ePOS XML** directly.

So the real architecture is:

```text id="9gq6nr"
receipt/image flow
pdf/document flow
raw/native-printer-language flow
direct-printer protocol flow
```

Not:

```text id="3yvobm"
everything is HTML and the printer somehow figures it out
```

---

# 1) POS receipt printing: HTML -> canvas -> image -> IoT box -> ESC/POS

This is the core receipt path.

## Where it starts in Odoo

### `addons/point_of_sale/static/src/app/utils/printer/base_printer.js`

This file is the heart of the frontend receipt-print abstraction.

It does two critical things:

### A. Render receipt HTML to canvas

```js
image = this.processCanvas(
    await htmlToCanvas(receipt, { addClass: "pos-receipt-print" })
);
```

So Odoo starts from **receipt HTML**, then renders it to a **canvas**.

### B. Convert canvas to base64 JPEG

Still in the same file:

```js
processCanvas(canvas) {
    return canvas.toDataURL("image/jpeg").replace("data:image/jpeg;base64,", "");
}
```

So the generic receipt-print payload is a **base64 JPEG**, not HTML.

---

## Where that image is sent to the IoT box

### `addons/point_of_sale/static/src/app/utils/printer/hw_printer.js`

This is the hardware-printer bridge for the IoT box / hw proxy path.

It sends:

```js
sendPrintingJob(img) {
    return this.sendAction({ action: "print_receipt", receipt: img });
}
```

So the IoT endpoint receives:

```json
{
  "action": "print_receipt",
  "receipt": "<base64 jpeg>"
}
```

This is one of the most important discoveries: **the IoT box receives a rendered image for receipts**.

---

## Where the canvas itself comes from

### `addons/point_of_sale/static/src/app/utils/printer/base_printer.js`

Again, this line is the real clue:

```js
await htmlToCanvas(receipt, { addClass: "pos-receipt-print" })
```

So the rendering happens in the **POS frontend**, not in the IoT box.

That means the visual layout is browser-driven.

---

## Where the IoT box converts the image to printer bytes

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

(or equivalent path in your checkout, depending on branch/layout)

This file contains the printer base behavior used by the IoT drivers.

The receipt code:

```python
def print_receipt(self, data):
    receipt = b64decode(data['receipt'])
    im = Image.open(io.BytesIO(receipt))

    im = im.convert("L")
    im = ImageOps.invert(im)
    im = im.convert("1")

    print_command = getattr(self, 'format_%s' % self.receipt_protocol)(im)
    self.print_raw(print_command, action_unique_id=data.get("action_unique_id"))
```

This confirms the backend pipeline:

* decode base64 image
* load with Pillow
* convert to monochrome
* encode to printer-native bytes
* send RAW to the printer

---

## Where ESC/POS raster encoding happens

Also in:

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

Important methods:

* `format_escpos_bit_image_raster`
* `format_escpos_bit_image_column`
* `format_escpos`

These are the methods that turn the image into ESC/POS print commands.

This is where the IoT box becomes a true printer adapter.

---

## Receipt pipeline summary

The full Odoo receipt flow is:

```text id="m8nbi6"
Receipt HTML
  -> htmlToCanvas(...)
  -> canvas.toDataURL("image/jpeg")
  -> sendAction({ action: "print_receipt", receipt: img })
  -> IoT box decodes image
  -> Pillow converts image to 1-bit monochrome
  -> format_escpos(...)
  -> RAW bytes to receipt printer
```

### Files involved

* `addons/point_of_sale/static/src/app/utils/printer/base_printer.js`
* `addons/point_of_sale/static/src/app/utils/printer/hw_printer.js`
* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

---

# 2) Direct Epson printing: HTML -> canvas -> raster -> Epson ePOS XML

Odoo also has a direct Epson printer flow.

## Where it lives

### `addons/point_of_sale/static/src/app/utils/printer/epson_printer.js`

This file defines the direct Epson network printer path.

### What it does

It overrides `processCanvas(canvas)` and does **not** convert the canvas to JPEG.

Instead:

* `canvasToRaster(canvas)` turns the canvas into raster data
* `encodeRaster(...)` prepares the raster payload
* `ePOSPrint([...])` wraps it in Epson ePOS XML
* then it sends the XML over HTTP to the printer

Key parts:

```js
processCanvas(canvas) {
    const rasterData = this.canvasToRaster(canvas);
    const encodedData = this.encodeRaster(rasterData);
    return ePOSPrint([
        createElement("image", { ... }, [createTextNode(encodedData)]),
        createElement("cut", { type: "feed" }),
    ]);
}
```

And then:

```js
async sendPrintingJob(img) {
    const res = await fetch(this.address, params);
}
```

So this path is:

```text id="v5okly"
Receipt HTML
  -> htmlToCanvas(...)
  -> raster data
  -> Epson ePOS XML
  -> direct network request to Epson printer
```

### Files involved

* `addons/point_of_sale/static/src/app/utils/printer/epson_printer.js`
* `addons/point_of_sale/static/src/app/utils/printer/base_printer.js`

---

# 3) Report / office printing: PDF -> SumatraPDF or Ghostscript

For office printers and reports, the flow is completely different.

## Where it lives

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

This is the Windows printer driver implementation.

The key branch is in `_action_default`:

```python
document = b64decode(data['document'])
mimetype = guess_mimetype(document)
if mimetype == 'application/pdf':
    self.print_report(document)
else:
    self.print_raw(document, action_unique_id=action_unique_id)
```

So Odoo explicitly distinguishes:

* PDF documents
* everything else

---

## Where PDF printing happens

Still in:

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

The method:

```python
def print_report(self, data):
```

This method:

* writes PDF bytes to `document.pdf`
* checks whether `SumatraPDF.exe` exists
* uses SumatraPDF if present
* otherwise uses Ghostscript

That means PDF is the real format for document printing on Windows IoT.

### Files involved

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

---

# 4) Raw/native printer-language printing

This is the fallback path.

Again in:

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

Inside `_action_default`:

```python
if mimetype == 'application/pdf':
    self.print_report(document)
else:
    self.print_raw(document, action_unique_id=action_unique_id)
```

So for non-PDF bytes, Odoo just sends the raw bytes to the printer.

That supports things like:

* ESC/POS
* ZPL
* plain printer-native commands

### Files involved

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

---

# 5) Device classification: receipt printer vs office printer vs label printer

Odoo also classifies printer types.

## Where it lives

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

In `__init__`:

```python
if any(cmd in device['identifier'] for cmd in ['STAR', 'Receipt']):
    self.device_subtype = "receipt_printer"
elif "ZPL" in device['identifier']:
    self.device_subtype = "label_printer"
else:
    self.device_subtype = "office_printer"
```

This reinforces the same architectural split:

* receipt printers
* label printers
* office printers

These are not treated the same way.

### Files involved

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

---

# 6) Cash drawer support

The cash drawer is also treated as a receipt-printer control function.

## Where it lives

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

Commands are defined in:

```python
RECEIPT_PRINTER_COMMANDS = {
    'star': {...},
    'escpos': {...},
}
```

And drawer opening is implemented in:

```python
def open_cashbox(self, data):
    commands = self.RECEIPT_PRINTER_COMMANDS[self.receipt_protocol]
    for drawer in commands['drawers']:
        self.print_raw(drawer, action_unique_id=data.get("action_unique_id"))
```

So drawer support is really just specialized RAW printer control.

### Files involved

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

---

# 7) Test print behavior

Odoo’s test printing also varies by printer type.

## Where it lives

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

In `print_status`:

* receipt printer → prints ESC/POS receipt commands
* label printer → prints ZPL
* office printer → prints plain text

That is another strong clue that Odoo does **not** assume a single print representation.

### Files involved

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

---

# 8) Job monitoring and status tracking

The IoT driver also monitors print jobs after submission.

## Where it lives

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

Polling loop:

```python
def run(self):
    while True:
        for job_id in self.job_ids:
            self._check_job_status(job_id)
        time.sleep(1)
```

### `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

Actual Windows status inspection:

```python
def _check_job_status(self, job_id):
    job = win32print.GetJob(...)
```

This is how Odoo reports success/error/timeouts back up through the IoT system.

### Files involved

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`
* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

---

# 9) The most important conclusion

If someone only reads one section, it should be this:

## Odoo does not “send JSON to the IoT box and let the IoT box render everything.”

Instead, Odoo uses different pipelines:

### POS receipts

**Frontend renders receipt HTML to a canvas, converts it to a base64 JPEG, sends it to the IoT box, and the IoT box turns it into ESC/POS raster commands.**

Files:

* `addons/point_of_sale/static/src/app/utils/printer/base_printer.js`
* `addons/point_of_sale/static/src/app/utils/printer/hw_printer.js`
* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py`

### Direct Epson printers

**Frontend renders to canvas, converts it to Epson ePOS XML, and sends it directly to the printer.**

Files:

* `addons/point_of_sale/static/src/app/utils/printer/epson_printer.js`
* `addons/point_of_sale/static/src/app/utils/printer/base_printer.js`

### Reports / office documents

**Odoo treats PDF as the real document format and prints it via SumatraPDF or Ghostscript on Windows.**

Files:

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

### Raw/native printer formats

**Odoo sends raw bytes directly to the printer.**

Files:

* `odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py`

---

# 10) What this means for your replacement IoT agent

A complete Odoo-compatible print API should expose the real logical flows:

## Receipt printing

* `print_receipt_image(...)`
* `print_receipt_data(...)` if you want local rendering too
* `print_raw(...)`
* `feed_lines(...)`
* `feed_dots(...)`
* `cut_paper(...)`
* `open_cash_drawer(...)`

## Document printing

* `print_pdf(...)`
* `print_rendered_document(...)`
* maybe `print_html_via_pdf(...)`

## Label printing

* `print_raw(...)` for ZPL/EPL/TSPL

## Optional device-specific transports

* Epson ePOS support if you want direct-printer parity

The design rule should be:

```text id="jlwmrv"
receipt printers -> image/raster/raw ESC/POS path
office printers -> PDF/document path
label printers -> native label language path
HTML -> authoring format, not final printer format
```

---

# 11) One-glance summary

```text id="n8lpoe"
Odoo IoT printing lives in two main worlds:

Frontend POS printing:
- addons/point_of_sale/static/src/app/utils/printer/base_printer.js
- addons/point_of_sale/static/src/app/utils/printer/hw_printer.js
- addons/point_of_sale/static/src/app/utils/printer/epson_printer.js

IoT / Windows printer drivers:
- odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py
- odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py

Actual flows:
- Receipt: HTML -> canvas -> base64 JPEG -> IoT box -> ESC/POS raster -> printer
- Epson direct: HTML -> canvas -> raster -> ePOS XML -> printer
- Report: PDF -> SumatraPDF / Ghostscript -> office printer
- Raw: bytes -> printer
```

If you want, I can turn this into a polished `ARCHITECTURE.md` for your project.
