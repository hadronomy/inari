# Architecture

Inari is a local-first device platform with an optional managed control plane.
The architecture is built around a simple rule: hardware work belongs close to
the hardware, while fleet policy and coordination belong in the controller.

## System shape

```text
Business application or POS
          │ local HTTP
          ▼
┌──────────────── edge host ────────────────┐
│ Inari Agent                              │
│  ├─ device discovery and drivers         │
│  ├─ durable jobs and event history       │
│  ├─ authenticated local API              │
│  └─ managed gateway client               │
│        ▲                                  │
│        │ local HTTP + events              │
│ Inari Device Center                      │
└──────────────────┬────────────────────────┘
                   │ HTTPS enrollment
                   │ Zenoh data plane
                   ▼
┌──────────── private infrastructure ───────┐
│ Inari Controller                         │
│  ├─ Axum JSON API                        │
│  ├─ Leptos operator console              │
│  ├─ enrollment, policy, and audit         │
│  └─ PostgreSQL repositories               │
│                                           │
│ Zenoh Router · OIDC · step-ca · Postgres │
└───────────────────────────────────────────┘
```

The edge agent opens every managed connection. No controller needs an inbound
route to a device host.

## Edge host

### Agent

`packages/agent` is the long-running Python service. It owns:

- discovery and stable device identity;
- transport- and platform-specific drivers;
- durable jobs, attempts, and local event history;
- the authenticated loopback API used by local software;
- managed enrollment and the Zenoh client;
- agent identity and protected local secrets.

SQLite is the right persistence boundary here: one process owns a local file,
and queued work must survive controller and network outages.

The composition root is `packages/agent/inari/container.py`. Dishka provider
modules live under `packages/agent/inari/di`; domain and runtime code use normal
constructor injection and do not depend on the container framework.

### Device Center

`crates/inari-device-center` is the native GPUI application in the user
session. It presents setup and device state, owns the tray icon, and controls
the service through the local API. It never becomes the hardware service and
never connects to Zenoh directly.

`crates/inari-agent-client` owns that local boundary: generated HTTP transport,
curated domain types, event-stream supervision, protected client identity, and
pairing. Transport models remain private so that the application is not shaped
by OpenAPI implementation details.

The installed application controls the operating-system service. Closing Device
Center hides the window while the tray remains active; explicitly quitting it
stops only the desktop client and leaves the agent running.

### Drivers and runtime

Interfaces discover transport-level descriptors; drivers claim those
descriptors and expose typed device capabilities. Platform details remain below
that boundary. Jobs enter a durable queue, are leased to a per-device worker,
and publish observable state as they progress.

```text
request → durable job → device worker → driver → hardware
               │                         │
               └──── history/events ◀────┘
```

User-facing names are presentation. Automation targets stable `DeviceId`
values derived from hardware identity wherever possible.

## Managed control plane

### Controller

The Rust workspace separates the controller by responsibility:

- `inari-server` assembles configuration, PostgreSQL, Axum, Leptos, OIDC, and
  the concrete Zenoh adapter;
- `inari-gateway` owns managed domain types, application services, security,
  and persistence mappings;
- `inari-migration` owns the embedded, forward-only PostgreSQL history;
- `inari-web` owns shared routes, components, and server functions;
- `inari-web-frontend` is the minimal browser hydration entry point.

Axum registers the API router before the Leptos routes. Everything below `/api`
is JSON or Zenoh-compatible HTTP. Browser fallbacks cannot handle an API path.

Controller state and sessions live in externally managed PostgreSQL. Production
pods verify the schema but do not migrate it; a dedicated deployment job runs
the embedded migrator under an advisory lock.

### Enrollment and data plane

Enrollment uses HTTPS because it begins before an agent certificate exists. The
controller validates the invitation, protocol version, Ed25519 JWK, CSR
signature, and CSR/JWK binding before it mints short-lived certificate bootstrap
material.

After enrollment, steady-state status, commands, results, and liveliness use
Zenoh over TLS with client certificates. The controller and router are separate
workloads: the controller owns application policy; Zenoh owns data-plane routing.

The [gateway protocol](docs/gateway_protocol.md) is the canonical wire contract.

## Trust boundaries

- Local browser and desktop clients authenticate to the loopback agent with
  paired identities and short-lived tokens.
- Device Center is a client of the agent, not a privileged in-process extension.
- The controller authenticates people with OIDC and authorizes typed roles.
- Agent enrollment consumes one-use credentials and binds them to cryptographic
  identity.
- Zenoh carries managed traffic only after transport and protocol checks.
- Windows package signing, device identity PKI, and Kubernetes workload PKI are
  independent trust domains.

Secrets are wrapped or protected at the boundary where they enter the system.
They do not belong in rendered HTML, URLs, ordinary configuration files, logs,
or debug representations.

## Main flows

### Local device work

```text
Odoo or local client
  → authenticated agent API
  → durable local job
  → driver execution
  → job history and live event
```

This path remains available when the controller is offline.

### Interactive enrollment

```text
Operator signs in to controller
  → creates one-use invitation
  → Device Center opens inari:// link
  → agent submits JWK + CSR over HTTPS
  → controller verifies identity and returns data-plane contract
  → agent obtains certificate and connects to Zenoh
```

The invitation secret stays in the URL fragment until the hydrated setup flow
hands it to Device Center; it is never sent in the setup-page request.

### Managed command

```text
Controller persists command
  → publishes to the agent Zenoh namespace
  → agent deduplicates and persists it
  → local runtime executes the job
  → result is persisted and published upstream
```

Sequence numbers and command identifiers make reconnect and replay explicit.

## Extension rules

New device families should join through the interface/driver boundary and the
existing durable runtime. New controller adapters should call application
services rather than repositories. Transport DTOs should map into domain types
at the edge of a crate or package.

Avoid shared “utilities” that hide domain decisions. Reuse the language and
ecosystem first; keep custom helpers for Inari-specific validation,
authorization, and protocol transitions.

## Related documentation

- [Agent guide](packages/agent/README.md)
- [Device Center guide](crates/inari-device-center/README.md)
- [Gateway protocol](docs/gateway_protocol.md)
- [Managed deployment](docs/managed_gateway_stacks.md)
- [Controller database](docs/controller_database.md)
- [Kubernetes operations](docs/kubernetes.md)
