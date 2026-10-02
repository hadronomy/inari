---
status: accepted
---

# Use Inari-native hybrid device integration

The Inari Odoo Addon uses Inari contracts as its canonical device boundary.
Operator sessions submit Local Device Work to an Agent. The Odoo backend submits
Managed Device Work through the Controller.

The addon preserves Odoo workflows through Device Adapters. Native Device Paths
remain available until Inari has a tested Device Adapter for each device
contract.

We rejected full Odoo IoT Box emulation because the complete Enterprise
contract is private and its public local routes use weaker trust assumptions.
We rejected a server-only path because it cannot preserve local POS work during
Controller or wide-area network failures.

The addon depends on Odoo `point_of_sale` and `mail`. It receives Community
`iot_base` indirectly through `point_of_sale`. The addon has no direct
dependency on `iot`, `iot_base`, `pos_iot`, or another Enterprise module.

Community 19 is the first Conformance Target. Installation stays blocked while
Enterprise `iot` or `pos_iot` is installed or changing installation state.
Later Enterprise activation stays blocked while Inari is installed. A future
licensed Conformance Target still requires one integration at a time.
