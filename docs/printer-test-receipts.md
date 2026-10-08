# Printer test receipts in Odoo

In **Inari Devices → Devices**, select **Test receipts** in the toolbar or
on a printer row. The printer page has the same action. A Device Manager can
choose any printer in the selected companies.

The dialog offers three choices:

- **Both receipts**: one copy of each sample.
- **INARI check**: text, Spanish accents, Code 128 barcode, QR code, and feed/cut.
- **MIZONA sample**: the full reference POS layout, with logo, products, tax,
  discount, payments, loyalty, barcode, and QR code.

Both images are fixed samples. Their dates, amounts, and customer details are
examples. Printing them creates no Odoo order or payment. It also creates no
signed Device Test evidence or hardware certification.

The printer needs an active receipt Binding Revision, a trusted Agent endpoint,
and an open POS session. If more than one POS authorizes the printer, select the
POS in the dialog. Missing setup is shown beside the printer selection. The
dialog never creates a Binding or bypasses Device Work admission.

On the first use, approve the browser's Client Pairing in Device Center on the
Agent Host. Check the phrase shown in Odoo before approval. The dialog then
submits each selected sample through the protected receipt queue.

Each receipt has its own Print Intent and result. **Queued** means the Agent
accepted the job. **Output confirmed** means the Agent reported output evidence.
Check the paper and scan the codes to confirm their quality. The jobs also
appear in **Device Center → Activity**.

Repeated clicks in the dialog do not create extra copies. Pending results are
saved in the browser. Reopening the dialog checks these jobs before another
batch can print. **Check result** reconciles the original Print Intents; it does
not submit new copies. If the result is unknown, inspect the printer first.

The dialog uses native radio buttons, labeled selectors, Odoo's keyboard-aware
Dialog, and live result announcements. Receipt previews are optional. The print
button stays disabled while a request is pending or the setup is incomplete.
