# Inari controller-optional connectivity

> Supporting research. The [connectivity reference](connectivity/README.md)
> defines the consolidated target. Follow its requirements and implementation
> stages. Candidate rankings and rollout orders here are historical evidence.

**Checked:** 2026-10-08

This note uses official product documentation and primary repositories. It assesses a generic peer transport for Inari. The transport must work with several integrations at the same time. No integration-specific service, hosted SaaS controller, or Odoo-specific component is required.

## Conclusion

Use **Iroh as the default network transport candidate** for the planned Rust Agent. Keep **Tailcat as the serious alternative**. Device Center uses protected local IPC, with iceoryx2 or a similar library. Both network candidates can operate without a Tailscale account or an Inari Controller. Both need relays for peers that cannot connect directly. Neither defines Inari pairing, Client Grants, or the device contract.

The correct boundary is:

```text
integration (browser, desktop, CLI, ERP adapter, future client)
        ↓
generic Inari device protocol
        ↓
transport (Iroh or Tailcat)
        ↓
direct UDP when possible; relay when required
```

The Agent can be its own authority. It keeps a durable transport identity, a local peer trust store, and Client Grants. A Controller can later manage delegated policy, but the transport must not require one. Signed capabilities are an optional credential format, not a prerequisite for local authorization.

The transport only provides authenticated peers, streams or datagrams, path discovery, and connection lifecycle. The generic device protocol still defines command IDs, capabilities, expiry, replay protection, status, events, and idempotency. A gateway is an optional protocol adapter. It is not a required system component.

## Ranking

| Rank | Candidate | Controller-free fit | Main limitation |
| --- | --- | --- | --- |
| 1 | **Iroh** | Native fit for the planned Rust Agent | Application authorization and the device contract remain ours. Python bindings support external clients and the transition. |
| 2 | **Tailcat** | Strong persistent alternative | Go embedding, no API stability promise, and no official Python or Rust binding. |
| 3 | **OpenZiti** | Embeddable policy-aware network | Adds its own controller, routers, PKI, and policy model. |
| 4 | **Tailscale `tsnet` / Headscale** | Mature transport | `tsnet` is Go; managed or self-hosted coordination remains. |
| 5 | **NetBird** | Turnkey WireGuard overlay | Requires an installed privileged client and four network services. |
| 6 | **Cloudflare Tunnel** | Good outbound HTTP path | Cloudflare account and `cloudflared`; not a generic peer transport. |
| 7 | **zrok / Pangolin / Hyperswarm** | Useful in narrower products | Connector, controller, or JavaScript/DHT assumptions do not fit the core boundary. |

OpenTunnel is a useful pairing UX reference. It is not a persistent transport candidate.

## The strongest candidates

### Iroh

[Iroh](https://github.com/n0-computer/iroh) addresses peers by public key. It finds a direct path when possible and falls back to QUIC relays. QUIC provides encrypted streams and datagrams. The Rust project includes a self-hostable [`iroh-relay`](https://github.com/n0-computer/iroh/tree/main/iroh-relay). It does not require a Tailscale-style control plane.

Iroh has a native Rust API for the planned Agent and Rust network clients. The current Python Agent and future Python clients can use the official [`iroh-ffi`](https://github.com/n0-computer/iroh-ffi) project. Device Center uses protected local IPC. The FFI exposes endpoints, streams, datagrams, endpoint IDs, custom relays, and watchers. Its [Python metadata](https://github.com/n0-computer/iroh-ffi/blob/main/pyproject.toml) marks it Alpha. The current [PyPI release](https://pypi.org/project/iroh/) lists wheels for Windows x86-64, Linux glibc 2.28 x86-64 and ARM64, and macOS ARM64. It does not list Windows ARM, Linux musl, or macOS Intel wheels. Verify the target matrix before packaging.

Iroh does not decide which peer belongs to which Agent. Its [endpoint hooks](https://docs.iroh.computer/connecting/endpoint-hooks) can reject peers after the handshake, before application data. Use that hook to check the peer key. The Agent must also check Client Grants for each operation. Revocation removes the peer from its local trust store and closes its active connection.

Browser use has a clear limit. Iroh's [browser build](https://docs.iroh.computer/languages/wasm-browser) cannot send UDP from the browser sandbox, so browser connections use a relay. Native and server clients can use direct paths and relay fallback. This gives one generic protocol with two path classes.

Do not use Iroh's [public relays](https://docs.iroh.computer/iroh-services/relays/public) for production. They have no SLA, shared access, rate limits, and expose connection metadata. Run a small self-hosted relay fleet, or use authenticated managed relays only when that vendor dependency is acceptable. The relay forwards encrypted traffic; it is not the authority for a tenant, peer, command, or device.

### Tailcat

[Tailcat](https://github.com/tailscale/tailcat) is a control-plane-free Go library and CLI. It uses userspace WireGuard, magicsock, direct UDP, and DERP fallback without a Tailscale account. It does not require a TUN device or OS route changes. A custom DERP server can be embedded in the address; the [installation guide](https://github.com/tailscale/tailcat/blob/main/INSTALL.md) documents Windows amd64 and arm64 binaries and browser builds from Go/WASM.

The current Go API supports persistent operation. [`Server.Key` and `Server.PresharedKey`](https://raw.githubusercontent.com/tailscale/tailcat/main/tailcat.go) must be restored together so the Tailcat address remains usable across restarts. `Client.Key` gives each integration a stable node identity. The server accepts several clients through `AllowClient(key.NodePublic)` or a local `KeySet`. Revocation first rejects the key, then calls `DisconnectClient` to remove its routes. Existing TCP connections stall or time out; that call does not reset them. The application must reject further Device Work immediately.

The address contains the server public keys, relay information, and, by default, a WireGuard pre-shared key. Treat it as a secret capability. A persistent deployment needs protected keys, a configured relay, approved-peer admission, Client Grants, and local audit. Tailcat makes no stability promise for its Go API, CLI, or wire format. Keep that dependency behind Inari's transport boundary.

There is no official Python or Rust Tailcat binding. The independent [`pytailcat`](https://pypi.org/project/pytailcat/) package wraps bundled Tailcat binaries and exposes a Python process API. Its source is [a separate repository](https://github.com/joseluisfalcon/pytailcat); PyPI lists one maintainer and its 0.1.4 upload was not Trusted Publishing. It lists Windows x86-64, Linux x86-64/ARM64, and macOS Intel/ARM64 wheels. Treat it as an unverified wrapper. Pin and audit the binary before use. A packaged Go helper is another option for the Python Agent.

Tailcat's [browser demo](https://github.com/tailscale/tailcat/blob/main/README.md) is relay-only. This matches the Iroh browser constraint. Native and server integrations can use direct paths.

## OpenTunnel

The exact candidate is [akoenig/opentunnel](https://github.com/akoenig/opentunnel). Its current `main` README is **v2 Beta** at `beta.opentunnel.sh`; the apex domain still serves 1.x and the [1.x branch](https://github.com/akoenig/opentunnel/tree/1.x) contains the older relay implementation. V2 is Bash plus a pinned Tailcat build. It uses claim-then-pin, ephemeral keys, expiry, idle limits, and a short-lived command session. It is designed to leave no standing access.

OpenTunnel is useful as a model for a pairing flow, not as Inari's transport or device protocol. Its claim race, bearer address, temporary key lifetime, and command wrapper do not provide persistent peer membership. Its [security model](https://opentunnel.sh/concepts/security-model/) describes the same beta assumptions. Any evaluation must pin the exact branch and Tailcat commit. Do not infer v2 behavior from the older 1.x service.

## Other stacks

**OpenZiti.** [OpenZiti](https://github.com/openziti/ziti) has application SDKs, including Python, and policy-aware identities. Its controller manages identities, services, certificates, and policies. Edge routers carry the data plane. This is a complete network product, but it adds a controller and PKI. It does not meet the no-required-controller goal.

**Tailscale and Headscale.** [tsnet](https://tailscale.com/docs/features/tsnet) embeds a Tailscale node in Go and still needs an auth URL or key. [Headscale](https://github.com/juanfont/headscale) removes the managed service but leaves a coordination server to operate. Use these only if a mesh control plane is acceptable.

**NetBird.** [NetBird](https://docs.netbird.io/about-netbird/how-netbird-works) uses WireGuard with Management, Signal, Relay, and STUN services. Each peer runs an installed client with network privileges. It is an overlay product, not an embedded transport.

**Cloudflare Tunnel.** [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/) is useful for an outbound HTTP endpoint. It requires `cloudflared` and a Cloudflare account. It does not give Inari a generic peer protocol or controller-free identity model.

**zrok, Pangolin, and Hyperswarm.** [zrok](https://github.com/openziti/zrok) and [Pangolin](https://github.com/fosrl/pangolin) add share/control services and connectors. [Hyperswarm](https://github.com/holepunchto/hyperswarm) is a JavaScript topic/DHT stack. None matches the Python/Rust runtime boundary and local authorization model as well as Iroh or Tailcat.

## Controller-free pairing model

The smallest design that supports several interchangeable clients is:

1. The Agent creates a durable transport identity on first start.
2. An Agent Administrator pairs a client through Device Center or an authenticated local interface. The client proves possession of its own key.
3. The pairing exchange binds that client key to the Agent and the approved request. Consume its short-lived nonce once.
4. The Agent stores the Client Pairing and Client Grants locally. It checks the peer key at transport admission and checks Client Grants before each operation.
5. Revocation rejects further Device Work and reconnects. Close application sessions and remove transport routes. Enforce grant expiry and request replay checks separately.
6. A Controller, if added later, uses an explicitly approved delegation policy. Default pairing does not depend on it.

Signed capabilities can support delegated or portable credentials if required. A local trust store does not need that extra credential format to enforce permissions.

This model keeps browser, native, desktop, CLI, and future integrations as peers. It also keeps device semantics above the transport. A gateway can implement one integration without becoming the system's network authority.

## Proof sequence

1. Define the generic device contract. Use Odoo and a plain reference integration at the same time. Validate local IPC separately from network clients.
2. Implement Agent-owned Client Pairings and Client Grants. Test restart, grant expiry, active revocation, reconnect, and replay.
3. Run the same protocol over Iroh and Tailcat. Compare direct UDP, relay fallback, cold start, reconnect, browser relay behavior, resource use, and operational burden.
4. Verify Iroh FFI wheels and Tailcat packaging on every supported OS. Treat Tailcat's unstable Go API and `pytailcat` provenance as explicit risks.
5. Keep the existing Zenoh gateway as an optional transport adapter during the trial. Remove it only after Iroh or Tailcat covers the same protocol, policy, reconnect, and offline behavior without a required controller.
