# IoT Agent

Extensible local hardware bridge for POS and local devices.

The current MVP is still Windows-printer focused, but the agent now runs on top of a proper runtime layer with:

- durable job queueing
- background device discovery
- per-device worker ordering
- retry and lease recovery
- live event streaming
- persisted job and device history
- built-in gateway identity, scoped local auth, and optional managed upstream mode

## Highlights

- loopback-first FastAPI service with explicit CORS allowlist
- driver registry that can grow from Windows printers into broader IoT device support
- Windows spooler driver isolated from the application layer
- coherent HTTP API split into `system`, `devices`, `jobs`, and live `events`
- built-in gateway mode so the agent can operate as its own secure local edge node
- scoped bearer-token auth for HTTP and WebSocket endpoints
- local loopback token bootstrap for desktop companions such as the tray app
- managed upstream enrollment and outbound status sync over HTTPS/WSS
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

- `POST /auth/local-token`
- `GET /auth/me`
- `GET /gateway/identity`
- `GET /gateway/upstream/status`
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

Interactive API docs are served with Scalar at `GET /docs`.

The operational API is now authenticated. Local desktop clients obtain a short-lived bearer token from `POST /auth/local-token` over loopback, then use that token for protected HTTP and WebSocket calls. By default the agent runs in standalone, loopback-only gateway mode, so it does not need any external gateway to broker local device connections.

The live events stream is snapshot-backed: the socket sends an initial `snapshot` message on connect, then `event_update` messages containing both the runtime event and a refreshed `SystemStatusResponse`. That lets local clients stay push-first without repeatedly polling `/system/status` for every queue or device change.

Device directory responses are intentionally semantic rather than driver-internal. Devices expose:

- `driver_key` instead of a generic `driver` label
- `device_class` to distinguish physical and virtual devices
- a nested `connection` object with `state`, `first_seen_at`, `last_seen_at`, and `observed_at`
- printer transport support through `supported_transports`
- printer feature flags as a sparse `capabilities` array, for example `["cash_drawer"]`

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

Queued job responses expose the agent-managed job resource immediately, and the frontend can follow its lifecycle through `GET /jobs/{job_id}` or the snapshot-backed `WS /events` stream.

## Runtime

- Python 3.12+
- `puremagic` for MIME detection from decoded bytes
- Pillow for receipt image normalization and raster rendering
- `cryptography` for persistent agent identity material
- `joserfc` for signed local bearer tokens
- `keyring` with resilient local fallback for secret storage
- `websockets` for the managed upstream control stream
- SQLite-backed runtime store for devices, jobs, attempts, and events
- background discovery polling plus lease-based job recovery

## Security And Gateway

- `gateway_exposure=loopback` is the secure default
- LAN exposure requires TLS certificate and key material at startup
- protected routes are scope-based rather than all-or-nothing
- the tray and other local clients use short-lived local bearer tokens
- managed mode adds outbound enrollment, status sync, and optional control-stream connectivity without changing the local runtime model

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
IOT_AGENT_GATEWAY_MODE=standalone
IOT_AGENT_GATEWAY_EXPOSURE=loopback
IOT_AGENT_TRUSTED_HOSTS=127.0.0.1,localhost
IOT_AGENT_DEFAULT_PRINTER_NAME=EPSON TM-T20III
IOT_AGENT_DEFAULT_PRINTER_MODE=auto
IOT_AGENT_HTML_PRINT_ENABLED=true
IOT_AGENT_LOG_LEVEL=INFO
IOT_AGENT_RUNTIME_DATABASE_PATH=./data/iot-agent.sqlite3
IOT_AGENT_SECURITY_STATE_DIR=./data/security
IOT_AGENT_TLS_CERT_PATH=./certs/agent.crt
IOT_AGENT_TLS_KEY_PATH=./certs/agent.key
IOT_AGENT_UPSTREAM_BASE_URL=https://controller.example
IOT_AGENT_UPSTREAM_BOOTSTRAP_TOKEN=replace-me
```
