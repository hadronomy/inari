# Inari

Inari is an IoT agent and managed gateway. The Python agent runs beside local hardware; the Rust server provides the controller-facing HTTPS enrollment surface, a hydrated Leptos operator interface, and the Zenoh data plane.

## Repository layout

- `packages/agent`: local agent, hardware integrations, managed enrollment client, and service runtime
- `packages/agent_tray`: user-session setup and status companion
- `crates/inari-server`: Axum composition root and concrete Zenoh adapter
- `crates/inari-gateway`: managed-gateway protocol, security, services, and SQLite repository
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
to configure the public controller URL and SHA-256 digests of the accepted
operator tokens before enabling invitation issuance.

The public HTTP namespaces are deliberately separate:

- `/api/inari/v1` exposes stable, typed Inari resources. Operational reads use
  the distinct bearer credentials configured under `managed_gateway.api`.
- `/api/zenoh/v1/{selector}` is the optional Axum-native Zenoh REST
  compatibility surface. The path after `/api/zenoh/v1/` is passed directly to
  Zenoh as the selector or key expression.

The UI is written in Rust and hydrated as WebAssembly. There is no authored application JavaScript; JavaScript emitted by wasm-bindgen is generated build output.

## Production build

```sh
cargo leptos build --release
```

Deploy the release binary together with `target/site`. Set `LEPTOS_SITE_ROOT` to that deployed site directory and configure the server through environment variables or a configuration file. The default managed-gateway database is `data/inari-server/managed-gateway.sqlite3`; migrations run before the HTTP server becomes ready.

The public protocol is documented in [docs/gateway_protocol.md](docs/gateway_protocol.md). The generated operator site must be served by the Rust binary so its CSP nonce, hydration scripts, static-asset handling, and Leptos routes remain consistent.
