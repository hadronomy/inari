---
status: accepted
---

# Own the Odoo addon in Inari

The reusable `inari_devices` addon lives under `packages/odoo-inari/` in the
Inari repository. Inari publishes an immutable addon artifact, and deployment
repositories pin that artifact.

This ownership keeps protocol and addon changes in one review boundary. It also
keeps MZE deployment policy and site configuration outside the reusable
product.

Inari publishes the addon in a dedicated content-addressed OCI artifact. The
artifact declares its supported Inari contract range and refuses Device Work
when contract negotiation fails.

Additive contract changes stay within one contract major and use capability
negotiation. A breaking change increases the major and uses a coordinated
canary deployment without compatibility paths.

Odoo owns Client Pairing, Device Bindings, permissions, workflow routing, and
recovery. Device Center owns Agent enrollment, local certificate trust, service
readiness, operating-system service control, and local repair.

Decommission drains Device Work, resolves unknown outcomes, exports audit
records, revokes grants and credentials, and purges Device Spool content. It
removes the Inari Stock Addon before the core addon. Production addon removal
is blocked until this sequence finishes.
