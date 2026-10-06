# Inari Device Center

Device Center is Inari’s native desktop client. It gives the signed-in user a
quiet tray presence, guides first-time setup, and presents the devices and work
owned by the local agent.

The application is intentionally not the agent service. It connects to the
local FastAPI boundary through `inari-agent-client`; closing the window or
quitting Device Center does not stop device work.

## Run it locally

Start the Python agent first, then launch the GPUI client. `mbx` owns the build
storage and cache paths:

```sh
uv run --directory packages/agent inari serve
mbx run -p inari-device-center
```

The committed projection in `contracts/local-agent.codegen.openapi.json` generates the
private HTTP transport at build time. Curated Rust types form the public client
boundary, so generated models do not leak into feature state.

The event stream is intentionally separate from OpenAPI. Its representative
wire envelope lives in `contracts/local-agent.events.json` and is validated by
both the Python service and Rust client tests.

Regenerate and verify the contract after changing a local API route or schema:

```sh
just check-contracts
```

## Approve an Odoo browser

Client Pairing grants one Odoo browser access to this Agent. It is separate
from Enrollment, which connects the Agent to its Controller.

1. Create a Pairing Request in Odoo.
2. Open its `inari://pairing/` link in Device Center to read the request.
3. If the link does not open, select **Client Pairing** and paste the link or request ID.
   Then select **Review request**.
4. Compare every word of the phrase with Odoo.
5. Check the Odoo origin, Agent Endpoint, business scope, and requested permissions.
6. If the request matches, select **Approve browser**.
7. Return to Odoo to complete Client Pairing before the request expires.

Select **Deny request** to reject access. Expired requests need a new request
from Odoo. A link opens the review screen; approval always requires an explicit
decision. Changing the input disables the previous review's decision buttons.

Device Center reads requests and saves decisions through the authenticated
native Agent client. On Windows, it discovers the configured Agent Endpoint
through the protected MSIX bootstrap pipe. A link contains only the request ID.

## Architecture

The crate is organized by product feature:

- `app.rs` owns navigation, application-level actions, and the window shell;
- `features/` owns setup, overview, devices, activity, Client Pairing, and support views;
- `infrastructure/` owns the supervised client runtime, tray, activation, and
  platform integration;
- `ui/` is the Inari design system over GPUI Component;
- `dev/` is the development environment, compiled only under
  `debug_assertions`.

Mutable screen state lives in GPUI entities. Long-running network work belongs
to the owned Tokio runtime in `infrastructure/runtime.rs`, which cancels and
joins its tasks during shutdown.

The runtime retains the current connection state. Each window receives that
state when it subscribes, even if the Agent connection opened before the
window. Activity events use a separate live channel. A closed event stream
sets the connection to Reconnecting before the next connection attempt.

### Development environment

Debug builds carry a Bench and a set of devtools. A small launcher floats at the
bottom right of every window; it is the entry point, and it is deliberately
quiet until the pointer reaches it.

| Shortcut | What it opens |
| --- | --- |
| `cmd-alt-d` / `ctrl-alt-d` | the Bench: the story catalog and the stage |
| `cmd-alt-i` / `ctrl-alt-i` | the devtools panel on the active window |

The panel is docked in GPUI's own inspector strip, so it never covers what is
being judged, and GPUI's element picker works with it. Four tools share it:
Knobs, Element, Frames, and Stage. Knobs controls the Bench story. Frames reads
only the current window. The toolbar's outline mode applies to every window;
the selected element's box model appears in the window that owns the selection.

Add a story next to the component it previews. There is no central list:

```rust
crate::story! {
    id: "control.button",
    name: "Button",
    scope: crate::dev::Scope::Controls,
    about: "Every emphasis, with the reporting swap.",
    render: |dial, _window, _cx| {
        let disabled = dial.flag("Disabled", false);
        ...
    },
}
```

Each `dial` call declares a control, gives it a default, and returns its current
value. The panel shows the knobs the render actually read, in the order it read
them.

See the [development environment](../../docs/device-center-dev-environment.md)
for the controls and their behavior.

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

Device Center pins a GPUI 0.2.2 fork for GPU effects. The chrome uses native
window blur. Content surfaces use tonal washes, and floating overlays use a
denser tint. Authored WGSL effects use the fork's renderer contract. See the
[GPU effects architecture](../../docs/device-center-gpu-effects-architecture.md).

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
