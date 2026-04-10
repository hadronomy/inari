# Odoo IoT Agent

Extensible local hardware bridge for Odoo Community POS.

The current MVP is printer-focused on Windows, but the internals now use a driver registry and explicit device contracts so new device families can be added without rewriting the HTTP API or core application service.

## Highlights

- loopback-first FastAPI service with explicit CORS allowlist
- driver registry that can grow from Windows printers into broader IoT device support
- Windows spooler driver isolated from the application layer
- ESC/POS receipt renderer with configurable layout and paper control
- cash drawer pulse support for RAW-capable receipt printers
- optional HTML printing hook through an injected renderer

## Layout

```text
iot_agent/
  api.py
  container.py
  drivers/
    printers/
      windows.py
  printer_service.py
  printers/
  receipt_renderers/
```

## Run

From the repository root:

```powershell
uv run --directory packages/agent iot-agent
```

Or from inside `packages/agent`:

```powershell
uv run iot-agent
```

## Test

```powershell
uv run --directory packages/agent python -m unittest discover -s tests -v
```

## Environment

```env
ODOO_IOT_ALLOWED_ORIGINS=http://127.0.0.1:8069,http://localhost:8069
ODOO_IOT_DEFAULT_PRINTER_NAME=EPSON TM-T20III
ODOO_IOT_DEFAULT_PRINTER_MODE=auto
ODOO_IOT_HTML_PRINT_ENABLED=true
ODOO_IOT_LOG_LEVEL=INFO
```
