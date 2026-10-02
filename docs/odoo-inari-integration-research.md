# Odoo 19 and Inari integration research

Status: initial fact record

Date: 2026-08-24

Scope: Odoo 19 IoT, POS hardware, printers, scales, and the deployed MZE Odoo
instance.

This note records observed behavior. It does not select an Inari product design.
Claims use these source classes:

- **Odoo source**: public Odoo 19 source code.
- **Odoo docs**: official Odoo 19 documentation.
- **MZE source**: local `mze-infra` files.
- **Live evidence**: read-only inspection of the deployed Odoo instance. The
  inspection has no committed capture file.
- **Unknown**: the cited sources do not expose the fact.

## Findings at a glance

- Odoo has two separate hardware processes in its official documentation: the
  IoT Box and the Windows virtual IoT system. Both connect local hardware to an
  Odoo database. Odoo documents barcode scanners, printers, payment terminals,
  scales, cameras, and measurement tools as IoT use cases. [Odoo IoT overview](https://www.odoo.com/documentation/19.0/applications/general/iot.html)
- The public Odoo 19 repository contains the IoT device process (`iot_drivers`)
  and browser transport helpers (`iot_base`). The database-side IoT models and
  controllers live in the Enterprise repository, which the public source does
  not expose. Odoo's source-install guide says that the Enterprise repository
  contains extra addons and depends on the Community server. [Odoo source install](https://github.com/odoo/documentation/blob/19.0/content/administration/on_premise/source.rst)
- The device process discovers hardware through `Interface` classes, selects a
  `Driver`, and reports the device inventory to the database through
  `/iot/setup`. It receives actions through `/iot_drivers/action` and publishes
  events through `/iot_drivers/event`. [Odoo `iot_drivers` manager](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/main.py#L20-L181)
- The public 19.0 driver process downloads handler code from the configured Odoo
  server through `/iot/get_handlers`. It loads Python driver and interface
  modules at runtime. This is an observed Odoo extension seam. The security and
  lifecycle rules for a third-party handler are not defined by the public code.
  [Odoo handler loader](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/tools/helpers.py#L287-L386)
- The POS IoT receipt path renders the receipt in the browser, converts it to a
  base64 image, sends a `print_receipt` action to the local hardware proxy, and
  waits for a result. The IoT printer driver converts the image to a one-bit
  raster and sends it to CUPS on Linux or the Windows spooler. [POS base printer](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/utils/printer/base_printer.js#L7-L120), [IoT printer base driver](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py#L16-L83)
- The deployed MZE Odoo backend reports build `19.0-20260723`. The live database
  has `point_of_sale` and `iot_base` installed. It does not have `iot`,
  `pos_iot`, `mrp`, or `quality` installed. It has one POS configuration,
  `MIZONA ECOLÓGICA`, with hardware flags disabled and no proxy, preparation
  printer, or Epson printer configured. **Live evidence. No committed capture.**
- The current deployment does not run an Inari service and has no Inari secrets.
  **Live evidence. No committed capture.**
- Community Odoo renders backend PDFs and printer-language text through
  `ir.actions.report`. Stock automatic printing sends these report actions to
  the normal web action service. Community source has no generic database-side
  report-to-IoT binding.
- The current Inari Agent parses PDF and HTML print work but has no configured
  renderer for either format. Those operations fail during execution.
- The MZE cluster has one host and one storage failure domain. Its daily logical
  database dump and OpenBao snapshot have no shared recovery-point identity.
  WAL archiving and off-site backup are disabled.

## 1. Public Odoo architecture

### 1.1 Odoo database process and IoT process are different boundaries

The public `iot_base` addon is a web-client support addon. Its manifest depends
on `web` and places network helpers and the device controller in
`web.assets_backend`. [Odoo `iot_base` manifest](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_base/__manifest__.py#L5-L23)

The public `iot_drivers` addon describes itself as **Hardware Proxy**. Its
manifest says that the module enables the framework and that actual drivers are
in separate modules. It is marked `installable: False`. [Odoo `iot_drivers` manifest](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/__manifest__.py#L3-L27)

The process imports connection management, controllers, driver and interface
registries, event handling, HTTP helpers, WebSocket support, and WebRTC support
at startup. It also adds an Odoo IoT user agent and database header to requests
that go to the configured remote server. [Odoo `iot_drivers` init](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/__init__.py#L7-L41)

The public Community repository does not contain the database-side IoT models,
Enterprise controller implementations, or the `pos_iot` addon. Their exact
fields, access rules, registration controller, and handler API are **unknown**
from the available public source.

### 1.2 Official installation modes

The official IoT overview names two supported systems:

- An IoT Box: a preinstalled microcomputer intended for plug-and-play use.
- Windows virtual IoT: an application on a Windows computer.

Odoo documents that multiple IoT systems can run at the same time. It also
documents that cameras and measurement tools used by MRP are not compatible
with Windows virtual IoT. [Odoo IoT overview](https://www.odoo.com/documentation/19.0/applications/general/iot.html), [Windows virtual IoT](https://www.odoo.com/documentation/19.0/applications/general/iot/windows_iot.html)

Connection uses the IoT app and a local-network discovery step. The browser can
pair by code or token. The pairing code is valid for up to two hours after
power-on. After pairing, Odoo asks the operator to associate the IoT system
with a POS when the system is used by POS. [Odoo IoT connection](https://www.odoo.com/documentation/19.0/applications/general/iot/connect.html)

Odoo documents that an IoT system requests an HTTPS certificate and receives an
`.odoo-iot.com` homepage URL. The IoT form exposes a domain address, image
version, and certificate end date in developer mode. A production database and
an active subscription are required to download the certificate. [Odoo IoT connection](https://www.odoo.com/documentation/19.0/applications/general/iot/connect.html)

Odoo explicitly warns that a Windows virtual IoT system must not be exposed to
the public Internet because it gives access to local-network devices. [Odoo Windows virtual IoT](https://www.odoo.com/documentation/19.0/applications/general/iot/windows_iot.html)

### 1.3 Handler and device lifecycle

The device process has global registries for drivers, interfaces, discovered
IoT devices, and unsupported devices. `Manager._send_all_devices` sends IoT-box
metadata and a device list to the Odoo backend at `/iot/setup`. The payload
includes an IoT-box identifier, MAC address, IP address, token, and version.
Each device includes a name, type, manufacturer, connection, and subtype. The
manager retries the request up to five times. [Odoo IoT manager](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/main.py#L20-L129)

The registration request is a device-process fact. The server-side model,
authorization, persistence, and response contract are **unknown** because the
Enterprise controller is not in the public Community source.

An `Interface` owns discovery for one connection type. It selects driver
classes by `connection_type` and priority. When a device appears, it chooses a
supported driver, registers it in the global device map, and starts the driver.
An interface can also record unsupported devices. [Odoo interface base](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/interface.py#L12-L90)

Drivers self-register through `__init_subclass__`. A driver declares a device
connection type and priority, exposes an `action` map, and keeps a bounded set
of recent action IDs. The generic action method deduplicates a repeated
`action_unique_id`, adds the session ID, and reports success or error. Printer
and payment drivers are excluded from the generic event path. [Odoo driver base](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/driver.py#L13-L80)

The handler loader requests a ZIP from `/iot/get_handlers` with the IoT
identifier and an automatic-update flag. It uses an ETag, accepts `304 Not
Modified`, extracts the archive, deletes old handlers, and imports Python
modules from driver and interface directories. Raspberry Pi and Windows
platform suffixes select different files. [Odoo handler loader](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/tools/helpers.py#L287-L386)

Odoo documents that handler updates can happen automatically on IoT restart and
can also be forced from the IoT homepage. [Odoo IoT updates](https://www.odoo.com/documentation/19.0/applications/general/iot/iot_advanced/updating_iot.html)

## 2. Device transport and server routes

### 2.1 Browser to local IoT system

The `iot_base` HTTP helper posts JSON with a `params` object. It defaults to a
six-second timeout. When local-network access is enabled, it uses HTTP and
sets the browser request target address space to `local`. [Odoo IoT HTTP helper](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_base/static/src/network_utils/http.js#L3-L42)

The `IoTLongpolling` service sends actions to `/iot_drivers/action` and polls
`/iot_drivers/event`. It carries a session UUID, IoT IP, device identifier, and
action data. Polling starts at 1.5 seconds and can back off to 15 seconds. [Odoo IoT long-polling service](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_base/static/src/network_utils/longpolling.js#L6-L30), [action and event request code](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_base/static/src/network_utils/longpolling.js#L88-L237)

`DeviceController` is the browser object for one device. It stores the IoT IP,
device identifier, IoT record ID, and manual-measurement flag. It delegates
actions and listeners to the long-polling service. [Odoo device controller](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_base/static/src/device_controller.js#L6-L39)

### 2.2 Local route behavior

The public driver controller declares device types for display, printer, scanner,
keyboard, camera, generic device, payment, scale, and fiscal data module. The
action route is `/iot_drivers/action`. It uses JSON-RPC and `cors: '*'`.
`iot_route` disables CSRF for the route. The event route is
`/iot_drivers/event` and waits
up to 50 seconds for matching events. [Odoo driver controller](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/controllers/driver.py#L19-L113)

`iot_route` defaults to `auth='none'` and `save_session=False`. It can restrict a
route to Linux. [Odoo IoT route wrapper](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/tools/route.py#L9-L30)

The proxy controller exposes `/hw_proxy/hello` and `/hw_proxy/status_json`.
These routes report local proxy availability and driver status. [Odoo proxy controller](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/controllers/proxy.py#L6-L19)

The source shows an HTTP callback to `/iot/box/<method>` and a WebSocket client
that subscribes to the Odoo `/websocket` endpoint. [Odoo controller callback](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/websocket_client.py#L20-L33), [Odoo IoT WebSocket client](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/websocket_client.py#L40-L201)

This is the transport in the public Odoo 19 branch. It is not an Inari design
decision. The Inari architecture names Zenoh as the managed gateway data plane.
The repository operating rules prohibit a WSS-era stream path without a strong
reason. Any bridge needs a protocol review at this boundary.

## 3. Printers

### 3.1 Odoo IoT printer behavior

Odoo documents USB and network printers. IoT systems auto-detect them and can
take up to two minutes to list a device. A network printer and its IoT system
must share a network. A test from the IoT device form prints a test page.
Epson receipt and Zebra label printers use their built-in handling and do not
select a driver. Printer subtypes are receipt, label, and office. [Odoo IoT printer](https://www.odoo.com/documentation/19.0/applications/general/iot/devices/printer.html)

Odoo can link a printer to work orders and quality-control points. Reports can
be linked from the printer's report tab. The first report print asks the
operator to select a printer. The browser caches the report-to-printer choice.
Resetting linked printers affects the current browser only. [Odoo IoT printer](https://www.odoo.com/documentation/19.0/applications/general/iot/devices/printer.html)

### 3.2 POS printer records and configuration

The public POS model `pos.printer` supports an IoT printer type and a direct
Epson ePOS type. It stores the proxy IP, product categories, company, POS
relation, and Epson IP. [Odoo POS printer model](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/models/pos_printer.py#L28-L81)

The POS configuration includes printer records, cash drawer, electronic scale,
print-via-proxy, scan-via-proxy, automatic receipt, and proxy IP fields. It also
has a separate Epson IP field. [Odoo POS configuration](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/models/pos_config.py#L71-L126), [other device fields](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/models/pos_config.py#L163-L209)

Odoo's POS hardware documentation lists payment terminals, cash drawers,
customer displays, scales, barcode scanners, receipt printers, and electronic
shelf labels. It documents Local Network Access, IoT systems, and certificates
as separate setup areas. [Odoo POS hardware](https://www.odoo.com/documentation/19.0/applications/sales/point_of_sale/pos_hardware.html)

### 3.3 POS ticket print call path

The public POS code provides this call path for an IoT printer:

```text
POS order receipt
  -> PosStore.printReceipt
  -> printer service
  -> BasePrinter.printReceipt
  -> canvas rendering and JPEG encoding
  -> HWPrinter.sendPrintingJob
  -> local /hw_proxy/default_printer_action
  -> IoT printer driver
  -> CUPS or Windows spooler
```

The POS store creates `HWPrinter` from a printer proxy URL when the printer is
not an Epson printer. It calls the printer service for the receipt and increments
the order print count only after a truthy result. [Odoo POS store printer creation](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/services/pos_store.js#L450-L459), [Odoo POS store print flow](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/services/pos_store.js#L1307-L1313), [Odoo POS store receipt result](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/services/pos_store.js#L1915-L1951)

`BasePrinter` queues receipts per printer. It renders HTML to a canvas,
serializes the canvas, sends the job, and maps connection, action, and result
errors to user-facing error objects. It exposes retry and download information.
[Odoo POS base printer](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/utils/printer/base_printer.js#L7-L120)

`HWPrinter` posts `print_receipt` and cashbox actions to
`/hw_proxy/default_printer_action`. [Odoo POS hardware printer](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/utils/printer/hw_printer.js#L7-L35)

The POS printer service displays a retry popup after a failed print. The popup
offers retry and download paths. [Odoo POS printer service](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/services/pos_printer_service.js#L8-L72)

The IoT printer base driver decodes the receipt image. It converts the image to
grayscale and one-bit pixels.

It formats ESC/POS raster or column data. It also maps cashbox, receipt, status,
and default-print actions. [Odoo printer base driver](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py#L16-L101), [printer actions and job status](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/printer_driver_base.py#L198-L237)

The Linux printer driver submits jobs to CUPS and tracks the job ID. It reports
completion, unreachable printers, unknown status, and a 30-second timeout. Its
controller exposes `/hw_proxy/default_printer_action`. [Odoo Linux printer driver](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/printer_driver_L.py#L20-L67), [Linux printer status and proxy route](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/printer_driver_L.py#L204-L255)

The Windows printer driver sends raw bytes to the Win32 spooler. It can submit
PDF reports through SumatraPDF or Ghostscript. [Odoo Windows printer driver](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/printer_driver_W.py#L21-L149)

The Linux printer interface discovers CUPS printers, supports USB and network
devices, filters unsupported models, and requires three missed discovery checks
before disconnecting a device. It normalizes device identifiers by removing
separator characters and truncating the result to 127 characters. [Odoo Linux printer interface](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/interfaces/printer_interface_L.py#L23-L118)

The Windows printer interface enumerates local Windows printers and excludes
`PORTPROMPT` virtual printers. [Odoo Windows printer interface](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/interfaces/printer_interface_W.py#L11-L46)

### 3.4 POS printer alternatives

Odoo documents three POS receipt paths: network Epson ePOS without IoT, Epson
USB through IoT, and ESC/POS USB or network through IoT. Bluetooth is not
supported in that documented setup. [Odoo POS Epson printers](https://www.odoo.com/documentation/19.0/applications/sales/point_of_sale/configuration/epos_printers.html)

The direct Epson path uses local-network access and can require a self-signed
certificate. A third-party device bridge must preserve the distinction between
the Epson path and the IoT proxy path.

### 3.5 Odoo backend report and label paths

`ir.actions.report` supports `qweb-html`, `qweb-pdf`, and `qweb-text`.
`_render_qweb_pdf` produces PDF bytes, and `_render_qweb_text` produces text
bytes. The browser report controller uses these methods for report downloads.
[Odoo report model](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/odoo/addons/base/models/ir_actions_report.py),
[Odoo report controller](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/web/controllers/report.py)

The POS report service loads an `ir.actions.report` and downloads its PDF. It
does not submit the report to a printer. The POS browser fallback uses
`window.print()`.
[POS report service](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/services/report_service.js),
[POS browser printer](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/services/printer_service.js)

Stock automatic printing creates report actions for delivery documents,
reception documents, product labels, lot labels, and package labels. Its web
client action runs those report actions in order. Odoo stock uses `qweb-text`
for ZPL labels and `qweb-pdf` for other labels and reports.
[Stock automatic report actions](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/models/stock_picking.py),
[Stock multi-print action](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/static/src/client_actions/multi_print.js),
[Stock report actions](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/report/stock_report_views.xml)

The Community source contains no generic database-side IoT report-binding
model. The browser report download route also requires Odoo user
authentication. A managed printer Device Adapter must receive Odoo-rendered bytes. It
must not ask the Agent to fetch that browser route.

Odoo contextual actions support `binding_model_id`, `binding_type`, groups,
view types, and client-action parameters. A bound client action appears in the
standard form and list action menus after Odoo checks access.
[Odoo action bindings](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/odoo/addons/base/models/ir_actions.py),
[Odoo action menus](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/web/static/src/search/action_menus/action_menus.js)

The web action Module provides an `ir.actions.report handlers` registry. Each
registered handler can handle one marked report action or return control to the
normal report download Implementation.
[Odoo report-handler seam](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/web/static/src/webclient/actions/action_service.js),
[Odoo report-handler tests](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/web/static/tests/webclient/actions/report_action.test.js)

Stock completes `_action_done()` before it creates automatic report actions.
The `do_multi_print` client action runs each non-HTML report in sequence. A
rejected action stops the remaining sequence.
[Stock validation](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/models/stock_picking.py),
[Stock multi-print action](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/static/src/client_actions/multi_print.js)

POS synchronizes the paid order before it runs automatic stock reports. A
later RPC error can reset the local order to draft.
[POS payment validation](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/utils/order_payment_validation.js)

Package labels use a second automatic seam in
`stock.move.line._post_put_in_pack_hook()`. An integration that changes only
`do_multi_print` misses this path and the direct POS report loop.
[Stock package-label seam](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/models/stock_move_line.py)

## 4. Scales

Odoo documents USB and serial-to-USB scales. A compatible scale is
auto-detected. Missing devices can require a reboot or driver update. Legal certification is
required for an integrated scale in EU member states. [Odoo IoT scale](https://www.odoo.com/documentation/19.0/applications/general/iot/devices/scale.html)

The public serial interface enumerates serial ports and allows unsupported
devices. [Odoo serial interface](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/interfaces/serial_interface.py#L13-L30)

The serial driver opens a configured protocol, locks access, exposes status and
name, and emits measurements. [Odoo serial driver base](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/serial_base_driver.py#L17-L160)

The public scale driver supports `read_once`, `start`, and `stop`. It reports
weight changes and errors through events. A compatibility `/hw_proxy/scale_read`
route returns the active scale weight. [Odoo serial scale driver](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/serial_scale_driver.py#L17-L86), [scale read and event logic](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/iot_handlers/drivers/serial_scale_driver.py#L88-L193)

The public POS scale service uses the hardware proxy and polls `scale_read` in
continuous mode. It supports manual mode, tare, validity checks, and explicit
disconnected-proxy or disconnected-scale errors. [Odoo POS scale service](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/screens/scale_screen/scale_service.js#L9-L70), [POS scale validation and errors](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/screens/scale_screen/scale_service.js#L94-L171)

The scale screen displays gross, tare, net, unit, and total weight. It disables
order confirmation until the reading is valid. It exposes Tare and, in manual
mode, Get Weight. [Odoo POS scale screen](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/point_of_sale/static/src/app/screens/scale_screen/scale_screen.xml#L3-L50)

The exact Enterprise `pos_iot` scale integration is **unknown** from public
source. The public POS scale service is the available Community seam.

## 5. Security and transport constraints

- Odoo's local IoT action and event routes use unauthenticated route defaults,
  permissive CORS, and disabled CSRF in the public driver process. [Odoo driver controller](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/controllers/driver.py#L24-L87), [Odoo route wrapper](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/tools/route.py#L9-L30)
- Odoo's Windows virtual IoT documentation says not to expose the local service
  to the public Internet. [Odoo Windows virtual IoT](https://www.odoo.com/documentation/19.0/applications/general/iot/windows_iot.html)
- IoT certificate provisioning uses Odoo Enterprise endpoints and stores a
  certificate and key for the local HTTPS server. The device reports certificate
  status back to `/iot/box/update_certificate_status`. [Odoo IoT certificate code](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/iot_drivers/tools/certificate.py#L23-L153)
- The public driver process currently starts an HTTP callback and a WebSocket
  client. This differs from Inari's managed Zenoh data-plane architecture. The
  distinction requires an explicit protocol boundary review.
- The Odoo docs say the HTTPS certificate authenticates and encrypts IoT
  communication and is needed for payment terminals. [Odoo IoT connection](https://www.odoo.com/documentation/19.0/applications/general/iot/connect.html)

The first two facts are direct constraints for any third-party gateway. The
public source does not define a secure third-party handler contract, device
credential rotation contract, or remote authorization model. Those are
**unknowns**.

### 5.1 Inari-side integration facts

- The local agent API has `POST /print-jobs` and `POST /device-commands`, but
  their request models contain content, target, options, and free-form metadata.
  They have no dedicated idempotency key or idempotency header handling. The
  controller API has a separate required `Idempotency-Key` extractor for job
  creation. This difference is an observed contract gap. [Local print schemas](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/local_api/schemas/jobs.py#L183-L201), [local job routes](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/local_api/routes.py#L438-L463), [controller idempotency](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/crates/inari-server/src/http/routes/api/inari.rs#L154-L168), [controller key extractor](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/crates/inari-server/src/http/routes/api/extract.rs#L60-L94)
- The architecture places Odoo or another POS on local HTTP to the Agent. The
  local path enters a durable job and remains available when the Controller is
  offline. A browser POS therefore needs a local Agent path for offline work.
  The managed Controller path alone does not provide that behavior. [Inari architecture](../ARCHITECTURE.md#system-shape), [local device work](../ARCHITECTURE.md#local-device-work)
- Loopback address checks do not equal client pairing. In managed mode,
  `StandaloneTrustService.pairing_required` is false by definition, and token
  requests can receive a `LOOPBACK` trust grant without an attestation. The
  local token route still checks loopback, but this does not identify the Odoo
  browser or bind it to a trusted origin. A managed browser integration needs a
  stronger pairing contract before it can receive local job scopes. [Loopback policy](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/security/policies.py#L76-L99), [pairing mode and grant](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/security/local_trust/service.py#L123-L128), [managed-mode token grant](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/security/local_trust/service.py#L266-L315)
- The public Controller API extracts a session `Principal` for fleet and job
  routes. The enrollment route accepts an optional bearer token, but the public
  API has no workload or Odoo service-principal extractor. A server-side Odoo
  connector therefore has no documented workload-auth path in the current
  Controller source. [Controller routes](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/crates/inari-server/src/http/routes/api/inari.rs#L24-L36), [job authorization](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/crates/inari-server/src/http/routes/api/inari.rs#L154-L185), [session principal](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/crates/inari-server/src/identity/principal.rs#L17-L72)
- Inari's public device enum includes printers, scanners, scales, and displays,
  but the current Python driver provider builds printer drivers only and the
  discovery coordinator yields printers only. The enum is broader than the
  current runtime coverage. [Device enum](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/contracts/local-agent.openapi.json#L284-L292), [printer-only provider](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/di/drivers.py#L20-L44), [printer-only discovery](https://github.com/hadronomy/inari/blob/7986bede134726acff97d470f8b71ce7eaba1d38/packages/agent/inari/runtime/devices/discovery.py#L67-L92)

### 5.2 Current print-media execution gaps

The local print schema accepts structured receipts, receipt images, text,
HTML, PDF, and raw content. The service has no injected PDF or HTML renderer.
It returns `PDF_RENDERER_NOT_CONFIGURED` or the matching HTML error when those
paths execute.
`../packages/agent/inari/local_api/schemas/jobs.py`,
`../packages/agent/inari/printing/service.py`,
`../packages/agent/inari/di/drivers.py`

`JobService.enqueue_print()` serializes the full request into the SQLite
`jobs.request_json` text column. The current schema has no artifact reference,
byte reservation, or Device Spool table.
`../packages/agent/inari/runtime/jobs/service.py`,
`../packages/agent/inari/runtime/jobs/operations.py`,
`../packages/agent/inari/db/schema.py`

`BinaryContentInput.base64` has no input-size constraint. The Agent decodes the
complete Base64 value before media checks.
`../packages/agent/inari/local_api/schemas/jobs.py`,
`../packages/agent/inari/printing/payloads.py`

The per-Device execution queue uses `asyncio.Queue()` without `maxsize`.
Storage configuration has no Device Spool path or quota fields.
`../packages/agent/inari/runtime/jobs/execution.py`,
`../packages/agent/inari/config.py`

The current image renderer accepts Pillow image objects, including PNG. It does
not enforce the intended JPEG-only contract, 2 MiB input limit, or decoded
pixel limit.
`../packages/agent/inari/printing/renderers/image_escpos_renderer.py`

The Windows Driver sends bytes through the raw spooler. The Linux Driver uses a
temporary file and `lp`. The raw-socket Driver sends bytes unchanged. These
Implementations do not provide a tested PDF execution contract or a declared
label-language contract.
`../packages/agent/inari/printing/drivers/windows.py`,
`../packages/agent/inari/printing/drivers/cups.py`,
`../packages/agent/inari/printing/drivers/socket.py`

Microsoft states that `WritePrinter` raw data must describe the printer-native
format. Windows does not render PDF bytes that enter this path.
[Microsoft `WritePrinter`](https://learn.microsoft.com/en-us/windows/win32/printdocs/writeprinter),
[Microsoft print data types](https://learn.microsoft.com/en-us/windows/win32/printdocs/doc-info-1)

PDFium has a permissive license and a public rendering Interface. Its build can
disable V8 JavaScript and XFA. PDFium tells embedders to use only public
headers.
[PDFium license](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE),
[PDFium public Interface](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/public/fpdfview.h),
[PDFium build notes](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/README.md)

Windows GDI `StartDoc` returns the print job identity for the submitted
document. This Interface fits a PDFium bitmap path better than raw
`WritePrinter`.
[Microsoft `StartDoc`](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-startdoca)

CUPS accepts an explicit `application/pdf` document format. Its destination
Interface also exposes supported formats, media, resolution, job identity,
status, and cancellation.
[CUPS job submission](https://openprinting.github.io/cups/doc/cupspm.html#submitting-a-print-job),
[CUPS destination capabilities](https://openprinting.github.io/cups/doc/cupspm.html#detailed-destination-information),
[IPP job states](https://www.rfc-editor.org/rfc/rfc8011.html#section-5.3.7)

`qpdf --check` distinguishes clean input, warnings, and errors. It also exposes
encryption state and structured object data without acting as a renderer.
[qpdf checks and exit codes](https://qpdf.readthedocs.io/en/stable/cli.html#exit-status),
[qpdf JSON inspection](https://qpdf.readthedocs.io/en/stable/json.html)

Windows generic queue states do not prove physical output. Microsoft states
that `JOB_STATUS_COMPLETE` can precede printing. An unqualified
`JOB_STATUS_PRINTED` can occur at submission.
[Microsoft Windows job state](https://learn.microsoft.com/en-us/windows/win32/printdocs/job-info-1),
[Microsoft print-monitor controls](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/winsplp/ns-winsplp-_monitor)

IPP `completed` means that the Printer completed processing. A forwarding
Printer can report `queued-in-device` when it cannot observe later device
state. Cancellation during processing does not prove zero output.
[IPP job processing](https://www.rfc-editor.org/rfc/rfc8011.html#section-5.3.7.1),
[IPP cancellation](https://www.rfc-editor.org/rfc/rfc8011.html#section-4.3.3)

### 5.3 Current managed-security gaps

The deployed Zenoh ACL authorizes one TLS subject for the complete
`iot/v1/agents/**` keyspace. It does not match an Agent certificate identity.
`../deploy/helm/inari/templates/zenoh-configmap.yaml`,
`../deploy/helm/inari/values.yaml`

Zenoh supports certificate-common-name subjects. A router can bind one
certificate identity to one Agent keyspace.
[Zenoh access control](https://zenoh.io/docs/manual/access-control/)

The managed protocol contains `command_id` and sequence fields. The Agent
deduplicates `command_id`, but the live path does not persist the highest
accepted sequence.
`gateway_protocol.md`,
`../packages/agent/inari/gateway/bridges/runtime.py`,
`../packages/agent/inari/gateway/repositories.py`

RFC 9449 binds a DPoP proof to the method, URI, issue time, access-token hash,
public-key thumbprint, nonce, and proof identity.
[RFC 9449](https://www.rfc-editor.org/rfc/rfc9449)

RFC 9180 defines recipient-bound application encryption. HPKE can bind tenant
and work fields as authenticated data.
[RFC 9180](https://www.rfc-editor.org/rfc/rfc9180)

The Controller chart sets `server.maxBodySizeBytes` to 1 MiB. This value is
less than the accepted 10 MiB Report PDF limit.
`../deploy/helm/inari/values.yaml`

### 5.4 Accepted target corrections

This section records design targets. It does not describe the current runtime.

- All Report PDF and Label Document work uses Managed Device Work. One secure
  Odoo backend method validates and renders marked reports. Automatic stock
  sequences group records by Site, continue after handled Device failures, and
  show one summary.
- Print Intent identity uses a discriminated POS or report Print Origin and one
  transactionally allocated copy ordinal. A Retry never requests another copy.
- Odoo stores logical Device Bindings and separate immutable Binding Revision
  records. Environmental failure keeps the prior passed result.
- Managed Work derives its deadline in the Controller, keeps a unique
  `(Organization, Idempotency Key)` pair for 90 days, and serializes acceptance,
  expiry, and cancellation on one row.
- The Agent keeps `accepted` through preparation and sets `in_progress` at the
  first Device I/O marker. Later signed evidence can improve the current
  projection without erasing history.
- The Agent grants a 10-second Transport Leader Lease. The holder renews every
  three seconds. IndexedDB coordinates candidates, and BroadcastChannel carries
  hints only.
- The Device Spool limits each Device to 128 MiB and the Agent to 512 MiB. Both
  limits count artifacts and reservations. Acceptance uses file and directory
  sync before a FULL-durability SQLite commit.
- Production Agent Hosts use encrypted storage and a TPM 2.0 Rollback Anchor.
  Operating-system print spools remain a plaintext exposure boundary.
- PDF policy runs before `Accepted`. Contract Major 1 permits embedded fonts
  only. qpdf, PDFium, and the Windows Platform Backend have separate memory
  budgets.
- The ZPL contract covers six registered Community 19 actions. It pins exact
  media, firmware, language, font, sensing, and device-configuration profiles.
- Managed dispatch uses RFC 9180 HPKE Base mode with X25519, HKDF-SHA256, and
  AES-256-GCM. Signing keys remain separate by trust boundary.
- Pairing binds the browser key, target Agent, business scope, and one-time
  identity. `cnf.jkt` belongs in the access token.
- Zenoh identity uses one exact Agent URI SAN and one ACL subject. The Agent
  atomically fences every command by Dispatch Epoch and sequence.
- Controller recovery serializes acceptance against one Admission Epoch row.
  It uses signed five-minute WAL checkpoints, daily Recovery Points, aligned
  OpenBao snapshots, Cloud KMS, and locked EU dual-region Cloud Storage.
- Managed production uses three Controller replicas and three Zenoh routers in
  three failure domains with `minAvailable: 2`.
- Community 19 is the first Conformance Target. Enterprise IoT and Inari cannot
  coexist. A future licensed target still uses guarded migration and
  Decommission between integrations.
- Marked work observes Agent acceptance for 10 seconds. Pending work remains a
  handled result and enters persistent recovery without browser fallback.
- POS seams carry immutable Submission Context through receipt and preparation
  queues. Inari owns retry UX, partial preparation state, and counter repair.
- Managed reports use deterministic Site Resolution, server-generated markers,
  one-Intent-per-copy rules, and durable sequence summaries.
- Physical support requires an exact Hardware Certification Matrix Row. Scale,
  printer geometry, canary evidence, and Release Set identity bind to that row.
- Recovery uses a 60-second fence, Site-specific canaries, `eur4` storage, and
  three-failure-domain Controller, Zenoh, OpenBao, and PostgreSQL topology.
- Signed Device Center packages enter the Release Set. Durable Decommission
  Runs revoke authority, purge reachable spools, and guard addon removal.

## 6. Deployed MZE Odoo state

### 6.1 Repository and image layout

MZE infrastructure is a single-node Rocky Linux stack built around K3s, Flux,
Kustomize, Cilium Gateway API, cert-manager, Cloudflare Tunnel, OpenBao,
CloudNativePG, and Odoo. Flux owns continuous delivery after bootstrap.
`mze-infra/README.md:1-25`

The Odoo configuration adds `/mnt/extra-addons` to `addons_path`, enables proxy
mode, and exposes HTTP port 8069 and gevent port 8072.
`mze-infra/kubernetes/services/odoo/resources.yaml:42-62`

The deployment pulls a content-addressed OCI addon artifact into
`/mnt/extra-addons`. The Odoo init and main containers use the pinned
`odoo:19.0` image digest. The addon mount is read-only in the main container.
`mze-infra/kubernetes/services/odoo/resources.yaml:158-205`,
`mze-infra/kubernetes/services/odoo/resources.yaml:315-332`,
`mze-infra/kubernetes/services/odoo/resources.yaml:413-457`

The deployment routes `/websocket/` to port 8072 and other Odoo requests to
8069. `odoo.eden.mizonaecologica.es` is a private hostname.
`mze-infra/kubernetes/services/odoo/resources.yaml:472-515`

The artifact script computes a SHA-256 tag from every file under
`odoo/addons`. It writes that tag into both Kubernetes manifests.

The script pushes the addon tree as an OCI artifact. It also explains the
asset-mtime restamp because Flux normalizes artifact mtimes.
`mze-infra/scripts/odoo-addons-artifact.sh:4-34`,
`mze-infra/scripts/odoo-addons-artifact.sh:77-128`

### 6.2 Existing custom addon conventions

`mze_product_review` declares POS assets in `point_of_sale._assets_pos` and
backend assets in `web.assets_backend`. It depends on `point_of_sale` and uses
Odoo 19 manifest metadata.
`mze-infra/odoo/addons/mze_product_review/__manifest__.py:26-50`

Its POS JavaScript uses the documented Odoo `patch` utility and patches
`PosStore.prototype`. Its templates extend `point_of_sale.ProductCard` and
`point_of_sale.Orderline` through QWeb extension templates.
`mze-infra/odoo/addons/mze_product_review/static/src/pos_review.js:1-41`,
`mze-infra/odoo/addons/mze_product_review/static/src/pos_review.xml:1-40`

The local addon validation script is hard-coded to
`mze_customer_branding`: its `ADDON`, manifest path, path resolver, and
promotion checks all target that directory.
`mze-infra/scripts/validate-odoo-addon.py:11-16`,
`mze-infra/scripts/validate-odoo-addon.py:47-85`

The Odoo init container update list is hard-coded to
`mze_customer_branding,mze_medusa_bridge`.
`mze-infra/kubernetes/services/odoo/resources.yaml:213-228`

These hard-coded paths are current deployment facts. The accepted release work
generalizes validation and update lists for pinned MZE and Inari addon
artifacts. The signed Release Set records both digests.

### 6.3 Live database evidence

Read-only JSON-RPC inspection of the deployed Odoo instance reports:

- server build `19.0-20260723`
- installed `point_of_sale` version `19.0.1.0`
- installed `iot_base` version `19.0.1.0`
- no installed `iot`, `pos_iot`, `mrp`, or `quality` modules
- one POS configuration named `MIZONA ECOLÓGICA`
- hardware flags disabled, no `proxy_ip`, no preparation printers, and no
  Epson printer
- no Inari service and no Inari secrets in the live cluster

Source: live read-only inspection supplied on 2026-08-24. No local capture file
exists in this repository. Treat this as deployment evidence, not as a
reproducible fixture.

The current installation therefore exposes the public Community POS and
`iot_base` surfaces. Enterprise IoT behavior is not deployed in this database.

### 6.4 Failure domains and recovery state

The MZE inventory defines one host named `shadow`. K3s, local storage,
PostgreSQL, OpenBao, Odoo, Envoy Gateway, and Cloudflare Tunnel therefore share
one host failure domain.
`mze-infra/ansible/inventory/hosts.yml`,
`mze-infra/config/inventory.yaml`

Odoo uses one CloudNativePG instance on a `local-path` volume. Its cluster has
no WAL archive, base-backup schedule, or point-in-time recovery configuration.
The host backup script creates one daily custom-format `pg_dump` and retains 14
days. Off-site backup is disabled.
`mze-infra/kubernetes/services/odoo/resources.yaml`,
`mze-infra/ansible/roles/infra_host/templates/mze-infra-backup.sh.j2`

OpenBao uses one Raft replica on `local-path` storage and a static seal key from
a Kubernetes Secret. MZE enables KV v2 but does not enable Transit. The backup
script takes an OpenBao snapshot separately from the PostgreSQL dump.
`mze-infra/kubernetes/platform/openbao/values.yaml`,
`mze-infra/kubernetes/platform/openbao-resources/resources.yaml`

OpenBao Transit does not store application payloads. The caller stores the
payload ciphertext and wrapped data key. Deleting both values from live
PostgreSQL removes live access. A matching older PostgreSQL backup and OpenBao
snapshot can recover both sides before backup expiry.
[OpenBao Transit](https://openbao.org/docs/secrets/transit/),
[OpenBao data-key API](https://openbao.org/docs/next/api/secret/transit/#generate-data-key),
[OpenBao Raft snapshots](https://openbao.org/docs/next/commands/operator/raft/)

The current backup script creates the OpenBao snapshot and PostgreSQL dump at
different times. It has no dispatch fence, shared recovery identity, final LSN,
or payload-decryption test.
`mze-infra/ansible/roles/infra_host/templates/mze-infra-backup.sh.j2`

The script writes raw snapshots and dumps to local storage before optional
upload. It can finish without a PostgreSQL dump when no database Pod exists.
`mze-infra/ansible/roles/infra_host/templates/mze-infra-backup.sh.j2`

PostgreSQL logical dumps do not provide continuous archiving or point-in-time
recovery. Those functions require physical base backups and a continuous WAL
archive.
[PostgreSQL continuous archiving](https://www.postgresql.org/docs/18/continuous-archiving.html)

CloudNativePG restores a backup into a new cluster. A restored Controller can
therefore remain isolated while it checks its database, OpenBao state, and
Agent projections.
[CloudNativePG recovery](https://cloudnative-pg.io/docs/1.26/recovery/)

## 7. Extension seam inventory

This inventory names observed boundaries. It does not rank them or select one.

| Boundary | Observed source | Fact and open point |
| --- | --- | --- |
| IoT device runtime handler | `Driver`, `Interface`, dynamic handler loader | Public IoT process can load driver and interface Python handlers. The handler package contract and signing policy are unknown. |
| IoT device registration | `Manager._send_all_devices` → `/iot/setup` | Device metadata is posted to the Odoo backend. Enterprise persistence and authorization are unknown. |
| Browser IoT transport | `iot_base` long-polling service | Browser sends local action requests and waits for events. Routes use the local IoT process, not a public controller API. |
| POS printer model | `pos.printer`, `pos.config` | Public POS records identify IoT, Epson, proxy, category, and company data. |
| POS printer runtime | `PrinterService`, `BasePrinter`, `HWPrinter` | Receipt rendering, queueing, result mapping, retry, and download behavior are public. |
| POS scale runtime | `PosScaleService`, `ScaleScreen` | Public POS service uses hardware-proxy reads, tare, validation, and error states. |
| POS frontend extension | Odoo `patch`, registries, asset bundles | Odoo documents registries as preferred extension points and `patch` for cases that need it. [Patching code](https://www.odoo.com/documentation/19.0/developer/reference/frontend/patching_code.html), [assets](https://www.odoo.com/documentation/19.0/developer/reference/frontend/assets.html) |
| Odoo HTTP controller | `@route` | Odoo documents route inheritance, auth, CSRF, CORS, request type, and session behavior. [Web controllers](https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html) |
| Module declaration | `__manifest__.py` | Odoo requires a manifest dictionary and supports dependencies, data, assets, installability, and license metadata. [Module manifests](https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html) |

## 8. Verification and release gates

The current GitHub workflow installs the pinned mise toolchain and runs
`just validate`. It does not install Odoo Python, load an Odoo database, run
HOOT, or run POS browser tours.
`mze-infra/.github/workflows/validate.yml:17-46`

`just validate` runs XML and manifest-path checks, artifact-tag checks, Ansible
syntax checks, Kustomize and Helm validation, policy checks, and optional tool
checks. It does not run Odoo ORM tests, POS JavaScript tests, HOOT, or browser
tours.
`mze-infra/scripts/validate.sh:15-41`,
`mze-infra/scripts/validate.sh:43-199`

The accepted implementation requires these checks:

- protocol contract fixtures for device inventory, print actions, results,
  events, scale readings, and error codes
- Odoo ORM tests for device configuration, permissions, idempotency, and
  upgrade behavior
- HOOT tests for POS service and printer state transitions
- POS browser tours for first setup, successful ticket print, offline state,
  retry, download fallback, duplicate click, and receipt reprint
- a fake Inari agent that models handshake, device discovery, job acceptance,
  completion, disconnect, timeout, and retry without hardware
- a physical canary with a receipt printer and scale for USB, network,
  reconnect, power loss, paper-out, invalid weight, and recovery flows
- idempotent retry tests that cover browser retries, Odoo retries, agent retries,
  and duplicate action IDs
- service-worker and asset-cache tests after an addon OCI release
- immutable OCI artifact rollback, including database migration and asset
  bundle behavior

These are accepted test and release gates. They are not evidence that the
current repository already supports them.

## 9. Unknowns that need Enterprise or runtime access

1. The Enterprise `iot` database models, ACLs, routes, device form fields, and
   registration response contract.
2. The Enterprise `pos_iot` module's exact POS device loading, device selection,
   and scale integration code.
3. The Enterprise implementation of `/iot/setup`, `/iot/get_handlers`, and
   `/iot/box/*`.
4. The supported Enterprise handler packaging, signing, update, rollback, and
   compatibility policy.
5. The exact relation between `iot.device` records, `pos.printer` records, POS
   configuration, printer reports, and browser-local printer mappings.
6. The Enterprise behavior when an IoT device is offline during POS startup,
   order payment, receipt reprint, or order-change printing.
7. The production certificate, token, and identity lifecycle for a third-party
   secure gateway.
8. Whether the local Inari agent can run on the same machine and network role as
   an IoT Box or Windows virtual IoT system.

The first seven items require Enterprise source or a running Enterprise IoT
database. The last item requires an integration test environment.

## 10. Label Document research

The [ZPL security research](./odoo-inari-zpl-security-research.md) records the
approved Community 19 reports, command allowlist, parser rules, media profile,
injection risk, Output Evidence, and physical gate.

## Primary source index

- [Odoo 19 IoT overview](https://www.odoo.com/documentation/19.0/applications/general/iot.html)
- [Odoo 19 IoT connection](https://www.odoo.com/documentation/19.0/applications/general/iot/connect.html)
- [Odoo 19 IoT printer](https://www.odoo.com/documentation/19.0/applications/general/iot/devices/printer.html)
- [Odoo 19 IoT scale](https://www.odoo.com/documentation/19.0/applications/general/iot/devices/scale.html)
- [Odoo 19 Windows virtual IoT](https://www.odoo.com/documentation/19.0/applications/general/iot/windows_iot.html)
- [Odoo 19 IoT updates](https://www.odoo.com/documentation/19.0/applications/general/iot/iot_advanced/updating_iot.html)
- [Odoo 19 POS IoT](https://www.odoo.com/documentation/19.0/applications/sales/point_of_sale/configuration/pos_iot.html)
- [Odoo 19 POS Epson printers](https://www.odoo.com/documentation/19.0/applications/sales/point_of_sale/configuration/epos_printers.html)
- [Odoo 19 POS hardware](https://www.odoo.com/documentation/19.0/applications/sales/point_of_sale/pos_hardware.html)
- [Odoo 19 public source](https://github.com/odoo/odoo/tree/19.0)
- [Odoo 19 source installation](https://github.com/odoo/documentation/blob/19.0/content/administration/on_premise/source.rst)
- [Odoo 19 backend controllers](https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html)
- [Odoo 19 frontend patching](https://www.odoo.com/documentation/19.0/developer/reference/frontend/patching_code.html)
- [Odoo 19 frontend assets](https://www.odoo.com/documentation/19.0/developer/reference/frontend/assets.html)
- [Odoo 19 module manifests](https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html)
- [Odoo 19 upgrade scripts](https://www.odoo.com/documentation/19.0/developer/reference/upgrades/upgrade_scripts.html)
- [RFC 9449 DPoP](https://www.rfc-editor.org/rfc/rfc9449)
- [RFC 9180 HPKE](https://www.rfc-editor.org/rfc/rfc9180)
- [Zenoh access control](https://zenoh.io/docs/manual/access-control/)
