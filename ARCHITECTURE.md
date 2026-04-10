# Custom Odoo IoT Alternative Architecture

## Goal

Replace the paid Odoo IoT dependency for the common Community stack with a self-hosted, upgrade-conscious local hardware bridge.

## Topology

```text
Odoo backend
  └─ POS SPA in browser
       └─ local HTTP agent on terminal machine (127.0.0.1:7310)
            ├─ receipt printer
            ├─ cash drawer
            ├─ barcode scanner plugin
            ├─ scale plugin
            └─ customer display plugin
```

## Design principles

- Odoo remains source-of-truth for business data and receipt payload generation.
- The browser talks to local hardware only through a loopback agent.
- Odoo-specific patches stay very thin.
- Hardware complexity lives in the agent behind explicit endpoints.
- Device integrations are plugin-oriented so the print bridge can grow into a true IoT replacement.

## Odoo responsibilities

- store per-POS terminal bridge settings in `pos.config`
- expose a small config endpoint for the POS frontend
- patch the POS receipt flow to redirect transport to the local agent
- provide diagnostics, test payloads, and optional server-side validation

## Agent responsibilities

- device enumeration and health
- receipt rendering to ESC/POS
- raw printer access and drawer pulse
- optional HTML passthrough printing
- future device plugins for scanners/scales/displays
- machine-readable errors and local rotating logs

## Device plugin contract

Every device plugin should eventually expose:

- `health()`
- `capabilities()`
- `list_devices()`
- `execute(command, payload)`

That lets you add kitchen printers, serial scales, HID scanners, and pole/customer displays without changing the Odoo addon boundary.

## Security model

- agent binds to loopback by default
- explicit CORS allowlist for your Odoo origin
- no printing through Odoo controllers
- no direct server-to-printer assumptions
- optional shared secret can be added later if you want multi-host deployments

## Upgrade posture

The frontend extension follows the documented Odoo 19 approach:

- native JavaScript modules in asset bundles
- patching existing classes instead of replacing them wholesale
- POS remains a browser app and the receipt screen remains the integration point
