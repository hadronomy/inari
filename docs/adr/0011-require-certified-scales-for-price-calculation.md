---
status: accepted
---

# Require Certified Scales for price calculation

Only a Certified Scale can supply weight for an Odoo price calculation. The
Device Binding records the certification identity and operating jurisdiction.

This rule prevents a technically compatible scale from entering a regulated
retail workflow before its Device and Driver combination receives legal
approval.

Manual Weight is disabled by default. Company policy and a dedicated user
permission must both allow it. Its content-free audit record identifies the
input as manual and never presents it as a Scale Reading.

The Controller owns the Certification Record for each approved device model,
Driver, jurisdiction, certificate identity, and expiry date. Odoo receives a
read-only Projection.

The operator interface warns 30 days before expiry. An expired Certification
Record blocks the scale from price calculations.

The Agent supplies exact gross Scale Readings. The Odoo POS owns tare and net
calculation. A scale-screen subscription supplies at most two ephemeral
readings each second.

Odoo accepts a reading only from the active Binding Revision and current
Certification Record. The reading must be stable, in range, newer than the
last sequence, at most one second old, and expressed in a compatible UCUM
unit.

Odoo calculates the exact decimal net weight and requires a positive value
within the certified range before it calls the native weighed-product seam.

A reading gap longer than one second marks the scale stale. Odoo requires two
new stable readings within one resolution before it accepts a weight.

Lease loss, a Binding Revision change, or stale data also invalidates the
reading. The operator can Retry, enter a permitted Manual Weight, or remove the
product. The system never selects Manual Weight without an operator action.

Odoo keeps one live tab for each POS session. The Agent owns a time-limited
Scale Lease across valid sessions and auxiliary browser contexts.

The Transport Leader sends notifications through a scoped `BroadcastChannel`.
The channel does not enforce exclusivity and contains no Odoo access token.

The Agent grants one 10-second Transport Leader Lease for the exact pairing,
Agent, POS configuration, and Authorization Digest. The leader renews every
three seconds. A suspended context loses the lease and its successor reconciles
sequence before processing input.

Each Scale Reading includes the Agent Boot Identity, Subscription Identity,
Client Grant, Binding Revision, and Agent monotonic time. The Agent signs each
reading that can cross the browser channel.

The browser channel carries hints. The POS checks the signed envelope before a
reading can change an order.

The first release permits no lease stealing. A second register can try again
after the owner lease expires.

The scale seam accepts only signed decimal readings with current certification.
The scanner seam owns the Agent subscription while keyboard scanning stays
native. The drawer seam awaits a separate Drawer Intent. A Retry reconciles the
prior intent. Manual Weight has a separate permission and audit record.
