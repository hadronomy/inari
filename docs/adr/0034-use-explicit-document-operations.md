---
status: accepted
---

# Use explicit document operations

The public print contract exposes `receipt_image`, `report_pdf`, and
`label_document`. It does not expose general raw, text, HTML, or structured
receipt operations.

One deep Document Imaging Module checks and prepares each document. Drivers
receive a profile-specific artifact and own only physical output.

The Agent advertises an operation only when the required Document Imaging
Module loads and its Platform Backend and Driver pass startup checks. The
operation also requires a current physical Device Test Result.

Contract Major 1 accepts a Report PDF only after qpdf policy passes before
`Accepted`. Every font is embedded. The qpdf worker has a 384 MiB limit, the
PDFium worker has a 256 MiB limit, and the Windows Platform Backend has a 160
MiB limit.

A Label Document requires an allowlisted Report Binding, declared printer
language, media size, and DPI.

The first Label Document contract supports only the six registered Odoo
Community 19 stock ZPL actions. The unregistered transfer view is excluded. A
structural parser accepts only the commands those reports need.

The contract rejects persistent, file, network, firmware, RFID, configuration,
diagnostic, copy-count, prefix-change, and arbitrary command features.

Approved templates require `^CI28`, full font dimensions, bounded geometry, and
pre-render quantity checks. Product profiles use exact 203 DPI media sizes of
2.25 by 1.25, 1.25 by 1, 2 by 1, and 2.2 by 0.5 inches. Larger stock labels use
a separate exact profile.

One rendered report contains at most 500 envelopes. The addon validates and
splits it before Print Intent creation. Each Label Document contains one
envelope and represents one physical label.

Canonical `^FH` uses `_` as its indicator. Quality-200 `^BX` declares
backslash as its escape character. The helper applies barcode escaping before
hex field escaping and doubles a literal Data Matrix escape character. It
rejects controls and overflow without truncation.

EPL, TSPL, CPCL, custom ZPL, and arbitrary `qweb-text` remain Native Device
Paths until separate contracts pass their physical gates.
