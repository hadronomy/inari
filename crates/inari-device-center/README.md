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

- `app.rs` owns navigation, application-level actions, and the window shell;
- `features/` owns setup, overview, devices, activity, and support views;
- `infrastructure/` owns the supervised client runtime, tray, activation, and
  platform integration;
- `ui/` is the Inari design system over GPUI Component.

Mutable screen state lives in GPUI entities. Long-running network work belongs
to the owned Tokio runtime in `infrastructure/runtime.rs`, which cancels and
joins its tasks during shutdown.

### Design system

`ui/` is the single source of appearance. Views read semantic roles, never raw
colors:

- `theme.rs` holds every token and derives the GPUI Component palette from the
  same values, so the two cannot drift;
- `material.rs` decides whether the window is translucent or solid;
- `motion.rs` holds the durations and the reduced-motion gate;
- `status.rs` maps every device, job, and service state to one shared
  vocabulary;
- `surface.rs`, `content.rs`, `banner.rs`, `chrome.rs`, and `icon.rs` are the
  component and asset layer;
- `gate.rs` draws the connection path on Overview.

Tests in `theme.rs` hold the palette to WCAG AA for body text, to 3:1 for the
focus ring, and to a visible tonal step between elevations. They run against
light and dark in both materials.

### Window material

GPUI 0.2.2 can blur the content behind a window and nothing else. There is no
per-element backdrop filter, so no surface claims one: the chrome is
translucent over one real window blur, content surfaces use thin tonal washes,
and floating overlays keep a denser legibility tint.

| Platform | Behind-window blur | Default |
| --- | --- | --- |
| macOS | `NSVisualEffectView` | Translucent |
| Windows | Acrylic blur-behind | Translucent |
| Linux | Wayland only, compositor-dependent | Solid |

GPUI 0.2.2 reports no OS accessibility preferences, so Reduce Transparency and
Reduce Motion cannot be detected. Support offers both as preferences, and
`INARI_MATERIAL=opaque` and `INARI_REDUCED_MOTION` apply them from launch.

Windows is the production packaging target. The release workflow builds this
crate as `InariDeviceCenter.exe` and stages it beside the frozen Python agent
service in the signed MSIX.
