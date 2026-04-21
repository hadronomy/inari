# Inari Architecture

## Goal

`Inari` is a local-first hardware bridge for POS and desktop-adjacent device workflows.

Its job is to keep local device operations reliable, observable, and secure while still allowing an optional managed upstream controller mode for fleets.

The project is intentionally split into two architectural planes:

- the **local runtime plane** for browser, tray, and device workflows on one machine
- the **managed upstream plane** for enrollment, fleet control, and remote coordination

## Topology

```text
Odoo backend / other local business app
  └─ browser or local desktop client
       └─ Inari local API on terminal machine (127.0.0.1:7310)
            ├─ runtime queue + device discovery
            ├─ printer drivers and future device plugins
            ├─ authenticated local HTTP API
            ├─ authenticated local WebSocket event stream
            ├─ native tray / Device Center
            └─ optional managed gateway
                 ├─ HTTPS enrollment
                 ├─ step-ca certificate lifecycle
                 └─ Zenoh managed data plane
```

## Core Principles

- The local machine remains the authoritative execution point for hardware work.
- The browser and the tray talk to devices only through the loopback agent.
- Managed mode extends the local runtime; it does not replace it.
- Device integrations stay behind explicit runtime and API boundaries.
- Upstream transport details must not leak into local device or job models.
- Reliability and replay safety matter more than transport cleverness.

## Major Subsystems

### 1. Local API Surface

The local API is a FastAPI service built in [main.py](./packages/agent/inari/main.py) and exposed through [api.py](./packages/agent/inari/api.py).

It provides:

- local token bootstrap
- scoped authenticated HTTP endpoints
- a live local WebSocket stream for runtime events
- system, device, job, and gateway diagnostics

This is the boundary consumed by:

- browser-based local clients
- the native tray app
- local diagnostics and operator tooling

### 2. Runtime Layer

The runtime layer is responsible for local work execution and persistence.

Main responsibilities:

- background device discovery
- durable queued jobs
- per-device worker ordering
- lease and retry recovery
- event publication
- persistent device/job/event history

Key code lives under:

- [runtime/](./packages/agent/inari/runtime)
- [printer_service.py](./packages/agent/inari/printer_service.py)
- [print_jobs.py](./packages/agent/inari/print_jobs.py)
- [device_commands.py](./packages/agent/inari/device_commands.py)

The runtime is local-first and remains fully useful even when managed mode is disabled.

### 3. Driver Layer

Drivers isolate platform and transport specifics from the runtime and API layers.

Current printer backends include:

- Windows spooler
- CUPS
- configured raw socket printers

Key code lives under:

- [drivers/](./packages/agent/inari/drivers)
- [drivers/printers/windows.py](./packages/agent/inari/drivers/printers/windows.py)
- [drivers/printers/cups.py](./packages/agent/inari/drivers/printers/cups.py)
- [drivers/printers/socket.py](./packages/agent/inari/drivers/printers/socket.py)

The intended plugin shape remains broader than printers. The architecture deliberately leaves room for scanners, scales, displays, and similar local device integrations.

### 4. Security Layer

The security layer handles:

- loopback-first exposure rules
- scoped local bearer tokens
- standalone local pairing with signed client challenges
- origin-bound token issuance for paired browser-style clients
- tray-held local identity material for first-party desktop trust
- optional TLS requirements for non-loopback exposure
- persistent agent identity material
- resilient local secret storage

Key code lives under:

- [security/](./packages/agent/inari/security)

Local desktop clients such as the tray pair with the standalone agent, sign local trust challenges from `POST /auth/local-challenge`, obtain short-lived local client tokens from `POST /auth/local-token`, and then use those tokens for protected local API access.

### 5. Managed Gateway Layer

Managed mode adds an upstream controller boundary without changing the local execution model.

The managed gateway stack is responsible for:

- HTTPS enrollment into the controller
- optional enrollment auth through controller-issued tokens or ZITADEL
- managed client-certificate enrollment and renewal through step-ca
- Zenoh-based steady-state status, command, and runtime-event transport
- replay-safe command persistence and reconnect recovery

Key code lives under:

- [gateway/](./packages/agent/inari/gateway)
- [gateway/data_plane/](./packages/agent/inari/gateway/data_plane)

Important architectural split:

- local desktop / browser clients use loopback HTTP + local WebSocket
- managed controller traffic uses HTTPS enrollment + Zenoh data plane

Those are intentionally different systems.

### 6. Native Tray and Device Center

The tray app is a real local operator surface, not just a small menu.

It is responsible for:

- local service lifecycle controls
- current runtime status
- live event awareness
- a native cross-platform Device Center for device inspection and actions

Key code lives under:

- [packages/agent_tray/inari_tray/app.py](./packages/agent_tray/inari_tray/app.py)
- [packages/agent_tray/inari_tray/device_center/](./packages/agent_tray/inari_tray/device_center)

The tray is intentionally **local API driven**. It does not speak Zenoh directly and should not be treated as part of the managed controller plane.

## Data Flow

### Local Print Flow

```text
Browser / local client
  -> local authenticated HTTP API
  -> runtime job queue
  -> driver resolution
  -> printer execution
  -> runtime event publication
  -> local WebSocket + job history visibility
```

### Tray / Device Center Flow

```text
Tray / Device Center
  -> local token bootstrap
  -> local authenticated HTTP reads
  -> local WebSocket event stream
  -> event-led UI refresh with slow fallback reconciliation
```

### Managed Controller Flow

```text
Inari
  -> HTTPS enrollment
  -> optional step-ca bootstrap and client certificate issue
  -> Zenoh TLS + mTLS data plane
  -> controller commands / agent status / runtime events
```

## Persistence

The system persists two distinct classes of state:

### Runtime Persistence

Stored in the runtime database:

- devices
- jobs
- job attempts
- runtime events

### Managed Gateway Persistence

Stored in the gateway persistence layer:

- inbound controller commands
- outbound publications
- controller replay position

This separation is intentional. Local runtime reliability and managed-transport reliability are related, but they are not the same concern.

## Process and Supervision Model

At startup:

1. configuration is loaded and normalized
2. logging is configured
3. migrations run
4. the application supervisor starts the runtime
5. when enabled, the managed gateway supervisor starts the upstream stack

The composition root is [container.py](./packages/agent/inari/container.py), with provider modules under [di/](./packages/agent/inari/di). The DI framework is intentionally contained at the composition boundary; the domain and runtime code continue to rely on normal constructor injection.

## Deployment Modes

### Standalone Mode

This is the default local deployment:

- loopback-first
- no controller enrollment
- local runtime only
- tray and browser clients talk directly to the local API

### Managed Mode

This adds the upstream controller boundary:

- controller enrollment over HTTPS
- optional ZITADEL-backed enrollment auth
- optional step-ca-managed client certificates
- steady-state Zenoh data plane

Managed mode is additive. The local runtime still remains the execution engine.

## Odoo Boundary

The Odoo side should stay thin.

Odoo responsibilities remain:

- storing terminal-side bridge configuration
- providing terminal configuration to the POS frontend
- redirecting local hardware transport toward the loopback agent

Odoo should not become the hardware execution layer. That complexity belongs in Inari.

## Documentation Map

For the managed controller wire contract, see [docs/gateway_protocol.md](./docs/gateway_protocol.md).

For supported managed deployment stacks, see [docs/managed_gateway_stacks.md](./docs/managed_gateway_stacks.md).

## Engineering Discipline

The repo’s quality bar is:

- explicit boundaries
- durable local behavior
- strict typed interfaces
- cross-platform local UX
- documentation that reflects the code we actually ship

Type checking, linting, and tests are expected parts of the architecture contract, not optional cleanup:

- `ty`
- `ruff`
- `pytest`
