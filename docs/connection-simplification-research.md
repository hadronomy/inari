# Connections without a required Controller

> Supporting research. The [connectivity reference](connectivity/README.md)
> defines the consolidated target. Follow its requirements and implementation
> stages. Candidate rankings and rollout orders here are historical evidence.

Research date: 2026-10-08.

## Recommendation

Make the Agent the authority for its Devices and approved integrations. Give every integration the same device contract and its own Client Pairing and Client Grants. Several integrations must work at the same time.

Use protected local IPC between Device Center and the Agent. Evaluate iceoryx2 or another local IPC library for that boundary. Evaluate **Iroh and Tailcat** for network integrations. Iroh is the leading embedded transport candidate. Tailcat is a serious alternative because it can carry a TCP interface.

The target Agent is Rust. Python is the current implementation and remains a supported integration language. Its FFI packaging must not define the long-term core architecture. Pairing is an Inari authorization workflow. It can run over Iroh, but it is separate from Iroh's authenticated connection establishment.

The default product needs no Inari Controller, PostgreSQL, OIDC service, Zenoh Router, or step-ca deployment. Remote connections still need relay infrastructure for networks that prevent direct connections. Inari can operate that infrastructure without making it an application Controller.

This is a proposed architecture. It changes current boundaries and contracts. No runtime or contract change accompanies this research.

## User requirements

- No required Controller.
- Independent integrations that can work at the same time.
- No Odoo-specific requirements in the Agent's public device contract.
- Minimal installation and pairing steps.
- Durable Device Work, precise Output Evidence, and local operation during network failure.

The recommendation follows these requirements. A hosted fleet Controller is an optional product, not the default prerequisite.

## Evidence from Inari

The active checkout is `feat/config-explain-provenance`, at `ff7f3132274b415b533e5f7013d487de5bb242d4`. It predates recent integration changes. I also read `main`, at `a1c04f5b94e05aaab8f25d77a01f7372e0514e20`, through Git. These revisions must not be treated as the same implementation.

The [existing architecture](../ARCHITECTURE.md) puts hardware execution, Drivers, and durable storage in the Agent. It describes the Controller as optional. This is a useful foundation for the proposed default.

In the active checkout, [SetupSnapshot conversion](../crates/inari-agent-client/src/model.rs) requires a managed-onboarding completion timestamp. [Device Center](../crates/inari-device-center/src/app/runtime.rs) does not load operational data before setup completes. [Local trust](../packages/agent/inari/security/local_trust/service.py) also restricts pairing to standalone mode. These are code-reading observations, not clean-install test results.

Recent `main` has stronger integration contracts, but it embeds Odoo concepts in core authorization:

| Current boundary | Coupling found | Proposed boundary |
| --- | --- | --- |
| `client_trust.models.BusinessScope` | Required `database` and `company_id`, plus `pos_configuration_id` | Client identity and Agent-owned permissions |
| `device_authority.models.AuthorityScope` | Odoo database and POS configuration semantics | Device and Device Capability authority |
| Browser pairing | Local approval followed by an external Pairing Assertion | Agent-approved key binding, with optional delegated identity |
| Browser authentication configuration | One Odoo assertion issuer and signing-key configuration | Independent trust for each approved integration |
| Device binding and report routing | Application workflow identities affect shared authorization | Device Adapter translates the application workflow |

Sources: [`BusinessScope`](https://github.com/hadronomy/inari/blob/a1c04f5b94e05aaab8f25d77a01f7372e0514e20/packages/agent/inari/client_trust/models.py), [`AuthorityScope`](https://github.com/hadronomy/inari/blob/a1c04f5b94e05aaab8f25d77a01f7372e0514e20/packages/agent/inari/device_authority/models.py), [pairing service](https://github.com/hadronomy/inari/blob/a1c04f5b94e05aaab8f25d77a01f7372e0514e20/packages/agent/inari/client_trust/service.py), [configuration](https://github.com/hadronomy/inari/blob/a1c04f5b94e05aaab8f25d77a01f7372e0514e20/packages/agent/inari/config.py).

The `main` [CONTEXT.md](https://github.com/hadronomy/inari/blob/a1c04f5b94e05aaab8f25d77a01f7372e0514e20/CONTEXT.md) explicitly defines Client Pairing, Pairing Assertion, Organization, and Device Binding through Odoo. A future implementation must update that glossary with the contracts. Merely renaming fields leaves the dependency intact.

## Target system

Several business applications connect to one Agent. Each application has an independent approved identity. The Agent enforces its Client Grants before any Device Work reaches a Driver.

```text
Odoo Device Adapter ───┐
Other application ─────┼── same device contract ──► Agent ──► Drivers ──► Devices
Automation client ────┘                             │
                                                  ├─ Client Pairings and Grants
                                                  ├─ durable Device Work
                                                  ├─ events and Output Evidence
                                                  └─ protected local IPC
                                                       └─ Device Center for approval

Other computers: direct encrypted path where possible, relay path otherwise.
Device Center and native local clients: protected local IPC.
```

Device Center remains a user-session client. The Agent remains the service. The installer provisions local access through an authenticated IPC boundary. Device Center does not need the external integration QR pairing flow. The Agent still enforces local administrative permissions. A network relay forwards encrypted traffic. It does not approve Client Grants, interpret Device Work, or become the Device authority.

A future fleet Controller can use the same device contract as an authorized client. Fleet administration can add delegated policy when required. Default pairing does not depend on that service.

## A generic device contract

The common interface needs a small, complete set of operations:

- Identify the Agent and list permitted Devices.
- Advertise Device Capabilities and Contract Majors.
- Submit Device Work with an Idempotency Key and an optional execution deadline.
- Read execution state and subscribe to permitted events.
- Cancel eligible work and revoke a Client Grant.

The contract carries device facts: `device_id`, Device Capability, media type, bounds, execution state, and Output Evidence. It does not require an Odoo database, company, POS configuration, order, or report action.

The Odoo Device Adapter owns business rules and rendering. It translates the approved workflow into the common contract. Other integrations perform the same translation for their own workflows.

Odoo-specific Print Origins and Device Bindings remain in the Odoo integration. Optional external references can be opaque metadata. The Agent must not interpret that metadata to grant access.

Capability Negotiation must stay explicit. Interchangeable integrations use supported Contract Majors and Device Capabilities. They cannot assume that all Devices support the same actions.

The local IPC interface and network transport must call the same application services. Both enforce permissions for their authenticated principal. They do not require identical credentials. Two transports must not create two Device Work models. Shared-memory messages and network messages can use different encodings of the same contract.

## Pairing and simultaneous use

The Agent stores one Client Pairing for each approved client key. Each Client Grant binds that key to permitted Devices, Device Capabilities, expiry, and resource limits.

Proposed default flow:

1. Install Inari and discover local Devices.
2. Select **Connect Inari** in an integration, or **Add integration** in Device Center.
3. Exchange a short-lived pairing link or QR code.
4. Approve the client and its requested Devices and Device Capabilities in Device Center.
5. Run a Device Test.

The pairing exchange binds the proposed client key and target Agent. Possession of a pairing link does not become a permanent Client Grant. A displayed application name is not proof of identity. A confirmation code can bind the two visible pairing screens.

Each integration keeps its own Client Grants. Adding another integration does not replace the first one. Revoking one integration closes its sessions and prevents further Device Work without affecting the others.

The Agent remains the final permission boundary. An integration's identity provider can authenticate its users, but a claim from that provider cannot expand an Agent-approved Client Grant.

For enterprise delegation, an Agent Administrator can approve a particular issuer and a bounded delegation policy. That is optional. The default flow does not require OIDC, application registration in a central service, or an Odoo-signed assertion.

Use existing key protection, pairing proof, replay checks, and permission models where they fit. Reuse a maintained credential library for any new signed grant format. Do not invent cryptography.

### Concurrent clients need explicit isolation

Scope Idempotency Keys to the approved client identity. Two integrations can submit the same external key without sharing execution records. A Retry from the same client must resolve to the same accepted work.

Shared Devices use the Agent's existing queue. Exclusive Devices need Agent-owned leases tied to Client Grants and sessions. This generalizes the current Scale Lease without embedding a POS configuration in the device runtime.

Event subscriptions and execution records need access filters. One client cannot read another client's content or cancel its work without an explicit permission. Integration labels do not replace those checks.

Apply queue and bandwidth limits per client so one busy integration cannot prevent another from working. Device safety and operator control take priority over unrestricted concurrency.

## Technology choice

The [candidate research](inari-connectivity-alternatives-research.md) contains the primary-source comparison.

| Technology | Result for this requirement |
| --- | --- |
| **Iroh** | Leading embedded transport candidate. Public-key endpoints, QUIC, direct paths, and relays fit independent clients. |
| **Tailcat** | Strong alternative. Userspace TCP connections can preserve an HTTP boundary. Go embedding or a packaged helper adds an integration cost. |
| **OpenTunnel** | Temporary remote command product. Its pairing UX is a reference, but it does not supply the device contract. |
| **Tailscale / NetBird / OpenZiti** | Useful private-network products. Their accounts, policy services, or Controllers add prerequisites to the default product. |
| **Pangolin / Cloudflare Tunnel / zrok** | Useful service exposure. They add another connector and access platform. |
| **NATS / MQTT broker** | Useful central messaging. They do not meet the primary goal of direct use without a required central service. |
| **Existing Zenoh** | Keep during evaluation. Its current managed contract requires a Controller. No transport migration is justified before the generic boundary works. |

### Why Iroh leads

Iroh provides authenticated, encrypted connections identified by public keys. It tries direct paths and uses relays when necessary. Its documentation lists Rust and Python interfaces. [Introduction](https://docs.iroh.computer/), [language index](https://docs.iroh.computer/llms.txt)

Iroh hooks can reject a peer after its handshake and before application traffic. The Agent can use its local Client Pairings for admission. Device and operation permissions still need checks on each request. [Endpoint hooks](https://docs.iroh.computer/connecting/endpoint-hooks)

This is a useful basis for connections without private per-client certificates. It does not provide Inari's device API, Client Grants, leases, durability, or audit model. Removing step-ca from this path requires a deliberate change to the trust contract.

The Rust Agent uses Iroh's native crate. Python bindings serve the current Agent during the transition and future Python clients. Validate each shipped operating-system target. Do not select the long-term transport solely around the current Python package.

### Why Tailcat remains a serious alternative

Tailcat uses WireGuard, NAT traversal, and DERP without a Tailscale account or control plane. It offers a Go library and TCP port forwarding. Inari can exchange connection information through its own pairing flow. A Go helper remains an extra integration boundary for a Rust Agent. [Official README](https://github.com/tailscale/tailcat)

The Agent still needs key persistence, approved-peer admission, reconnect handling, and per-request Client Grants. Those obligations do not disqualify Tailcat. They are also necessary with Iroh. A Go helper can be simpler than a new native stream protocol if it preserves the existing interface.

A fair experiment compares persistent multi-client sessions on both candidates. A short-lived file-transfer demonstration is insufficient.

## Browser and relay limits

Current Iroh browser connections always use a relay. Its browser build needs a Rust/Wasm wrapper rather than an official bundled npm package. A generic browser SDK therefore needs real integration work. [Browser documentation](https://docs.iroh.computer/languages/wasm-browser)

Tailcat also documents a browser demo whose traffic uses DERP. Neither current browser path guarantees direct LAN access. Device Center uses local IPC. Browsers need a supported network interface or an explicitly designed native bridge. [Tailcat browser demo](https://github.com/tailscale/tailcat#tailcat)

Iroh's public relays target development and hobby use. They have rate limits and no SLA or version-lock guarantee. Production needs managed or self-hosted relays. [Relay policy](https://docs.iroh.computer/iroh-services/relays/public)

No required Controller is feasible. No external infrastructure for every possible WAN connection is not a supportable promise. Networks can prevent direct connections, so a reliable remote product needs a relay path.

The Agent owns revocation. A remote administrator cannot instantly revoke a grant on an unreachable Agent. Short expiry bounds that delay. A fleet service can distribute policy updates later if that product is required.

A relay does not store pending Device Work. If an Agent is unreachable, the submitting integration must retain unaccepted work or report that delivery is unavailable. The SDK needs explicit deadlines and Job Reconciliation after uncertain responses.

## What belongs where

| Agent owns | Integration owns | Optional infrastructure owns |
| --- | --- | --- |
| Device identity, Drivers, and Device Capabilities | Business workflow and external identifiers | Encrypted packet forwarding |
| Client Pairings and bounded Client Grants | User authentication within the application | Optional endpoint lookup |
| Device queue and exclusive leases | Document rendering and workflow routing | Optional fleet administration |
| Execution state and Output Evidence | Retention of work before Agent acceptance | Optional central audit collection |
| Local audit and revocation | Translation into the common device contract | Relay abuse controls and availability |

Driver Profiles and certification evidence stay device-specific. They cannot become optional merely because the application Controller is optional. Their verification can use locally installed trust or bounded delegated trust without Odoo business scope.

## Validation and delivery order

First, define the generic contract and Agent-owned Client Grants. Prove that two integrations can pair and use it concurrently. One integration must be a plain reference client with no Odoo concepts. The existing local HTTP interface can validate domain changes during the experiment. It is not the target Device Center transport.

Then compare Iroh and Tailcat against that working contract. Validate protected local IPC between Device Center and the service. Do not turn the desktop application into the transport service.

The experiment must cover:

- Independent grants for three concurrent clients, with isolated event and execution records.
- Revocation of one client without interruption to the other clients.
- A Shared Device queue and an Exclusive Device lease.
- Restart, key persistence, grant expiry, and recovery from lost responses.
- LAN, CGNAT, symmetric NAT, blocked UDP, HTTP proxies, and TLS inspection.
- A working browser client and production relay behavior.
- Windows service packaging, macOS, Linux, updates, and protected key storage.
- Large documents, queue limits, and relay operating cost.

Measure time to the first Device Test, manual fields, administrative actions, connection failures, latency, and memory. These measurements decide the transport. This research does not contain a benchmark.

Preserve Output Evidence and Outcome Unknown. A transport acknowledgement does not prove physical execution. A Retry after uncertain Device I/O must not silently request another physical copy.

An adopted change must update the glossary, protocol, Agent, SDKs, Device Adapters, persistent structures, deployment, and relevant tests together. Remove obsolete Odoo authority requirements from the generic boundary. The Odoo Device Adapter must migrate to the same contract as every other integration.

The current managed Zenoh path can remain during the experiment. Once the new default works, a separate decision can remove it or retain it for optional fleet use. Do not stack a permanent peer tunnel around the old required Controller architecture.

## Research limits

The [pairing and SDK follow-up](pairing-and-sdk-technologies-research.md)
explains Client and Agent roles, the Iroh deployment, and tools that reduce
pairing friction across Rust, Python, and browser integrations.

The evidence comes from official documentation, project repositories, and local Git source. The proposed permission boundary and candidate ranking are engineering judgments. No clean-install test, transport benchmark, or cost estimate was performed.

Only research documents changed. Runtime code, contracts, dependencies, and release behavior remain unchanged.
