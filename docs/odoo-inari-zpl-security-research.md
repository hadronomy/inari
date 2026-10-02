# ZPL Label Document security research

Date: 2026-08-25

Scope: Odoo Community 19 stock labels and the first Inari `label_document`
contract.

## Decision summary

Odoo Community 19 stock has a real ZPL target. Its standard stock label reports
use `qweb-text` and produce ZPL for product labels, lot and serial labels,
package labels, packaging barcodes, and operation type labels. The source uses
one or more `^XA ... ^XZ` label formats. It uses text fields, Code 128, and
Data Matrix. It does not need stored formats, printer configuration, network
commands, firmware commands, RFID commands, or ZPL copies.

The first Inari release can support these reports with a typed ZPL
`Label Document`. It must not expose a general raw-printer operation. The
release needs all of the following:

1. An approved Report Binding for an Odoo report.
2. A strict structural parser with an allowlist.
3. A fixed layout and DPI profile.
4. Device Capability Negotiation and a passed physical Device Test.
5. Platform Job Identity and bounded Output Evidence.

ZPL-only does not cover arbitrary custom `qweb-text` reports, EPL, TSPL, CPCL,
RFID programming, printer configuration, stored formats, or firmware work.
Those paths remain outside the first Label Document contract.

## Odoo Community 19 findings

The stock report declarations register six `qweb-text` report actions:

- `stock.label_product_product`
- `stock.label_lot_template`
- `stock.label_package_template`
- `stock.label_package_history_template`
- `stock.label_packaging_barcode`
- `stock.label_picking_type`.

The transfer ZPL view has no registered report action. It is not in the first
contract. The same declaration file keeps PDF reports as `qweb-pdf`, so the
addon can distinguish a ZPL Label Document from a Report PDF at the report
boundary.

Source: [Odoo 19 stock report declarations](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/report/stock_report_views.xml)

The standard product templates emit `^XA^CI28`, text fields, optional Code 128
barcodes, optional prices, and `^XZ`. They support normal, alternative, small,
and jewelry layouts. The standard lot template emits text, Code 128, or Data
Matrix and closes each label with `^XZ`.

Source: [Odoo 19 product and lot ZPL templates](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/report/product_templates.xml)

The transfer, operation type, package, and packaging templates use the same
envelope and field model. Their variable values include product names, lot
names, package names, dates, quantities, and barcodes.

Sources: [Odoo 19 transfer and operation type templates](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/report/picking_templates.xml),
[Odoo 19 package templates](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/report/package_templates.xml),
[Odoo 19 packaging barcode template](https://github.com/odoo/odoo/blob/d1e35dc9e67071561abaa907cc5a27dbe3e264e6/addons/stock/report/packaging_barcode.xml)

Odoo documents Zebra labels as designed for a four by six inch label. Odoo
also warns that ZPL is sensitive to format errors and that custom ZPL views
need maintenance across upgrades.

Sources: [Odoo 19 Zebra label setup](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/shipping_receiving/setup_configuration/zebra.html),
[Odoo 19 printer troubleshooting](https://www.odoo.com/documentation/19.0/applications/general/iot/devices/printer.html)

### Odoo data safety issue

QWeb `t-esc` protects XML output. It does not make a value safe for the ZPL
command language. A product name or barcode that contains `^`, `~`, a control
byte, or a delimiter can change the printer program when placed inside `^FD`.
The current upstream templates do not encode every variable field with `^FH`.

The addon must therefore qualify a report template before it creates a Report
Binding. A post-render parser cannot safely trust an unknown report.

The parser cannot identify whether a command-looking sequence came from a
static template or business value.

The safe producer choices are:

- harden the approved report views so every variable field uses an explicit
  ZPL field-data escape helper, or
- reject a rendered document when a field contains an unescaped ZPL control
  byte.

The first release must not silently remove, replace, or truncate a business
value. A rejected value produces a clear `Label data needs correction` error
before the Agent accepts work.

## ZPL command risk model

ZPL is an executable printer language. Zebra documents stored formats, object
downloads, memory deletion, network settings, printer resets, and
configuration changes.

The same programming guide also documents RFID and Set-Get-Do commands with
ordinary label-layout commands.

Sources: [Zebra ZPL command index](https://docs.zebra.com/us/en/printers/software/zpl-pg/c-zpl-zpl-commands.html),
[Zebra ZPL introduction](https://docs.zebra.com/us/en/printers/software/zpl-pg/introduction.html),
[Zebra printer memory management](https://docs.zebra.com/us/en/printers/desktop/zd421-and-zd621-desktop-printers-user-guide/zpl-configuration/printer-memory-management-and-related-status-reports/zpl-programming-for-memory-management.html)

Zebra states that ZPL and Set-Get-Do commands are distinct command families.
The printer executes configuration commands immediately. Zebra also documents
that ZPL changes can affect SGD configuration changes. This makes a
document-level command allowlist safer than a denylist.

Source: [Zebra Set-Get-Do overview](https://docs.zebra.com/us/en/printers/software/zpl-pg/about-sgd-printer-commands/overview.html)

### Commands required by the first Community stock target

The parser allowlist for Contract Major 1 contains only the commands required
by the approved Odoo stock views:

| Command | Use | Contract rule |
| --- | --- | --- |
| `^XA` | Start a label format | Required once per label envelope |
| `^XZ` | End a label format | Required once per label envelope |
| `^CI28` | Select UTF-8 character encoding | Required once in each label envelope |
| `^FO` | Field origin | Coordinates must fit the Device Capability geometry |
| `^FT` | Field typeset origin | Coordinates must fit the Device Capability geometry |
| `^A0N` | Built-in font | Orientation and size use bounded values |
| `^FD` | Field data | Data ends only at `^FS` and cannot contain an unescaped control byte |
| `^FS` | Field separator | Required after every field |
| `^FH` | Hex field-data mode | Only the canonical field-data escape mode is accepted |
| `^BY` | Bar code defaults | Width, ratio, and height use bounded values |
| `^BCN` | Code 128 | Height and flags use bounded values |
| `^BXN` | Data Matrix | Module size and quality use bounded values |

The parser can add drawing commands such as `^GB` in a later contract major
after a real Odoo report requires them. Contract Major 1 rejects commands that
the approved Community stock views do not use. This limits the grammar and
keeps physical qualification small.

The parser rejects `^PQ`. Odoo expands report quantity into separate label
envelopes. A report cannot hide an unbounded copy count inside one ZPL command.

## Prohibited command classes

The following classes are rejected before an Agent receives the payload. The
list is intentionally broader than the current upstream templates.

### Persistent storage and file operations

Reject stored-format recall and every object or memory operation, including:

- `^DF`, `^XF`, `^XG`, `^IS`, `^IL`, `^IM`
- `^ID`, `^TO`, `^CM`, `^CW`, `^FL`
- `~DG`, `~DS`, `~DT`, `~DU`, `~DY`
- `^JB`, `~JB`, `^WD`, and directory or memory reports.

`^XF` is dangerous even though it reads rather than writes. It recalls a
format stored in printer memory. The recalled format is outside the signed
Odoo payload and can change the effective printer program.

Sources: [Zebra recall stored format](https://docs.zebra.com/us/en/printers/software/zpl-pg/advanced-techniques/recall-stored-format-command.html),
[Zebra download graphics](https://docs.zebra.com/us/en/printers/software/zpl-pg/zpl-commands/~dg2.html),
[Zebra memory management commands](https://docs.zebra.com/us/en/printers/desktop/zd421-and-zd621-desktop-printers-user-guide/zpl-configuration/printer-memory-management-and-related-status-reports/zpl-programming-for-memory-management.html)

### Network and remote control

Reject all ZPL network commands, including `^ND`, `^NS`, `^NW`, `^WS`, `^WX`,
wireless reset or print commands, and any `! U1` Set-Get-Do command. Reject
ZBI programs and every payload beginning with `!`.

The parser also rejects `^CC`, `~CC`, `^CD`, and `~CD`. A document cannot change
the command prefix or delimiter to hide a later command from a simple scanner.

Source: [Zebra ZPL network command index](https://docs.zebra.com/us/en/printers/software/zpl-pg/c-zpl-zpl-commands.html#zpl-network-commands)

### Firmware, reset, and printer configuration

Reject reset, firmware, configuration, calibration, density, media, and
communication commands.

This class includes `~JR`, `^JU`, `^JZ`, `^JM`, `^MT`, `^MN`, `^MM`, `^PW`,
`^LL`, `^MD`, `^PR`, `^SC`, `^SD`, `^SI`, `^SO`, `^SZ`, `^JS`, `^JH`, and
`~DY`.

Reject `^PP`, `~PP`, `^PH`, and `~PH`. They pause the printer or feed media.
The first document contract does not allow a report to consume media outside
its label envelopes. Feed, pause, calibration, and configuration are separate
Agent-owned Device Work operations with their own authorization.

Zebra documents `~JR` as a power-on reset that clears buffers and resets
communication parameters. Zebra documents `^JU` as a command that can save
configuration for use at power-on. These are direct reasons to reject both
commands from a report payload.

Source: [Zebra test and setup commands](https://docs.zebra.com/us/en/printers/software/zpl-pg/advanced-techniques/test-and-setup-commands.html),
[Zebra configuration update command](https://docs.zebra.com/us/en/printers/software/zpl-pg/c-zpl-zpl-commands/r-zpl-ju.html)

### RFID and diagnostics

Reject all RFID commands. Reject host status, memory status, configuration
labels, head diagnostics, and other query commands such as `~HS`, `~HM`,
`~HQ`, `~HD`, and `~WC` in a Label Document.

The Agent can run a fixed diagnostic command as a separate typed Device Test
or health operation. A report cannot use a diagnostic response as an output
proof and cannot mix a query with a print payload.

## Parser and validation strategy

The `Label Document` parser is a stateful lexer and validator. A regular
expression denylist is not sufficient.

### Lexical rules

1. Accept UTF-8 bytes only. Reject NUL and unsupported control bytes.
2. Accept CR, LF, and horizontal tab only between commands. Treat all other
   control bytes as invalid.
3. Accept the canonical caret and tilde prefixes only. Reject prefix or
   delimiter changes before parsing the next command.
4. Reject `!`, SGD, ZBI, binary object download markers, and bytes outside the
   approved text encoding.
5. Parse `^FD` data as a separate state. A caret or tilde inside field data is
   invalid unless the data uses the approved `^FH` hex escape form.

### Structural rules

1. The document contains one to `max_labels` complete `^XA ... ^XZ`
   envelopes.
2. No command exists outside an envelope.
3. Every field has one origin, one font or barcode declaration, one `^FD`, and
   one `^FS` in the grammar accepted by the Report Binding.
4. Coordinates, font sizes, barcode dimensions, field lengths, and envelope
   bytes stay within the selected Device Capability.
5. The document contains no `^PQ`. The parser counts envelopes instead.
6. The parser returns a normalized command sequence and a reason code. It does
   not rewrite an invalid document into a valid one.

Every envelope contains `^CI28`. Every `^A0N` command contains explicit height
and width. The addon validates requested quantities before QWeb rendering.

### Producer qualification

The Report Binding stores the report action identity, report template identity,
approved command profile, layout profile, and template digest. The addon runs a
static command-set test for every approved report and a rendered fixture test
for every dynamic field. A report binding is inactive until the fixtures pass.

The fixture corpus includes product, lot, package, barcode, date, price, and
quantity values. It includes Unicode, `^`, `~`, `_`, CR, LF, NUL, and long
values.

The safe result is correct escaped field data or a clear preflight rejection.
The addon never strips or truncates values.

Canonical `^FH` uses `_` as its indicator and encodes a literal `_` as `_5F`.
Every quality-200 `^BX` declares backslash as its escape character. Data Matrix
values apply barcode escaping before `^FH` escaping and double a literal
backslash. Unsupported controls and overflow cause rejection.

## Media, DPI, and size limits

Odoo Community 19 defines four exact product layout sizes. The first contract
uses one 203 DPI profile for each size:

- `odoo_stock_product_zpl_203_normal_v1`: 2.25 by 1.25 inches
- `odoo_stock_product_zpl_203_small_v1`: 1.25 by 1 inch
- `odoo_stock_product_zpl_203_alternative_v1`: 2 by 1 inch
- `odoo_stock_product_zpl_203_jewelry_v1`: 2.2 by 0.5 inches.

Larger stock labels use the separate exact
`odoo_stock_zpl_203_4x6_v1` profile with 812 by 1,218 printable dots on 4 by 6
inch media. One rendered report has a 2 MiB limit and at most 500 envelopes.
The addon validates and splits it into one-envelope Label Documents before it
creates Print Intents. The contract permits no payload scaling, density change,
media change, or printer-side copy command.

ZPL coordinates are printer dots, so the Agent cannot send a 203 DPI document
to a 300 DPI profile. A 300 DPI printer needs a new profile identity, Report
Binding, template qualification, and physical gate. A different media size has
the same requirements.

The Driver Profile pins manufacturer, model, firmware version and build,
language mode, fonts, media sensing, complete device configuration, printable
width, label length, origin, margins, orientation, density, action mapping, and
transport details. Geometry uses integer dots.

The profile stays inactive until one exact Hardware Certification Matrix Row
binds its Device, firmware, Driver, Platform Backend, connection, media, and
operating system. The document cannot change these values. Device Preflight
fails closed when a binding and Device Capability do not match exactly.

Preflight validates `report action -> template digest -> layout profile ->
Hardware Certification Matrix Row`. Any mismatch stops before Agent
acceptance.

The 2 MiB limit applies to the complete rendered report before splitting. Each
one-envelope Label Document also passes the Agent payload limit before platform
spooling. The Agent enforces the per-Device queue and Device Spool limits. A
parser limit does not replace storage quotas.

## Capability checks

Preflight requires all of these values:

- `operation = print`
- `media_type = label_document`
- `language = zpl`
- `contract_major = 1`
- the exact Report Binding layout profile
- the exact DPI and printable geometry
- the maximum payload and label count
- the Driver Profile digest
- the supported Output Evidence level
- a current physical Device Test
- a live Agent state that is ready for Device Work.

The Binding Revision pins the selected values. Odoo uses a Controller projection
for configuration screens but asks the local Agent for live Preflight in local
POS work. A stale projection or missing Agent capability does not trigger
automatic rerouting. The workflow keeps the Native Device Path or presents an
explicit setup error before acceptance.

The physical Device Test prints one signed, bounded fixture. It checks the
selected geometry, barcode mode, character encoding, and platform job
identity. It does not allow an operator-entered ZPL string.

## Job evidence

ZPL content does not provide a reliable physical end-of-job signal. A
successful write proves different facts on different transports:

- Windows `WritePrinter` and `EndDocPrinter`, with every byte written, give
  `spooler` evidence. A complete spooler state gives at most `transport`
  evidence unless the signed Driver Profile has tested final-media evidence.
- CUPS gives a Platform Job Identity and a completed IPP job gives `spooler`
  evidence. A forwarding queue can give `transport` evidence when it reports
  that the job is queued in the device.
- Direct TCP write to port 9100 gives transport evidence only. A socket close
  is not proof of printed media.
- `~HS` or another status query is a separate diagnostic operation. It does
  not bind printer status to one Label Document.
- `device` evidence requires a signed, physically qualified Driver Profile
  with a direct final-media counter, end-of-job sensor, or equivalent proof.

The Agent stores the full Platform Job Identity, byte count, payload
fingerprint, Driver Profile digest, and evidence level. A short write,
connection loss after submission, platform status timeout, cancellation after
execution, or partial output maps to `outcome_unknown`. A stronger result
requires signed Driver or Platform Backend evidence, qualified by the Driver
Profile, that no Device I/O began.

## Verification plan

The first release gate includes:

- every standard Community stock ZPL report listed above
- each product, lot, package, packaging, and operation type fixture
- quantity at one, at the limit, and over the limit
- exact 2 MiB and 2 MiB plus one byte cases
- malformed UTF-8 and unsupported control bytes
- incomplete, nested, and out-of-order `^XA`, `^XZ`, `^FD`, and `^FS`
- field injection attempts with `^JUS`, `^DF`, `~DG`, `! U1`, network
  commands, reset commands, and RFID commands
- alternate prefixes and delimiters
- oversized coordinates, barcode dimensions, fields, and copy counts
- 203 DPI physical labels on each supported Windows and Linux printer
  profile
- rejection on 300 DPI or incompatible media profiles
- CUPS and Windows Platform Job Identity capture
- short write, disconnect, cancel, status timeout, and uncertain-result
  recovery
- parser property tests and fuzz tests with a maximum CPU and memory budget.

The physical suite must compare printed geometry and barcode readability. A
parser pass alone does not certify a label printer.

## Recommendation

Ship ZPL-only for the first Community stock target, but ship it as a narrow
versioned `Label Document` contract. Approve only the signed Odoo stock report
bindings after the variable-field escaping work passes. Keep arbitrary native
printer languages on a separate, disabled-by-default future Interface. This
gives the required Community stock label coverage without turning Odoo report
data into an unrestricted printer control channel.
