# Contributing to Inari

Inari spans edge hardware, a local-first Python agent, a native tray companion,
a Rust controller, a Leptos web interface, and Kubernetes deployment assets.
Keeping those responsibilities distinct is part of the product architecture.

Before changing the repository, read the project standards in
[AGENTS.md](../AGENTS.md) and the system boundaries in
[ARCHITECTURE.md](../ARCHITECTURE.md). Protocol changes must also remain aligned
with [the managed gateway specification](../docs/gateway_protocol.md).

## Repository layout

- `packages/agent` contains the local agent, hardware integrations, managed
  enrollment client, and service runtime.
- `packages/agent_tray` contains the user-session setup and status companion. It
  controls and observes the agent; it is not the service daemon.
- `packages/brand` contains the canonical identity assets shared by web, tray,
  packaging, and documentation surfaces.
- `crates/inari-server` is the Axum composition root and concrete Zenoh adapter.
- `crates/inari-gateway` owns managed-gateway protocol, security, application
  services, and PostgreSQL repositories.
- `crates/inari-migration` owns the embedded, forward-only SeaORM controller
  migrations.
- `crates/inari-web` contains shared Leptos components, routes, and server
  functions.
- `crates/inari-web-frontend` is the minimal browser WASM hydration entrypoint.
- `deploy/helm/inari` and `deploy/kustomize/inari` are alternative Kubernetes
  lifecycle surfaces; they must not manage the same installation concurrently.
- `docs` contains protocol, architecture, operations, deployment, and identity
  documentation.

## Working agreement

- Keep dependency injection and infrastructure assembly at composition roots.
- Preserve typed protocol boundaries and update implementations, fixtures,
  tests, and documentation together.
- Remove superseded code during migrations instead of leaving parallel legacy
  paths.
- Use canonical brand assets rather than redrawing the mark in individual
  product surfaces.
- Use `cargo clippy` for Rust compile validation; do not substitute
  `cargo check`.

## Verification

Install the repository toolchain and dependencies with:

```sh
just sync
```

Run the complete repository gate before submitting a change:

```sh
just check
```

For focused work, `just format` and `just lint` provide faster feedback, but
they do not replace the complete verification suite.
