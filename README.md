<div align="center">
  <img src="packages/brand/inari_brand/assets/readme-header.webp" alt="Inari — the trusted threshold between physical devices and software" width="100%" />
  <p></p>
  <a href="https://github.com/hadronomy/inari/blob/main/LICENSE">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/license/hadronomy/inari.svg?mode=dark" />
      <img alt="MIT License" src="https://shieldcn.dev/github/license/hadronomy/inari.svg?mode=light" />
    </picture>
  </a>
  <a href="https://github.com/hadronomy/inari/stargazers">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/stars/hadronomy/inari.svg?mode=dark" />
      <img alt="GitHub stars" src="https://shieldcn.dev/github/stars/hadronomy/inari.svg?mode=light" />
    </picture>
  </a>
  <p></p>
  <p align="center">
    <strong>A private control plane for the devices your software still has to touch.</strong><br />
    <sub>Printers, scales, scanners, and edge hardware—local-first, secure, and observable.</sub>
  </p>
  <p></p>
  <a href="#overview">Overview</a> •
  <a href="#getting-started">Getting Started</a> •
  <a href=".github/CONTRIBUTING.md">Contributing</a> •
  <a href="#production-deployment">Deployment</a> •
  <a href="#license">License</a>
  <hr />
</div>

> [!CAUTION]
> Inari is alpha software. Protocol, configuration, and storage contracts may change before the first stable release.

## Overview

Inari brings printers, scales, scanners, and other peripherals into private
infrastructure through a local-first device platform. Business systems such as
Odoo work with a stable device API while the agent handles vendor drivers, USB
protocols, and local failure recovery.

The Python agent runs beside the hardware. It owns discovery, drivers, local
durability, and offline operation. The tray provides user-session setup and
status, while the agent remains the long-running service. In a managed
installation, the Rust controller handles enrollment, operator workflows,
policy, and fleet coordination. Enrollment uses HTTPS, while steady-state
traffic runs over Zenoh.

## Getting started

Inari is a mixed Rust and Python workspace. A development machine needs stable
Rust, Python 3.13, [`uv`](https://docs.astral.sh/uv/),
[`Mise`](https://mise.jdx.dev/), and `cargo-leptos`. Mise installs the
repository tools declared in [`mise.toml`](mise.toml), including `just` and the
Kubernetes validation toolchain.

Install those tools, add the Rust browser target, and synchronize the workspace:

```sh
mise install
rustup target add wasm32-unknown-unknown
cargo install cargo-leptos --locked
mise exec -- just sync
```

The canonical pre-submit gate runs through the same Mise-managed environment:

```sh
mise exec -- just check
```

It covers the Rust, Python, web, and deployment checks that apply to the whole
repository.

Clippy is the required Rust compile-validation path for every change.

## Running the controller locally

The normal development loop for the controller and hydrated web interface is:

```sh
cargo leptos watch
```

Plain `cargo run` also works from the workspace root and uses the Leptos
metadata in `Cargo.toml`. Run `cargo leptos build` first to populate
`target/site` with the browser assets.

The edge agent and tray have their own focused setup notes in
[`packages/agent/README.md`](packages/agent/README.md) and
[`packages/agent_tray/README.md`](packages/agent_tray/README.md).

## Enabling managed operation

Managed operation requires a public controller URL, PostgreSQL, OIDC, step-ca,
and Zenoh. Use
[`crates/inari-server/config.example.toml`](crates/inari-server/config.example.toml)
as the starting point. Once configured, the controller exposes the invitation
workflow and operators sign in through OIDC.

## Public interfaces

Axum owns every API route before the Leptos fallback. The two public namespaces
have different responsibilities and should remain separate:

- `/api/inari/v1` is the typed JSON API for Inari resources and operations.
- `/api/zenoh/v1/{selector}` is the optional Zenoh HTTP compatibility surface.
  The selector after the prefix is forwarded directly to Zenoh with its HTTP
  semantics intact.

Axum returns JSON or Zenoh-compatible responses beneath `/api`, while Leptos
owns browser pages outside that namespace. The operator UI is written in Rust
and hydrated as WebAssembly, with `wasm-bindgen` supplying the generated browser
glue.

## Production deployment

The server binary and generated site directory are one release unit:

```sh
cargo leptos build --release
```

Ship `target/site` with the binary and point `LEPTOS_SITE_ROOT` at its deployed
location. Production controller state belongs in externally managed PostgreSQL.
Run `inari-server database migrate` before rolling controller replicas, then
use `inari-server database status` to confirm that the schema is current.
Automatic startup migration is reserved for single-process development.

For Kubernetes, the maintained Helm chart lives in
[`deploy/helm/inari`](deploy/helm/inari). It deploys the stateless controller and
Zenoh router as separate workloads and consumes existing Kubernetes Secrets.
[`deploy/kustomize/inari`](deploy/kustomize/inari) provides a Kustomize-owned
alternative. Each installation assigns lifecycle ownership to exactly one of
these deployment surfaces.

## Further reading

- [Architecture](ARCHITECTURE.md) explains the agent, tray, controller, and
  Zenoh boundaries.
- [Contributing](.github/CONTRIBUTING.md) contains the repository map and the
  working agreement for maintainers.
- [Kubernetes deployment](docs/kubernetes.md) covers installation, upgrades,
  certificates, network policy, validation, and recovery.
- [Windows Device Center](docs/windows.md) covers trust deployment,
  installation, upgrades, and the service/tray boundary.
- [Release process](docs/releases.md) documents Tegami, Helm publication, and
  signed Windows artifacts.
- [Controller database](docs/controller_database.md) documents migration
  ownership and forward-repair policy.
- [Managed gateway protocol](docs/gateway_protocol.md) defines the public wire
  contract.
- [Zenoh HTTP compatibility](docs/zenoh_rest_axum.md) describes the Axum-owned
  compatibility surface.
- [Brand and identity](docs/brand.md) documents the canonical assets, usage
  rules, accessibility guidance, and cultural rationale.

## License

Inari is available under the [MIT License](LICENSE).
