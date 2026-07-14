# Managed deployment

Managed mode connects edge agents to an Inari controller without moving device
execution out of the local machine. The agent keeps its queue, drivers, and
local API; the controller adds enrollment, policy, audit, and fleet-wide work.

## Topology

```text
Edge agent
  ├── HTTPS ───────────────► Inari Controller
  │     enrollment             │
  │                            ├── PostgreSQL
  │                            ├── OIDC
  │                            └── step-ca provisioner
  │
  ├── HTTPS ───────────────► step-ca
  │     certificate issue
  │
  └── Zenoh over TLS/mTLS ─► Zenoh Router
        status and work          ▲
                                │ controller client
                                └── Inari Controller
```

The controller and Zenoh router belong to the same private platform, but they
are different workloads. The controller is a stateless HTTP application;
routers keep stable network identities and an explicit mesh. PostgreSQL, OIDC,
step-ca, ingress, and secret delivery remain organization-owned services.

Every edge connection is outbound. A managed installation does not open the
agent host to inbound controller traffic.

## What each boundary carries

### Local application boundary

Odoo, Device Center, and other software on the edge host use the authenticated
local HTTP API and live event stream. They do not speak Zenoh and do not need
controller credentials.

### Enrollment boundary

Enrollment uses HTTPS because it starts before the agent has a managed client
certificate. A one-use invitation or machine credential authorizes the request.
The controller verifies the protocol version, Ed25519 JWK, CSR signature, and
key binding before returning permissions, Zenoh endpoints, namespace, and
certificate bootstrap material.

Human operators authenticate to the controller with OIDC. Unattended agents use
a signed enrollment bundle or a machine flow approved by the deployment.

### Managed data plane

Zenoh carries status, liveliness, commands, results, runtime events, and replay
queries. Agents connect in client mode. Production transport uses TLS with a
client certificate issued for that agent.

The controller’s HTTP compatibility route at `/api/zenoh/v1/{selector}` is an
operator and integration surface over the same keyspace. It is not the agent’s
primary transport and does not wrap Zenoh keys into Inari resources.

## Certificate lifecycle

The normal production flow uses step-ca:

1. The controller authenticates and verifies the enrollment request.
2. It mints a short-lived, agent-bound step-ca token for the submitted CSR.
3. The agent verifies the CA root fingerprint and exchanges the token for its
   first certificate.
4. The agent opens the Zenoh session with that certificate.
5. Renewal uses certificate-backed step-ca authentication.

The controller never stores or reuses the one-time CA token. cert-manager may
manage Kubernetes workload certificates, but enrolled edge identities stay in
the step-ca device trust domain.

Bootstrap can begin without mTLS. Once the agent has a certificate, managed
traffic should require it.

## Configure the controller

Start from
[`crates/inari-server/config.example.toml`](../crates/inari-server/config.example.toml).
A production controller needs:

- an explicit public URL and organization identity;
- externally managed PostgreSQL;
- OIDC discovery, client credentials, and role mapping;
- onboarding policy and invitation lifetime;
- public Zenoh endpoints returned to agents;
- step-ca provisioner identity and mounted signing key;
- controller Zenoh client certificate and CA bundle.

Keep secret values in mounted files. Validate the complete configuration before
starting a rollout:

```sh
INARI_SERVER_CONFIG=/etc/inari/config.toml \
  inari-server config validate
INARI_SERVER_CONFIG=/etc/inari/config.toml \
  inari-server config print-effective
```

`print-effective` redacts secrets by default. Use `--no-redact` only in a
private terminal when the disclosure is deliberate.

## Configure an agent

Set `agent.mode = "managed"` in the agent TOML and provide the controller URL.
The interactive invitation flow supplies the one-use enrollment credential;
unattended installation supplies its signed bundle.

The controller normally owns the values under `transport.zenoh` and the
step-ca connection details. Configure local overrides only when the deployment
has an intentional fallback. The agent’s generated
[`config.example.toml`](../packages/agent/config.example.toml) documents every
field and its default.

Managed policy and local settings remain separate. Enrollment must not rewrite
arbitrary operator configuration.

## Production checklist

Before enrolling a real edge host, confirm that:

- the controller URL is reachable through HTTPS and has the expected public
  identity;
- OIDC sign-in and role mapping work for an enrollment administrator;
- PostgreSQL migrations are current;
- the step-ca root fingerprint is approved through an independent channel;
- Zenoh router certificates cover internal mesh and external client names;
- the public Zenoh endpoints match those certificate names;
- NetworkPolicy and firewalls allow the edge network to reach Zenoh TLS;
- controller readiness reports PostgreSQL, identity, certificates, and required
  Zenoh connectivity truthfully;
- an invitation can be created, previewed, consumed once, and rejected on a
  second attempt;
- the enrolled agent reconnects and recovers command history after an
  interruption.

The exact wire contract, keyspace, and replay rules live in
[the gateway protocol](gateway_protocol.md). Kubernetes rollout and recovery
procedures live in [the Kubernetes guide](kubernetes.md).
