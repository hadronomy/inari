# Contributing to Inari

Thank you for helping with Inari. The repository crosses hardware, desktop UI,
web, identity, messaging, and Kubernetes, so a good change begins by finding the
right boundary and keeping it intact.

Read [AGENTS.md](../AGENTS.md) for the project’s engineering standards and
[ARCHITECTURE.md](../ARCHITECTURE.md) for the component model. Changes to the
managed wire contract also need the [gateway protocol](../docs/gateway_protocol.md).

## Find the right home

- `packages/agent` — local device service, drivers, job runtime, and managed
  edge client;
- `packages/agent_tray` — user-session tray, setup assistant, and Device Center;
- `packages/brand` — canonical identity assets;
- `crates/inari-server` — Axum composition root, configuration, and concrete
  Zenoh adapter;
- `crates/inari-gateway` — controller domain, protocol, security, and
  repositories;
- `crates/inari-migration` — embedded PostgreSQL migrations;
- `crates/inari-web` — shared Leptos routes, components, and server functions;
- `crates/inari-web-frontend` — browser hydration entry point;
- `deploy/helm/inari` and `deploy/kustomize/inari` — Kubernetes distribution;
- `deploy/windows` — Device Center packaging and signing;
- `tooling` — Tegami configuration and release plugins.

The agent is the service; the tray is its desktop client. Enrollment uses
HTTPS; managed traffic uses Zenoh. Axum owns `/api`, while Leptos owns browser
pages. These boundaries should remain obvious after your change.

## Work on a change

Install the pinned toolchain and synchronize the workspace:

```sh
mise install
mise exec -- just sync
```

Keep changes complete across code, tests, schemas, migrations, and docs. Remove
obsolete paths when replacing a design instead of leaving two ways to do the
same thing. Use canonical brand assets and keep framework-specific wiring at
composition roots.

Rust compile validation always runs through Clippy.

```sh
mise exec -- just format
mise exec -- just lint
mise exec -- just check
```

The full `just check` gate is required before review. If a platform-specific
test cannot run locally, say which gate remains and why.

## Describe releasable work

Add a pending Tegami note to the same pull request as any release-facing change:

```sh
mise exec -- bun run release -- changelog
mise exec -- bun run release:preview
```

Write the note for the operator or user receiving the release. The package
groups and publish lifecycle are documented in
[the release guide](../docs/releases.md).
