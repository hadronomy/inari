# Signed Router policy

The Router Supervisor owns one stock Zenoh Router process and its exact Agent
ACL. It is distinct from the Controller API and the Controller's Zenoh session.
This document specifies the Supervisor boundary implemented by `inari-router`.
The Controller allocates generations in PostgreSQL and reconciles every configured
Supervisor over dedicated management mTLS. Helm deployment and credential
provisioning remain rollout requirements.

## Policy authority

The management API requires HTTPS with mutual TLS. It trusts only its configured
management CA. A client certificate must have exactly one common name equal to
`management.controller_common_name`. An Agent certificate cannot authorize a
management request. TLS validates the certificate chain, client authentication
usage, validity, and signature. Each request also checks certificate validity,
including requests on an existing connection.

Each new management handshake reads the configured CA, certificate, and private
key. Content changes replace the TLS acceptor before that handshake. An unreadable,
invalid, or expired replacement rejects new connections until the files recover.
The Supervisor does not require a restart for management certificate rotation.

The Controller sends `PUT /policy` with a `SignedPolicy` JSON document. The body
contains exactly `policy` and `signature`. Unknown fields and duplicate struct
fields are invalid. The body limit is 2 MiB.

`signature` is a hexadecimal Ed25519 signature over these bytes:

1. The UTF-8 domain separator `inari.router-policy.v1` and one newline.
2. The RFC 8785 canonical JSON encoding of `policy`.

The configured signing key is independent of the management client certificate.
The Supervisor rejects weak Ed25519 keys. The digest in an acknowledgment is the
lowercase hexadecimal SHA-256 digest of the canonical `policy` bytes, without
the signature or domain separator.

| Policy field | Contract |
| --- | --- |
| `version` | Exactly `1`. |
| `fleet_id` | The Supervisor's configured fleet identity. A literal ASCII name of 1–128 characters. |
| `generation` | A monotonically increasing integer from 1 through 9,007,199,254,740,991. |
| `issued_at` | An RFC 3339 UTC timestamp at or before `not_before`. |
| `not_before` | An RFC 3339 UTC timestamp strictly before `expires_at`. |
| `expires_at` | An RFC 3339 UTC timestamp no more than five minutes after `issued_at`. |
| `namespace_prefix` | A literal ASCII path, at most 256 characters, without empty, `.` or `..` segments. |
| `trusted_peer_common_names` | From 1 through 64 distinct literal common names for Controller and Router peers. Agent names are forbidden. |
| `agents` | At most 10,000 distinct entries, each with `common_name` and `namespace`. |

Literal names permit ASCII letters, digits, dots, hyphens, and underscores.
Each Agent common name is `agt_` followed by 24 lowercase hexadecimal digits.
Its namespace must equal `namespace_prefix + "/" + common_name`. A policy is
complete: an omitted Agent has no authority. Wildcard certificate subjects are
forbidden.

A policy is current only when `not_before <= now < expires_at`. An expired
policy retains its generation and digest for rollback rejection.

## Activation and acknowledgment

The Supervisor serializes policy updates through a bounded queue. Cancellation
of an HTTP caller does not interrupt an update that entered that queue.

It verifies the signature, fleet, static limits, time window, and generation
before changing the active Router. A lower generation is invalid. The same
generation requires the same digest. An identical retry can acknowledge an
already ready Router. It can also retry activation after a failed start.

For an update that needs activation, the Supervisor:

1. Builds a default-deny ACL and validates it with the Zenoh configuration parser.
2. Marks readiness false and stops the old Router. All existing links close.
3. Atomically replaces and synchronizes the signed policy and Router configuration.
4. Starts stock `zenohd` with that configuration.
5. Opens a real mTLS Zenoh session and verifies the expected Router ID.
6. Returns the exact generation, digest, expiry, and `ready: true`.

A higher generation with the same fleet, namespace, trusted peers, Agent
permissions, and TLS credentials refreshes the lifetime without restarting a ready Router.
The Supervisor persists that generation before acknowledgment and keeps existing
links open. The previous policy remains the authority during the durable write.
If that policy expires while storage is slow, the Supervisor stops the Router.
A failed durable write also closes admission and stops the Router.

The Supervisor reads data-plane and probe TLS files once per second. A content
change stops the old Router and activates the current policy with the new TLS
material. The configuration contains an immutable credential snapshot, so Secret
symlink changes cannot alter the child credentials after activation. The state
directory therefore contains private TLS material and must stay private.

Loaded certificate expiry stops the Router even if the signed policy is current.
An invalid replacement also stops it. A valid replacement can recover the same
current policy without changing its generation. A real mTLS probe precedes readiness.

The Supervisor cannot acknowledge a policy after its expiry. A failed start
leaves readiness false and retains the durable generation. Policy expiry stops
the Router. An unexpected child exit also removes readiness. The Controller
must reconcile the complete policy after a failure. The Supervisor does not
invent Agent admission from certificate names or network contact.

The state directory has one owner and an exclusive file lock. The signed policy
is the durable generation record. Malformed or incorrectly signed stored policy
stops startup. A restart can activate a stored policy only while it is current.
An expired stored policy starts no Router and still rejects an older generation.

Clock synchronization is an operating requirement for the Controller and every
Router Supervisor. The Controller must refresh policies before their expiry.

## Controller admission

The Controller derives a complete policy from its Organization's Agent records.
An Agent requires a dispatch key, its current transport identity key, and an
active Agent State key. Retired keys cannot authorize admission.

PostgreSQL serializes generation allocation across Controller replicas. A policy
binds its authority revision and a digest of the fleet, Router management
origins, trusted peers, namespace prefix, and signing key. Replicas with the same
config reuse the exact signed document. Activation is backdated by five seconds
for clock skew. Expiry stays 45 seconds after the Controller creates the policy. The
Controller reconciles every five seconds and refreshes when fewer than 15 seconds
remain.

Agent and key mutations invalidate the acknowledgment in the same transaction.
The Controller commits generation allocation before it sends management requests.
Every configured Router must return the exact generation, digest, expiry, and
current readiness within twenty seconds. Partial acknowledgment closes admission.
A mutation during those requests prevents the old policy from being acknowledged.

Enrollment returns its certificate bootstrap data only after Router admission.
A lost response uses the exact invitation retry contract. Managed Work preflight,
admission, and dispatch also require current admission. The admission transaction
holds a shared policy lock and rechecks the authority, Agent keys, policy expiry,
and acknowledgment age. An admission proof expires within ten seconds. Command
enqueue and replay hold the same shared lock and recheck the proof before commit.

The `router_policy` readiness component starts unavailable, becomes ready after
complete acknowledgment, and becomes degraded after a failure or proof expiry.
Managed admission remains closed while this component is unavailable.

`PUT /api/inari/v1/agents/{agent_id}/credential-retirement` requires enrollment
management permission in the configured Organization. It retires the Agent's
active verification keys, revokes its enrolled invitations, and records one audit
event. It then applies the complete policy to every Router. HTTP 204 proves that
all Routers acknowledged the removal. HTTP 503 leaves retirement committed and
requires an idempotent retry. Key history remains available for signed evidence.

The Controller uses only the configured management CA, client certificate, and
private key. Each request reads those files. Content changes replace the HTTP
client and discard its connection pool. Invalid or unreadable replacements close
admission until the files recover. It verifies the Router hostname, disables proxies and redirects,
and limits acknowledgment bodies to 64 KiB. The policy signing key is a separate
Ed25519 PKCS#8 PEM file. All Controller replicas must use the same policy config.

## Data-plane ACL

Every allowed subject requires TLS and an exact certificate common name.
Trusted Controller and Router peers can exchange messages below the configured
namespace prefix. Each Agent receives only these permissions in its namespace:

| Flow | Messages | Paths |
| --- | --- | --- |
| Ingress | `put` | `presence/agent`, `status/latest`, `results/*`, `events/*`, `errors/*` |
| Ingress | `liveliness_token` | `presence/agent` |
| Ingress | `declare_subscriber` | `commands/live/**` |
| Egress | `put` | `commands/live/**` |
| Ingress | `query` | `commands/history`, `state/commit` |
| Egress | `reply`, `declare_queryable` | `commands/history`, `state/commit` |

All other messages are denied. Removing an Agent from a new policy closes its
old connection before the new policy can receive an acknowledgment. A new
connection with that certificate receives default-deny permissions.

Zenoh requires mTLS and verifies peer names. Links close on certificate expiry.
Multicast discovery, gossip, the Zenoh admin interface, and plugin loading are
disabled. The Supervisor uses final `--cfg` overrides because stock `zenohd`
enables its admin interface and plugin loading after it reads a configuration
file. See the [Zenoh 1.9 source](https://github.com/eclipse-zenoh/zenoh/blob/1.9.0/zenohd/src/main.rs).

## Management responses

All three routes require the dedicated management certificate:

| Route | Result |
| --- | --- |
| `PUT /policy` | A ready acknowledgment after durable activation. |
| `GET /status` | The current generation, digest, expiry, readiness, and diagnostic message. |
| `GET /readyz` | HTTP 200 while ready, otherwise HTTP 503, with the same status body. |

Invalid policy facts, signatures, or time windows return HTTP 400. A stale or
conflicting generation returns HTTP 409. Queue saturation, storage errors, and
Router startup failures return HTTP 503. An expired management certificate
returns HTTP 403. Unauthorized TLS identities receive no HTTP response.

## Verification

Run the unit, storage, and real management mTLS contracts with
`mbx test -p inari-router`. The stock Router contract needs the official Zenoh
1.9 executable:

```sh
export INARI_TEST_ZENOHD=/absolute/path/to/zenohd
mbx test -p inari-router --test stock_router -- --ignored
```

The contract verifies allowed Agent publications and queries, cross-Agent
denial, preservation of a TCP connection across a lifetime refresh, closure of
that connection after revocation, policy expiry,
durable rollback rejection, and recovery with a higher generation. A second
contract verifies certificate rotation, rejection of an invalid replacement,
recovery, and certificate expiry. The CI job
verifies the official executable's SHA-256 digest before this contract runs.

Controller CI also runs a real management mTLS contract and isolated PostgreSQL
contracts. These cover concurrent generation allocation, partial or incorrect
acknowledgments, a key mutation during management I/O, credential retirement,
atomic enrollment, and Managed Work replay.
