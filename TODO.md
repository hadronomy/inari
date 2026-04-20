# TODO

## Standalone Security Hardening

Standalone mode is intentionally local-first and does not solve the same remote trust problem as managed enrollment. That said, there is still a meaningful future hardening track for standalone deployments that want stronger guarantees about which local clients are allowed to pair with and control the agent.

### Local bootstrap pairing secrets

- Add an explicit first-run pairing flow for standalone mode.
- Generate a short-lived local bootstrap secret that must be presented by the first trusted local client.
- Allow operators to rotate or revoke the pairing secret without reinstalling the agent.
- Persist pairing state in a way that survives restarts but remains local-machine scoped.
- Make the UX clear for the tray and other desktop clients so pairing feels intentional rather than incidental.

### Stricter origin and client attestation

- Tighten the local browser trust model beyond basic loopback + CORS.
- Evaluate origin-bound token issuance so the agent can distinguish between merely local requests and expected local application origins.
- Add stronger client identity signals where available, rather than relying only on bearer possession.
- Ensure the model remains practical for browser-based POS flows and does not become fragile under normal local development or service restarts.

### OS-user binding or local IPC-first auth

- Investigate OS-user-bound trust for standalone deployments where the agent and desktop client run under the same local user.
- Evaluate named-pipe or domain-socket style local auth for platforms where that gives a stronger local boundary than loopback HTTP alone.
- Determine whether some high-trust local workflows should prefer IPC transport over HTTP when both ends are native desktop components.
- Keep the abstraction clean so stronger local auth does not leak platform-specific complexity into the core runtime or API models.

### Optional mutual trust between the tray and the agent

- Add a stronger trust relationship between the tray and the local agent beyond today’s token bootstrap model.
- Evaluate tray-held local identity material or a mutually recognized local credential exchange.
- Make sure the tray can prove that it is an expected first-party desktop companion, not just another loopback client with a token.
- Keep this optional, because some standalone deployments will still want a simpler browser-only local model.

### Cross-cutting design constraints

- Do not weaken the clean distinction between standalone mode and managed mode.
- Keep standalone hardening local-first rather than turning it into a second managed-enrollment system.
- Preserve usability for single-terminal and low-admin environments.
- Keep the final model cross-platform, or make platform-specific strengthening explicitly additive rather than required.
