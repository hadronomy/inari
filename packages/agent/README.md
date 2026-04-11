# IoT Agent

Extensible local hardware bridge for POS and local devices.

The current MVP is printer-focused on Windows, but the internals now use a driver registry, typed print-job content models, and explicit device contracts so new device families and new print pipelines can be added without rewriting the HTTP API or core application service.

## Highlights

- loopback-first FastAPI service with explicit CORS allowlist
- driver registry that can grow from Windows printers into broader IoT device support
- Windows spooler driver isolated from the application layer
- coherent HTTP API split into `system`, `devices`, and `printing` route groups
- generic print-job endpoint with typed content kinds and nested target/options objects
- receipt image pipeline that converts base64 images to monochrome ESC/POS raster commands
- structured ESC/POS receipt renderer with configurable layout and paper control
- MIME detection and binary payload inspection powered by `puremagic`
- cash drawer pulse support for RAW-capable receipt printers
- optional HTML printing hook through an injected renderer
- explicit extension points for future PDF and document renderers

## Layout

```text
iot_agent/
  api.py
  binary_payloads.py
  container.py
  drivers/
    printers/
      windows.py
  print_jobs.py
  printer_service.py
  printers/
  receipt_renderers/
```

## HTTP API

Primary endpoints:

- `GET /system/status`
- `GET /devices/printers`
- `GET /devices/printers/{printer_name}`
- `POST /print-jobs`
- `POST /printer-commands`

## Error Format

Failures now use a single problem-details-style envelope across service errors, request validation errors, and framework HTTP errors:

```json
{
  "ok": false,
  "type": "urn:iot-agent:error:mime-type-mismatch",
  "title": "MIME Type Mismatch",
  "status": 400,
  "code": "MIME_TYPE_MISMATCH",
  "detail": "Declared MIME type 'image/jpeg' does not match detected MIME type 'image/png' for receipt image."
}
```

Validation failures add an `errors` array with field-level pointers such as `/content/binary/base64`.

## Print Content Kinds

`POST /print-jobs` accepts typed content kinds so the agent can route work without guessing from arbitrary JSON:

- `structured_receipt`
- `receipt_image`
- `text`
- `html`
- `pdf`
- `raw`

Binary content uses a shared wrapper:

```json
{
  "base64": "<base64-or-data-url>",
  "declared_mime_type": "image/png"
}
```

Print jobs use nested printer selection and execution options:

```json
{
  "content": {
    "kind": "receipt_image",
    "binary": {
      "base64": "data:image/png;base64,...",
      "declared_mime_type": "image/png"
    },
    "document_name": "POS Ticket"
  },
  "target": {
    "printer_name": "EPSON TM-T20III"
  },
  "options": {
    "transport": "auto",
    "open_cash_drawer": false
  },
  "metadata": {
    "source": "pos"
  }
}
```

## Runtime

- Python 3.12+
- `puremagic` for MIME detection from decoded bytes
- Pillow for receipt image normalization and raster rendering

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
IOT_AGENT_ALLOWED_ORIGINS=http://127.0.0.1:8069,http://localhost:8069
IOT_AGENT_DEFAULT_PRINTER_NAME=EPSON TM-T20III
IOT_AGENT_DEFAULT_PRINTER_MODE=auto
IOT_AGENT_HTML_PRINT_ENABLED=true
IOT_AGENT_LOG_LEVEL=INFO
```
