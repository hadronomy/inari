# Inari Agent

The agent is the local hardware service at the center of Inari. It discovers
devices, accepts work from local applications, persists jobs before execution,
and keeps operating when the network or controller is unavailable.

Device Center and browser clients talk to this service through the authenticated
local API. In managed mode, the same process enrolls with a controller over
HTTPS and exchanges steady-state traffic over Zenoh.

## Run it from source

From the repository root:

```sh
mise exec -- just sync
uv run --directory packages/agent inari
```

Pass a configuration file when you want settings beyond the development
defaults:

```sh
uv run --directory packages/agent inari serve \
  --config packages/agent/config.example.toml
```

The local API listens on `127.0.0.1:7310` by default. Scalar serves the OpenAPI
reference at `http://127.0.0.1:7310/docs`.

## Local API

The API is organized around a small set of resources:

| Area | Routes |
| --- | --- |
| Local trust | `/auth/local-challenge`, `/auth/local-token`, `/auth/pairing/*`, `/auth/me` |
| Agent state | `/system/status`, `/gateway/identity`, `/gateway/upstream/status` |
| Devices | `/devices`, `/devices/{device_id}`, `/devices/{device_id}/events` |
| Work | `/print-jobs`, `/device-commands`, `/jobs`, `/jobs/{job_id}` |
| Live updates | `WS /events` |

Operational routes require a scoped local token. Device Center pairs, signs a
challenge with its local identity, and refreshes short-lived tokens
automatically. Browser clients can bind tokens to an approved origin.

The WebSocket sends a complete snapshot when it opens, then event updates with
fresh system state. Clients can remain push-driven and use HTTP for deliberate
reconciliation instead of polling after every event.

Failures use an RFC 9457-style problem document:

```json
{
  "type": "urn:inari:error:mime-type-mismatch",
  "title": "MIME Type Mismatch",
  "status": 400,
  "code": "MIME_TYPE_MISMATCH",
  "detail": "The declared image type does not match the decoded content."
}
```

Validation errors add field pointers so clients can place feedback beside the
input that needs attention.

## Submit print work

`POST /print-jobs` accepts a discriminated content model rather than an
unstructured payload. Supported kinds are:

- `structured_receipt`
- `receipt_image`
- `text`
- `html`
- `pdf`
- `raw`

Binary content uses base64 with an explicit MIME type. A receipt-image request
looks like this:

```json
{
  "content": {
    "kind": "receipt_image",
    "binary": {
      "base64": "data:image/png;base64,...",
      "declared_mime_type": "image/png"
    },
    "document_name": "POS receipt"
  },
  "target": {
    "device_id": "dev_..."
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

The response is a durable job resource. Follow it through `/jobs/{job_id}` or
the live event stream. Prefer a stable `device_id`; printer names are a
human-facing fallback, not a durable automation key.

## Runtime and drivers

The runtime stores devices, jobs, attempts, and event history in local SQLite.
Workers lease jobs per device, heartbeat while they execute, and recover expired
leases after a crash. Alembic upgrades the database before the service starts
and backs up existing SQLite files when an upgrade requires it.

Current printer transports include:

- Windows spooler;
- CUPS on Linux and macOS;
- configured raw TCP printers;
- ESC/POS receipt rendering and cash-drawer control.

Install the optional `cups` extra when native pycups bindings are available:

```sh
uv sync --directory packages/agent --extra cups
```

The command-line CUPS tools remain the portable fallback.

## Configuration

Generate the schema and commented template from the canonical Pydantic model:

```sh
uv run --directory packages/agent inari-generate-config
```

The files are written to:

- `packages/agent/schemas/inari-config.schema.json`
- `packages/agent/config.example.toml`

Configuration resolves in this order:

1. built-in defaults;
2. the selected TOML file;
3. its sibling `*.local.toml` file;
4. `.env`;
5. `INARI_*` environment overrides.

`config_version = 1` is required. Keep ordinary settings in TOML and reserve
environment overrides for secrets, CI, and short-lived diagnostics.

Storage defaults depend on the selected profile:

- `development` uses repository-local `data`, `logs`, and `tmp` directories;
- `production` uses ProgramData on Windows, `/var/lib` and `/var/log` on Linux,
  and `/Library/Application Support` plus `/Library/Logs` on macOS;
- `auto` chooses development paths inside a checkout and production paths in an
  installed environment.

Use the generated template as the field reference rather than copying a second
configuration example into application documentation.

## Managed mode

Managed mode adds four boundaries to the local runtime:

1. HTTPS enrollment with a short-lived invitation or controller credential;
2. JWK and CSR identity binding;
3. optional step-ca certificate issue and renewal;
4. Zenoh status, command, result, and liveliness traffic.

The controller normally returns the Zenoh endpoints, namespace, permissions,
and certificate bootstrap details. Local configuration should contain only the
fallbacks or overrides the deployment genuinely owns.

See the [gateway protocol](../../docs/gateway_protocol.md) and
[managed deployment guide](../../docs/managed_gateway_stacks.md) for the wire
contract and production topology.

## Service management

The source package can install the agent as a native service:

```sh
uv run --directory packages/agent inari config write-default
uv run --directory packages/agent inari service print-definition
uv run --directory packages/agent inari service install
uv run --directory packages/agent inari service start
uv run --directory packages/agent inari service status
```

The default service identities are `InariService` on Windows,
`inari.service` on systemd, and `io.inari.service` on launchd. Linux and macOS
also support `--scope user`.

The Windows MSIX owns its packaged `InariAgent` service separately; use the
[Device Center installation guide](../../docs/windows.md) for that distribution.

Database maintenance commands are available without starting the runtime:

```sh
uv run --directory packages/agent inari db current
uv run --directory packages/agent inari db upgrade
uv run --directory packages/agent inari db backup
```

## Tests

Run the focused agent suite with:

```sh
uv run --directory packages/agent --group dev pytest tests -q
```

Repository-wide formatting, linting, type checking, and tests run through:

```sh
mise exec -- just check
```
