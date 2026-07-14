# Odoo print compatibility

Odoo does not send every print job through one universal format. Its POS,
office, label, and direct-printer paths prepare different payloads, and an Inari
integration should preserve those distinctions instead of forcing them through
an untyped “print this” endpoint.

This note records the upstream behavior that informs Inari’s print model. Paths
refer to the Odoo source tree and may move between Odoo releases; verify them
against the version being integrated.

## The four print paths

| Work | Odoo prepares | Device side does |
| --- | --- | --- |
| POS receipt through IoT | Base64 JPEG rendered by the browser | Convert to monochrome and encode as ESC/POS |
| Direct Epson receipt | Epson ePOS XML built from browser raster data | Printer interprets ePOS directly |
| Office report | PDF | Submit through the operating-system print stack |
| Label or native command | Raw printer-language bytes | Send bytes without document rendering |

HTML is an authoring format in the POS flow, not the common device payload.

## POS receipts through the IoT bridge

The POS frontend renders the receipt component to a canvas in:

```text
addons/point_of_sale/static/src/app/utils/printer/base_printer.js
```

`processCanvas` serializes that canvas as a base64 JPEG. The hardware-printer
adapter in:

```text
addons/point_of_sale/static/src/app/utils/printer/hw_printer.js
```

sends it as the `receipt` field of a `print_receipt` action.

The IoT-side base driver decodes the image, converts it to a one-bit raster, and
passes it through the selected receipt protocol:

```text
odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py
```

The relevant formatters include ESC/POS raster and column modes. The same driver
owns paper feed, cut, and cash-drawer pulses. Those are device commands, not
special document types.

```text
receipt HTML
  → browser canvas
  → base64 JPEG
  → IoT print_receipt action
  → monochrome raster
  → ESC/POS bytes
  → receipt printer
```

Inari’s `receipt_image` content kind matches this boundary. It accepts the
rendered image while keeping transport selection and cash-drawer control typed.

## Direct Epson printing

The Epson adapter takes the same browser canvas but converts it to raster data,
wraps that data in Epson ePOS XML, and sends it directly to the printer:

```text
addons/point_of_sale/static/src/app/utils/printer/epson_printer.js
```

That path bypasses the IoT receipt-image adapter. Supporting it in an Odoo
integration means choosing a direct Epson transport explicitly; it should not
change the semantics of ordinary Inari receipt jobs.

## Office documents

The Windows IoT printer driver distinguishes PDF from other bytes in:

```text
odoo/addons/iot_drivers/iot_handlers/drivers/printer_driver.py
```

PDF is written to a temporary document and submitted with SumatraPDF when
available, with Ghostscript as the fallback. Non-PDF bytes take the raw path.

Inari therefore models PDF as its own content kind and leaves the operating
system driver responsible for page setup and printer-specific rendering.
Rendering HTML to PDF, when needed, belongs before that submission boundary.

## Labels and native printer languages

ZPL, ESC/POS, and similar payloads are already printer programs. Odoo passes
non-PDF bytes to the raw spooler path, and Inari exposes the same capability as
`raw` content with an explicit target and transport.

Raw work is privileged: the agent validates size and authorization but cannot
infer whether arbitrary printer-language bytes are physically safe. Controller
policy should grant it more narrowly than ordinary text or document printing.

## Device classification and status

Odoo’s driver distinguishes receipt, label, and office printers because their
test pages and supported actions differ. Inari represents that distinction with
typed capabilities instead of deriving durable behavior from a display name.

Odoo also inspects operating-system print jobs after submission. Inari’s durable
job resource serves the same product need: acceptance into the local queue is
not the same event as successful physical output. Clients should follow job
state or subscribe to live events until the job reaches a terminal outcome.

## Integration guidance

An Odoo adapter should map each upstream path deliberately:

- POS receipt image → `receipt_image`;
- server-rendered report → `pdf`;
- label language or trusted device program → `raw`;
- plain office or receipt text → `text`;
- cash drawer, feed, cut, and test page → typed device commands.

Target a stable Inari `device_id`, include an idempotency key for retryable
business actions, and treat the returned job as the source of execution state.
Do not pass Odoo’s internal action object through as a permanent protocol.

Server-side Odoo integrations call the controller API for managed work. POS and
offline workflows use the local agent API so they remain available during a
controller outage. Both paths converge on the same durable edge runtime and
device identifiers.
