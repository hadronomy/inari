# Odoo IoT Agent

Windows-local hardware bridge for Odoo POS Community.

## Features

- bind to `127.0.0.1:7310` by default
- CORS allowlist for your Odoo origin
- printer discovery
- ESC/POS receipt printing from Odoo `export_for_printing()` payloads
- cash drawer pulse
- optional HTML passthrough

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 7310
```

## Environment

```env
ODOO_IOT_ALLOWED_ORIGINS=["http://127.0.0.1:8069","http://localhost:8069"]
ODOO_IOT_DEFAULT_PRINTER_NAME=EPSON TM-T20III
ODOO_IOT_HTML_PRINT_ENABLED=true
```
