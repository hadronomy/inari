# IoT Agent

Extensible local hardware bridge for POS and local devices.

The current MVP is still Windows-printer focused, but the agent now runs on top of a proper runtime layer with:

- durable job queueing
- background device discovery
- per-device worker ordering
- retry and lease recovery
- live event streaming
- persisted job and device history

## Highlights

- loopback-first FastAPI service with explicit CORS allowlist
- driver registry that can grow from Windows printers into broader IoT device support
- Windows spooler driver isolated from the application layer
- coherent HTTP API split into `system`, `devices`, `jobs`, and live `events`
- generic print-job endpoint with typed content kinds and nested device targeting
- receipt image pipeline that converts base64 images to monochrome ESC/POS raster commands
- structured ESC/POS receipt renderer with configurable layout and paper control
- MIME detection and binary payload inspection powered by `puremagic`
- cash drawer pulse support for RAW-capable receipt printers
- optional HTML printing hook through an injected renderer
- explicit extension points for future PDF and document renderers
- SQLite-backed runtime state for queued jobs, attempts, and event history

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
  runtime/
```

## HTTP API

Primary endpoints:

- `GET /system/status`
- `GET /devices`
- `GET /devices/{device_id}`
- `GET /devices/{device_id}/events`
- `POST /print-jobs`
- `POST /device-commands`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/history`
- `POST /jobs/{job_id}/cancel`
- `WS /events`

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

Print jobs use nested device targeting and execution options:

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
    "device_id": "dev_...",
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

Queued job responses expose the agent-managed job resource immediately, and the frontend can follow its lifecycle through `GET /jobs/{job_id}` or `WS /events`.

## Runtime

- Python 3.12+
- `puremagic` for MIME detection from decoded bytes
- Pillow for receipt image normalization and raster rendering
- SQLite-backed runtime store for devices, jobs, attempts, and events
- background discovery polling plus lease-based job recovery

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
IOT_AGENT_RUNTIME_DATABASE_PATH=./data/iot-agent.sqlite3
```
