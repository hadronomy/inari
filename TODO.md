# TODO

## Standalone Security Hardening

Status: implemented for the HTTP/tray path. IPC remains intentionally deferred.

Standalone mode is intentionally local-first and does not solve the same remote trust problem as managed enrollment. That said, there is still a meaningful future hardening track for standalone deployments that want stronger guarantees about which local clients are allowed to pair with and control the agent.

### Local bootstrap pairing secrets

- Add an explicit first-run pairing flow for standalone mode.
- Generate a short-lived local bootstrap secret that must be presented by the first trusted local client.
- Allow operators to rotate or revoke the pairing secret without reinstalling the agent.
- Persist pairing state in a way that survives restarts but remains local-machine scoped.
- Make the UX clear for the tray and other desktop clients so pairing feels intentional rather than incidental.

Implemented as:

- `/auth/pairing/start`, `/auth/pairing/complete`, `/auth/pairing/rotate`, and `/auth/pairing/revoke`.
- Short-lived pairing secrets persisted only as hashes in the local trust store.
- Trusted local clients persisted in the existing local secret-store stack.
- Tray-managed launches pass a one-time bootstrap pairing secret through the agent process environment.

### Stricter origin and client attestation

- Tighten the local browser trust model beyond basic loopback + CORS.
- Evaluate origin-bound token issuance so the agent can distinguish between merely local requests and expected local application origins.
- Add stronger client identity signals where available, rather than relying only on bearer possession.
- Ensure the model remains practical for browser-based POS flows and does not become fragile under normal local development or service restarts.

Implemented as:

- `/auth/local-challenge` signed challenge issuance.
- Local token requests can carry a signed paired-client attestation.
- Browser origins can be bound to paired clients and checked against configured trusted origins.

### OS-user binding or local IPC-first auth

- Investigate OS-user-bound trust for standalone deployments where the agent and desktop client run under the same local user.
- Evaluate named-pipe or domain-socket style local auth for platforms where that gives a stronger local boundary than loopback HTTP alone.
- Determine whether some high-trust local workflows should prefer IPC transport over HTTP when both ends are native desktop components.
- Keep the abstraction clean so stronger local auth does not leak platform-specific complexity into the core runtime or API models.

Deferred deliberately:

- The local trust model now has a clean service boundary where IPC can be added later.
- No named-pipe/domain-socket transport was implemented in this pass.

### Optional mutual trust between the tray and the agent

- Add a stronger trust relationship between the tray and the local agent beyond today’s token bootstrap model.
- Evaluate tray-held local identity material or a mutually recognized local credential exchange.
- Make sure the tray can prove that it is an expected first-party desktop companion, not just another loopback client with a token.
- Keep this optional, because some standalone deployments will still want a simpler browser-only local model.

Implemented as:

- The tray now owns a persistent Ed25519 local identity.
- The tray signs local trust challenges before token issuance.
- The agent records the tray as a paired native client.
- The tray can still fall back to an explicit local pairing start when it is not the process that launched the agent.

### Cross-cutting design constraints

- Do not weaken the clean distinction between standalone mode and managed mode.
- Keep standalone hardening local-first rather than turning it into a second managed-enrollment system.
- Preserve usability for single-terminal and low-admin environments.
- Keep the final model cross-platform, or make platform-specific strengthening explicitly additive rather than required.
