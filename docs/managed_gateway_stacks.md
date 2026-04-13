# Managed Gateway Deployment Stacks

This guide documents the managed gateway stacks the agent now supports directly:

- Caddy as the public HTTPS/WSS edge in front of the controller
- ZITADEL as the identity provider for controller and agent bearer auth
- step-ca as the private CA for short-lived client certificates

Use this document together with [gateway_protocol.md](C:/Users/pablo/.codex/worktrees/2eb5/odoo_iot_alt/docs/gateway_protocol.md).

## Design Overview

The current recommended production shape is:

```text
IoT Agent -> HTTPS / WSS -> Caddy -> Controller
    |                           ^
    |                           |
    |---- bearer tokens ------- ZITADEL
    |
    |---- client certificate -- step-ca
```

The agent stays the local edge gateway. It does not require an external hardware gateway.

For production bootstrap, the recommended certificate path is:

1. installer provides an enrollment code or invite
2. agent enrolls with the controller
3. controller validates policy and mints a short-lived step-ca OTT
4. agent exchanges that OTT with step-ca for its first client certificate
5. later renewals use `/1.0/renew`

## Supported Modes

### 1. Controller-Issued Tokens

Use when the controller owns enrollment and token lifecycle itself.

```env
IOT_AGENT_GATEWAY_MODE=managed
IOT_AGENT_UPSTREAM_BASE_URL=https://controller.example.com
IOT_AGENT_UPSTREAM_AUTH_MODE=controller
IOT_AGENT_UPSTREAM_BOOTSTRAP_TOKEN=replace-me
IOT_AGENT_UPSTREAM_CERTIFICATE_MODE=controller
```

In this mode:

- enrollment uses the bootstrap token
- the controller returns `access_token`
- the controller may optionally return a client certificate

### 2. ZITADEL Service Account Auth

Use when the controller trusts bearer tokens issued by ZITADEL.

```env
IOT_AGENT_GATEWAY_MODE=managed
IOT_AGENT_UPSTREAM_BASE_URL=https://controller.example.com
IOT_AGENT_UPSTREAM_AUTH_MODE=zitadel_service_account
IOT_AGENT_ZITADEL_BASE_URL=https://zitadel.example.com
IOT_AGENT_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
IOT_AGENT_ZITADEL_REQUESTED_SCOPES=openid,events:read,jobs:submit,commands:execute
```

In this mode:

- the agent signs a private-key JWT assertion with the ZITADEL service-account key
- the agent exchanges that assertion for an OAuth access token
- the controller may omit `access_token` from the enrollment response

### 3. step-ca Client Certificates

Use when the controller edge expects the agent to present a short-lived client certificate.

```env
IOT_AGENT_UPSTREAM_CERTIFICATE_MODE=step_ca
IOT_AGENT_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example.com/api/iot-agent/enroll
IOT_AGENT_UPSTREAM_ENROLLMENT_CODE=SITE-A-4F7K-92LM
```

In this mode:

- the controller returns `certificate_bootstrap` with a short-lived step-ca OTT
- the agent bootstraps the step-ca root certificate
- the agent requests a client certificate from `/1.0/sign` using that OTT
- the agent renews it via `/1.0/renew`
- the agent does not store a shared CA provisioner key locally

Optional overrides:

- `IOT_AGENT_STEP_CA_URL`
- `IOT_AGENT_STEP_CA_SIGN_URL`
- `IOT_AGENT_STEP_CA_RENEW_URL`
- `IOT_AGENT_STEP_CA_ROOT_FINGERPRINT`

Those are mainly useful for controlled environments where you want the agent to keep local fallback knowledge of the CA endpoints. The preferred production path is to let the controller return those values in `certificate_bootstrap`.

## Caddy Edge Profile

The agent now understands a Caddy-focused edge profile:

```env
IOT_AGENT_UPSTREAM_EDGE_PROVIDER=caddy
IOT_AGENT_UPSTREAM_MUTUAL_TLS_MODE=optional
```

Or for strict mTLS:

```env
IOT_AGENT_UPSTREAM_EDGE_PROVIDER=caddy
IOT_AGENT_UPSTREAM_MUTUAL_TLS_MODE=required
IOT_AGENT_UPSTREAM_CERTIFICATE_MODE=step_ca
IOT_AGENT_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example.com/api/iot-agent/enroll
```

When strict Caddy mTLS is enabled, the agent validates its own startup configuration and refuses to run if it has no way to obtain a client certificate.
The bootstrap enrollment URL is how the agent gets that first certificate before the normal mTLS-protected controller endpoints take over.

### Example Caddyfile

Optional client certificates:

```caddyfile
controller.example.com {
    tls {
        client_auth {
            mode verify_if_given
            trusted_ca_cert_file /etc/caddy/step-ca-root.pem
        }
    }

    reverse_proxy 127.0.0.1:8080
}
```

Required client certificates:

```caddyfile
controller.example.com {
    tls {
        client_auth {
            mode require_and_verify
            trusted_ca_cert_file /etc/caddy/step-ca-root.pem
        }
    }

    reverse_proxy 127.0.0.1:8080
}
```

## Recommended Production Combination

The cleanest stack is:

```env
IOT_AGENT_GATEWAY_MODE=managed
IOT_AGENT_UPSTREAM_BASE_URL=https://controller.example.com
IOT_AGENT_UPSTREAM_EDGE_PROVIDER=caddy
IOT_AGENT_UPSTREAM_MUTUAL_TLS_MODE=required
IOT_AGENT_UPSTREAM_AUTH_MODE=zitadel_service_account
IOT_AGENT_ZITADEL_BASE_URL=https://zitadel.example.com
IOT_AGENT_ZITADEL_SERVICE_ACCOUNT_KEY_PATH=./secrets/zitadel-service-account.json
IOT_AGENT_UPSTREAM_CERTIFICATE_MODE=step_ca
IOT_AGENT_UPSTREAM_ENROLLMENT_URL=https://bootstrap.controller.example.com/api/iot-agent/enroll
IOT_AGENT_UPSTREAM_ENROLLMENT_CODE=SITE-A-4F7K-92LM
```

That gives you:

- HTTPS/WSS edge protection through Caddy
- bearer auth and authorization through ZITADEL
- short-lived client certificates through step-ca without shipping a shared CA provisioner key to every agent
- a local agent that still runs fully as its own gateway
