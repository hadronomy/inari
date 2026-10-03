# Secure enrollment review

Reviewed on 2026-10-03 against the current Agent, Controller, and deployed
infrastructure. Controller sign-in is verified. Production enrollment and Zenoh
remain disabled until the remaining gates pass.

## Certificate boundary

The Agent derives its identifiers from an Ed25519 public key. Enrollment must
bind the identifiers, CSR key, common name, and URI SAN to that same key.
Issuance and renewal must enforce the same certificate identity.

The Controller uses SHA-256 of the complete CSR DER as the one-time token's
`cnf.x5rt#S256` claim. Smallstep 0.30.2 supports this claim and verifies it during
issuance. The Agent must submit the same CSR after enrollment.

Source: [Smallstep 0.30.2 JWK provisioner](https://github.com/smallstep/certificates/blob/v0.30.2/authority/provisioner/jwk.go).

The Agent uses the existing cryptography library to validate the full client
certificate chain. Response-provided intermediates remain untrusted until that
path succeeds. The pinned root remains unchanged when the Agent stores its client
chain. The certificate requires the protected Agent common name, its exact URI
SAN, client authentication usage, and `digitalSignature` key usage.

Root bootstrap requires a valid SHA-256 pin and exactly one CA certificate.
Enrollment and renewal TLS trust only that root. A CA response cannot add another
root, and operating-system trust roots cannot authorize certificate requests.
The Agent checks the configured pin before using an installed certificate,
including when renewal is not due. It replaces a cached intermediate or old CA
with the pinned root, then validates the installed client chain and identity.
CA endpoints require HTTPS.

Smallstep 0.30.2 returns its root as a JSON `ca` PEM string from
`GET /root/{fingerprint}`. The Agent uses `POST /sign` and mTLS `POST /renew`.
An isolated Smallstep 0.30.2 test passed pinned root bootstrap, CSR-bound issuance,
complete chain validation, and mTLS renewal with the same protected Agent key.
A different CSR with the same key, common name, and SAN failed with the original
token. Each signing attempt used a fresh JWT ID.

Source: [Smallstep 0.30.2 API](https://github.com/smallstep/certificates/blob/v0.30.2/api/api.go).

Controller HTTPS uses its own trust boundary before managed certificate issuance.
It does not load cached managed roots or client certificates. Data-plane admission
requires a current lifecycle result for status, outbox, and command execution.
Rejected trust closes the session. A fresh invitation replaces cached enrollment
without changing the Agent key or local Device Work.

Enrollment protocol `2026-10-03` carries the signed CSR and invitation, with no
installed certificate. Fresh enrollment can replace obsolete certificate names
without changing the protected key. The Controller migration removes the unused
Agent certificate column. Upgrade both boundaries before enabling enrollment.

Source: [cryptography certificate verification](https://cryptography.io/en/latest/x509/verification/).

## Router policy gate

Zenoh 1.9 loads ACL rules at startup. Adminspace configuration writes do not
rebuild the active ACL interceptor. Its TLS identity selector uses certificate
common names. URI SANs remain part of the Agent certificate contract; the router
uses the matching Agent common name for its policy subject.

Sources: [Zenoh ACL documentation](https://zenoh.io/docs/manual/access-control/),
[Zenoh 1.9 authorization source](https://github.com/eclipse-zenoh/zenoh/blob/1.9.0/zenoh/src/net/routing/interceptor/authorization.rs),
[Zenoh 1.9 ACL interceptor](https://github.com/eclipse-zenoh/zenoh/blob/1.9.0/zenoh/src/net/routing/interceptor/access_control.rs).

The chart's broad TLS subject grants wildcard Agent namespaces. It does not bind
each Agent to its own namespace. Production requires an automatic policy path
that applies exact Agent common-name subjects and exact namespaces to every
router replica. Enrollment must wait for admission. Revocation must remove access
from existing sessions within the required limit. Stock ACL changes require
router restart, so the policy path must include controlled session shutdown and
reconnection.
The chart disables enrollment and Zenoh by default. Enabling mutual TLS alone
does not satisfy the Router policy gate.

## Other rollout gates

- Invitation consumption and enrollment persistence need one atomic transaction.
  The current claim commits before CA token creation and enrollment persistence.
  A failure can consume an invitation without an enrollment. A retry must preserve
  the exact protected Agent identity and must not authorize another Agent.
- Device Center must retain the restart requirement and offer an explicit service
  restart. Setup must then follow the Agent state through connection and Device
  selection.
- Provision a pinned CA with an offline root, an online intermediate, a dedicated
  Agent provisioner, protected credentials, persistent state, and verified backup.
- Create the MIZONA Organization and its Site before enrollment.
- Run actual Device Tests before a Binding Revision becomes active.
- Verify the complete Odoo POS path and inspect the physical receipt.

OpenBao Transit is already enabled for Odoo Pairing Assertion signatures. The
review does not require enabling Transit again. Additional certificate and Device
authority credentials still need their own policy and provisioning.

## Upgrade boundary

The Controller migration removes the unused Agent certificate column. Old
Controller replicas cannot read Agent rows after that migration. Keep managed
enrollment and Zenoh disabled during the upgrade, and replace all Controller
replicas before enabling them. The deployment must use the tested Release Set.
A binary rollback across this migration is not supported. Recovery requires the
matching database backup and Release Set.
