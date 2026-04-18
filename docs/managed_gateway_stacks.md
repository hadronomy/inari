# Managed Gateway Deployment Stacks

This guide documents the managed gateway stacks the agent now supports directly:

- `HTTPS` enrollment into the controller
- `Zenoh` as the steady-state managed data plane
- `step-ca` as the private CA for short-lived client certificates
- optional `ZITADEL` enrollment auth
- optional `Caddy` in front of the controller’s HTTP enrollment surface

Use this together with [gateway_protocol.md](./gateway_protocol.md).

## Design Overview

The recommended production shape is:

```text
                 HTTPS enrollment
Inari ---------------------------------> Controller HTTP API
    |                                               ^
    |                                               |
    |---- enrollment auth ------------------------ ZITADEL (optional)
    |
    |---- step-ca OTT bootstrap ------------------ Controller
    |---- client certificate issue/renew --------- step-ca
    |
    |==== Zenoh TLS + mTLS data plane ==========> Zenoh Router(s) ==> Controller workers
```

The agent remains the local edge gateway. It does not require an external hardware gateway.

The steady-state managed path is:

1. installer provides a short-lived controller-issued `enrollment_token`
2. agent enrolls with the controller over `HTTPS`
3. controller returns Zenoh connection details and, when configured, step-ca bootstrap material
4. agent obtains its first managed client certificate from step-ca
5. agent opens a Zenoh session in `client` mode using TLS + client certificate
6. status, commands, and runtime events move over Zenoh
7. later renewals use `/1.0/renew`

## Supported Modes

### 1. Controller-Issued Enrollment Tokens

Use when the controller owns enrollment policy directly.

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_AUTH_MODE=controller
INARI_UPSTREAM_ENROLLMENT_TOKEN=replace-me
```

In this mode:

- enrollment uses the controller-issued `enrollment_token`
- the controller returns the Zenoh data-plane configuration
- the controller may optionally return a controller-installed client certificate
- or it may return step-ca bootstrap data for certificate issuance

### 2. ZITADEL Service Account Auth

Use when enrollment is authorized through ZITADEL instead of a controller-issued bearer token.

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_AUTH_MODE=zitadel_service_account
INARI_ZITADEL_BASE_URL=https://zitadel.example.com
INARI_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
INARI_ZITADEL_REQUESTED_SCOPES=openid,events:read,jobs:create,commands:execute
```

In this mode:

- the agent signs a private-key JWT assertion with the ZITADEL service-account key
- the agent exchanges that assertion for an OAuth access token
- the controller trusts that enrollment request and still returns the Zenoh data-plane contract

### 3. step-ca Client Certificates

Use when the managed data plane is expected to require mTLS after certificate issuance.

```env
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
INARI_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example.com/api/inari/enroll
INARI_UPSTREAM_ENROLLMENT_TOKEN=replace-me
```

In this mode:

- the controller returns step-ca bootstrap data in the enrollment response
- the agent bootstraps the step-ca root certificate
- the agent requests its first client certificate from `/1.0/sign`
- the agent later renews through `/1.0/renew`
- the agent does not store a shared CA provisioner key locally

Optional local overrides:

- `INARI_STEP_CA_URL`
- `INARI_STEP_CA_SIGN_URL`
- `INARI_STEP_CA_RENEW_URL`
- `INARI_STEP_CA_ROOT_FINGERPRINT`

These are mainly useful as explicit fallback knowledge of the CA. The preferred production path is to let the controller return the bootstrap details during enrollment.

## Caddy And HTTP Edge

If you use Caddy, it normally fronts the controller’s HTTP enrollment API rather than the Zenoh data plane itself.

Example:

```env
INARI_UPSTREAM_EDGE_PROVIDER=caddy
INARI_UPSTREAM_MUTUAL_TLS_MODE=optional
```

Or for the recommended post-issuance posture:

```env
INARI_UPSTREAM_EDGE_PROVIDER=caddy
INARI_UPSTREAM_MUTUAL_TLS_MODE=optional
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
INARI_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example.com/api/inari/enroll
```

That means:

- HTTP enrollment remains reachable before the first managed client certificate exists
- once the certificate has been issued, the Zenoh data plane can require mTLS

## Recommended Production Combination

The cleanest stack is:

```env
INARI_GATEWAY_MODE=managed
INARI_UPSTREAM_BASE_URL=https://controller.example.com
INARI_UPSTREAM_EDGE_PROVIDER=caddy
INARI_UPSTREAM_MUTUAL_TLS_MODE=optional
INARI_UPSTREAM_AUTH_MODE=zitadel_service_account
INARI_ZITADEL_BASE_URL=https://zitadel.example.com
INARI_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
INARI_UPSTREAM_CERTIFICATE_MODE=step_ca
INARI_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example.com/api/inari/enroll
INARI_UPSTREAM_ENROLLMENT_TOKEN=replace-me
```

That gives you:

- HTTPS enrollment protection through Caddy or another HTTP edge
- enrollment authorization through ZITADEL or a controller-issued enrollment token
- short-lived client certificates through step-ca without shipping a shared CA provisioner key to every agent
- Zenoh as the managed data plane for status, commands, and runtime events
- a local agent that still runs fully as its own gateway
