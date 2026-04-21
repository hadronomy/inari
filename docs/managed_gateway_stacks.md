# Managed Gateway Deployment Stacks

This guide describes the managed upstream deployment stacks that `Inari` supports today.

Use this together with [gateway_protocol.md](./gateway_protocol.md).

## Scope

This document is about the **managed upstream boundary**:

- `Inari` enrollment into a controller over `HTTPS`
- the steady-state managed data plane over `Zenoh`
- managed client certificates issued by `step-ca`
- optional enrollment authorization through `ZITADEL`
- optional `Caddy` in front of the controller's HTTP enrollment surface

It does **not** describe the local desktop or POS-facing boundary. The local tray and local browser clients still talk to the loopback agent over the authenticated local HTTP and WebSocket APIs.

## Design Overview

The recommended production shape is:

```text
                                   optional enrollment auth
Inari  ---- HTTPS enrollment ----> Controller API ----------------------> ZITADEL
  |                                  |
  |                                  | issues enrollment decision and, when configured,
  |                                  | returns certificate enrollment + Zenoh contract
  |
  |---- provider-specific certificate enrollment -----------> step-ca
  |---- client cert issue / renew --------------------------> step-ca
  |
  |==== Zenoh TLS + mTLS data plane ========================> Controller / Zenoh Router
```

Two deployment points are worth making explicit:

- The **agent always connects outward**. Managed mode does not require inbound connections opened toward the agent host.
- The controller service and the Zenoh router are commonly intended to **coexist on the same server boundary**. They are different responsibilities, but they are expected to live together cleanly in the production deployment.

## What Moves Over Which Boundary

### Enrollment Boundary

Enrollment stays on `HTTPS`.

That gives us:

- a clean first-contact path before a managed client certificate exists
- a natural place for controller-issued enrollment tokens
- a natural place for ZITADEL-backed enrollment authorization
- a natural place for certificate enrollment delivery

### Managed Data Plane

Steady-state managed traffic moves over `Zenoh`.

That includes:

- status publication
- controller-to-agent command delivery
- runtime event publication
- reconnect recovery and command-history replay
- liveliness / presence

### Local Desktop Boundary

The local tray is **not** part of the managed data plane.

It continues to use:

- local loopback `HTTP`
- local authenticated WebSocket events

That separation is intentional. The tray is a local desktop client, not a controller-side managed client.

## Supported Stacks

### 1. Controller Enrollment Token + step-ca

Use this when the controller owns enrollment policy directly and also wants to require mTLS after certificate issuance.

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_AUTH_MODE=controller
INARI_UPSTREAM_ENROLLMENT_TOKEN=replace-me
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
```

In this mode:

- enrollment is authorized by the controller-issued `enrollment_token`
- the controller returns the Zenoh data-plane contract
- the controller returns `step-ca` enrollment material when managed certificates are enabled
- the agent issues its first client certificate from `/1.0/sign`
- the agent later renews through `/1.0/renew`

This is the simplest serious managed production stack.

### 2. ZITADEL Enrollment Auth + step-ca

Use this when enrollment authorization is delegated to `ZITADEL`, but the controller still owns the managed data-plane contract.

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_AUTH_MODE=zitadel_service_account
INARI_ZITADEL_BASE_URL=https://zitadel.example.com
INARI_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
INARI_ZITADEL_REQUESTED_SCOPES=openid,events:read,jobs:create,commands:execute
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
```

In this mode:

- the agent signs a private-key JWT assertion with the configured ZITADEL service-account key
- the agent exchanges that assertion for an OAuth access token
- the controller accepts that enrollment call and still returns the managed Zenoh + certificate contract
- step-ca remains the certificate authority for the managed client certificate

This is the cleanest stack when enrollment auth belongs to a central identity layer rather than the controller itself.

### 3. Controller Enrollment Token + Controller-Installed Certificate

Use this when the controller installs and rotates the managed client certificate directly instead of delegating issuance to `step-ca`.

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_AUTH_MODE=controller
INARI_UPSTREAM_ENROLLMENT_TOKEN=replace-me
INARI_UPSTREAM_CERTIFICATE_MODE=controller
```

In this mode:

- enrollment is still controller-authorized
- the controller still returns the Zenoh data-plane configuration
- the controller may install client-certificate material directly in the enrollment response

This is simpler than `step-ca`, but less flexible for large fleets and less elegant for short-lived certificate rotation.

## Caddy and the HTTP Enrollment Edge

`Caddy` is relevant to the **HTTP enrollment surface**, not the steady-state Zenoh data plane.

Example:

```env
INARI_UPSTREAM_EDGE_PROVIDER=caddy
INARI_UPSTREAM_MUTUAL_TLS_MODE=optional
```

Recommended post-issuance posture:

```env
INARI_UPSTREAM_EDGE_PROVIDER=caddy
INARI_UPSTREAM_MUTUAL_TLS_MODE=optional
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
```

That means:

- the enrollment HTTP surface remains reachable before the first managed client certificate exists
- after issuance, the steady-state managed path can tighten to mTLS-required connectivity

## Mutual TLS Posture

The intended production recommendation is:

- first enrollment: mTLS may not exist yet
- after certificate issuance: the managed data plane should behave as mTLS-required

The current code intentionally supports:

- `disabled`
- `optional`
- `required`

But the recommended serious managed posture is:

- `optional` before issuance
- effectively `required` after a managed certificate has been issued

That keeps bootstrap ergonomic without weakening the steady-state trust model.

## Controller and Router Colocation

The expected production architecture is usually:

- controller HTTP API
- Zenoh router
- controller workers / command logic

on the same server boundary.

That means the deployment is conceptually one managed control plane, even though the HTTP enrollment API and Zenoh router are different technical concerns.

This is why the protocol documentation models them separately but treats their coexistence as the expected deployment shape.

## Recommended Production Combination

The cleanest production stack is:

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_EDGE_PROVIDER=caddy
INARI_UPSTREAM_MUTUAL_TLS_MODE=optional
INARI_UPSTREAM_AUTH_MODE=zitadel_service_account
INARI_ZITADEL_BASE_URL=https://zitadel.example.com
INARI_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
INARI_UPSTREAM_ENROLLMENT_TOKEN=replace-me
```

That gives you:

- `HTTPS` enrollment with a clear bootstrap path
- optional enrollment authorization through `ZITADEL`
- short-lived managed client certificates through `step-ca`
- `Zenoh` as the steady-state managed data plane
- TLS + mTLS on the managed data plane after issuance
- a local agent that still keeps the tray and POS/browser experience on the loopback boundary

## Operational Notes

- The managed gateway stack is intentionally **separate** from the local desktop UX. The tray should not be reinterpreted as a controller-managed surface.
- The agent remains useful in standalone mode. Managed mode layers onto the existing local runtime instead of replacing it.
- The cleanest way to reason about the deployment is:
  - local plane: loopback API + local WebSocket
  - managed plane: HTTPS enrollment + Zenoh data plane
  - certificate plane: step-ca bootstrap / issuance / renewal
