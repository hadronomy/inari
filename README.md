# Inari

Inari is an IoT agent and managed gateway. The Python agent runs beside local hardware; the Rust server provides the controller-facing HTTPS enrollment surface, a hydrated Leptos operator interface, and the Zenoh data plane.

## Repository layout

- `packages/agent`: local agent, hardware integrations, managed enrollment client, and service runtime
- `packages/agent_tray`: user-session setup and status companion
- `crates/inari-server`: Axum composition root and concrete Zenoh adapter
- `crates/inari-gateway`: managed-gateway protocol, security, services, and PostgreSQL repository
- `crates/inari-migration`: embedded, forward-only SeaORM controller migrations
- `crates/inari-web`: shared Leptos application and server functions
- `crates/inari-web-frontend`: minimal browser WASM hydration entrypoint
- `docs`: protocol and deployment documentation

## Development

Install stable Rust, the browser target, cargo-leptos, Python 3.13, `uv`, and `just`:

```sh
rustup target add wasm32-unknown-unknown
cargo install cargo-leptos --locked
just sync
```

Run the complete verification suite with:

```sh
just check
```

Rust compile validation always runs through Clippy; the repository does not use `cargo check` as a validation gate.

For the hydrated server during development:

```sh
cargo leptos watch
```

Plain `cargo run` is also supported from the workspace root. It reads the same
Leptos application metadata from `Cargo.toml`; run `cargo leptos build` first
when `target/site` does not yet contain the generated browser assets.

Managed onboarding is disabled by default. Start from
[`crates/inari-server/config.example.toml`](crates/inari-server/config.example.toml)
to configure the public controller URL, OIDC, PostgreSQL, step-ca, and Zenoh
before enabling invitation issuance. Human
operators authenticate through OIDC; static operator tokens are not supported.

The public HTTP namespaces are deliberately separate:

- `/api/inari/v1` exposes stable, typed Inari resources. Operational reads use
  the authenticated organization session and typed role permissions.
- `/api/zenoh/v1/{selector}` is the optional Axum-native Zenoh REST
  compatibility surface. The path after `/api/zenoh/v1/` is passed directly to
  Zenoh as the selector or key expression.

The UI is written in Rust and hydrated as WebAssembly. There is no authored application JavaScript; JavaScript emitted by wasm-bindgen is generated build output.

## Production build

```sh
cargo leptos build --release
```

Deploy the release binary together with `target/site`. Set `LEPTOS_SITE_ROOT` to that deployed site directory and configure the server through environment variables or a configuration file. Production controller state lives in externally managed PostgreSQL. Run `inari-server database migrate` before rolling controller replicas and use `inari-server database status` to verify that the schema is current. Retain `database.migrate_on_startup = true` only for a single-process development environment.

The production Helm chart lives at [`deploy/helm/inari`](deploy/helm/inari). It deploys the stateless controller separately from the Zenoh router StatefulSet and references existing Kubernetes Secrets rather than embedding credentials in values. A Kustomize-owned installation is available at [`deploy/kustomize/inari`](deploy/kustomize/inari); Helm and Kustomize are alternative lifecycle owners, not overlapping reconcilers.

The complete deployment, upgrade, certificate, network-policy, validation, and recovery procedure is documented in [docs/kubernetes.md](docs/kubernetes.md). Controller migration ownership and recovery policy are documented in [docs/controller_database.md](docs/controller_database.md).

The public protocol is documented in [docs/gateway_protocol.md](docs/gateway_protocol.md). The generated operator site must be served by the Rust binary so its CSP nonce, hydration scripts, static-asset handling, and Leptos routes remain consistent.
