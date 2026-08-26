---
status: accepted
---

# Use trusted local HTTPS for POS device work

An Odoo POS browser submits Local Device Work to a locally trusted HTTPS Agent
endpoint. Device Center provisions certificate trust, while browser Local
Network Access controls network reachability.

This boundary supports workstation and shared Agent Hosts without public Agent
exposure. Plain HTTP cannot provide the required endpoint identity for a shared
local network.

The Agent uses a separate local-server certificate from the Inari certificate
authority. The certificate has a 30-day lifetime and renews at two-thirds of
that lifetime. It does not reuse the Zenoh client certificate.

A workstation Agent uses a fixed loopback Agent Endpoint. A shared Agent
publishes a signed mDNS record. Device Center checks the record against the
Organization certificate authority.

Managed DNS provides discovery when the network blocks mDNS. The addon does
not accept arbitrary URLs or raw IP addresses because they bypass authenticated
discovery.

The browser selects the exact Agent from the Device Binding. The Agent identity
must match the bound Organization and Site. Discovery order and network
distance never select an Agent.
