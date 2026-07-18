# Inari Device Center

Device Center is Inari’s native desktop client. It gives the signed-in user a
quiet tray presence, guides first-time setup, and presents the devices and work
owned by the local agent.

The application is intentionally not the agent service. It connects to the
local FastAPI boundary through `inari-agent-client`; closing the window or
quitting Device Center does not stop device work.

## Run it locally

Start the Python agent first, then launch the GPUI client:

```sh
uv run --directory packages/agent inari serve
cargo run -p inari-device-center
```

The committed contract in `contracts/local-agent.openapi.json` generates the
private HTTP transport at build time. Curated Rust types form the public client
boundary, so generated models do not leak into feature state.

The event stream is intentionally separate from OpenAPI. Its representative
wire envelope lives in `contracts/local-agent.events.json` and is validated by
both the Python service and Rust client tests.

Regenerate and verify the contract after changing a local API route or schema:

```sh
just check-contracts
```

## Architecture

The crate is organized by product feature:

- `app.rs` owns navigation and application-level actions;
- `features/` owns setup, overview, devices, activity, and support views;
- `infrastructure/` owns the supervised client runtime, tray, activation, and
  platform integration;
- `ui/` is the small Inari component and token layer over GPUI Component.

Mutable screen state lives in GPUI entities. Long-running network work belongs
to the owned Tokio runtime in `infrastructure/runtime.rs`, which cancels and
joins its tasks during shutdown.

Windows is the production packaging target. The release workflow builds this
crate as `InariDeviceCenter.exe` and stages it beside the frozen Python agent
service in the signed MSIX.
