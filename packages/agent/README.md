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

For the current external controller contract, see [docs/gateway_protocol.md](C:/Users/pablo/.codex/worktrees/2eb5/odoo_iot_alt/docs/gateway_protocol.md). For supported deployment stacks with Caddy, ZITADEL, and step-ca, see [docs/managed_gateway_stacks.md](C:/Users/pablo/.codex/worktrees/2eb5/odoo_iot_alt/docs/managed_gateway_stacks.md).

## Highlights

- loopback-first FastAPI service with explicit CORS allowlist
- driver registry that can grow from Windows printers into broader IoT device support
- Windows spooler driver isolated from the application layer
- coherent HTTP API split into `system`, `devices`, `jobs`, and live `events`
- built-in gateway mode so the agent can operate as its own secure local edge node
- scoped bearer-token auth for HTTP and WebSocket endpoints
- local loopback token bootstrap for desktop companions such as the tray app
- managed upstream enrollment, token refresh, and outbound status sync over HTTPS/WSS
- controller protocol version negotiation plus durable upstream inbox/outbox persistence
- upstream command acknowledgement, replay-safe deduplication, and runtime event forwarding
- optional managed client-certificate installation for outbound mTLS
- ZITADEL service-account auth with private-key JWT for managed controller access
- controller-issued enrollment-code bootstrap for seamless managed installs
- step-ca-backed client-certificate bootstrap, issuance, and renewal through controller-issued one-time tokens
- Caddy-compatible controller edge profile with optional or required mTLS
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
- SQLite-backed gateway inbox/outbox for upstream command audit and replay
- background discovery polling plus lease-based job recovery

## Security And Gateway

- `gateway_exposure=loopback` is the secure default
- LAN exposure requires TLS certificate and key material at startup
- protected routes are scope-based rather than all-or-nothing
- the tray and other local clients use short-lived local bearer tokens
- managed mode adds outbound enrollment, token refresh, status sync, and a controller protocol without changing the local runtime model
- the upstream boundary now persists inbound commands and outbound event messages for replay-safe delivery
- negotiated controller protocol versions and optional client certificates harden the managed control plane
- managed auth can come from controller-issued tokens or ZITADEL service accounts
- managed client certificates can come from controller enrollment or controller-issued step-ca OTT bootstrap
- Caddy edge mode validates HTTPS/WSS and mTLS expectations early at startup

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
IOT_AGENT_UPSTREAM_EDGE_PROVIDER=caddy
IOT_AGENT_UPSTREAM_MUTUAL_TLS_MODE=required
IOT_AGENT_UPSTREAM_AUTH_MODE=zitadel_service_account
IOT_AGENT_ZITADEL_BASE_URL=https://zitadel.example.com
IOT_AGENT_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
IOT_AGENT_UPSTREAM_BOOTSTRAP_TOKEN=replace-me
IOT_AGENT_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example/api/iot-agent/enroll
IOT_AGENT_UPSTREAM_ENROLLMENT_CODE=SITE-A-4F7K-92LM
IOT_AGENT_UPSTREAM_CERTIFICATE_MODE=step_ca
```

For controller-issued step-ca bootstrap, the controller can return the CA URL, fingerprint, sign URL, and renew URL dynamically, so those values do not need to be preconfigured on every agent. `IOT_AGENT_STEP_CA_URL`, `IOT_AGENT_STEP_CA_SIGN_URL`, `IOT_AGENT_STEP_CA_RENEW_URL`, and `IOT_AGENT_STEP_CA_ROOT_FINGERPRINT` remain available as explicit overrides when you want local fallback knowledge of the CA.
