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
