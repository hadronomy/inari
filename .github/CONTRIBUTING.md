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
  controls and observes the agent, while the agent owns the service lifecycle.
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
  lifecycle surfaces. Each installation has exactly one lifecycle owner.
- `docs` contains protocol, architecture, operations, deployment, and identity
  documentation.

## Working agreement

- Keep dependency injection and infrastructure assembly at composition roots.
- Preserve typed protocol boundaries and update implementations, fixtures,
  tests, and documentation together.
- Complete migrations across code, tests, documentation, and schemas so each
  boundary has one current path.
- Use canonical brand assets rather than redrawing the mark in individual
  product surfaces.
- Use `cargo clippy` for every Rust compile-validation pass.

## Verification

Install the declared repository toolchain and synchronize the workspace with:

```sh
mise install
mise exec -- just sync
```

Run the complete repository gate before submitting a change:

```sh
mise exec -- just check
```

For focused work, `mise exec -- just format` and `mise exec -- just lint`
provide faster feedback. The complete verification suite remains the final
pre-submit gate.
