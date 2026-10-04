---
packages:
  group:edge:
    replay:
      - exit-prerelease(cargo:inari-agent-client)
      - exit-prerelease(cargo:inari-device-center)
      - exit-prerelease(pip:inari)
      - exit-prerelease(pip:inari-brand)
      - exit-prerelease(pip:inari-print-contracts)
      - exit-prerelease(msix:inari-device-center)
---

### Bind enrollment certificates to the Agent Identity

Enrollment rejects Agent identifiers and certificate requests that do not match
the protected Ed25519 identity. Certificate issuance and renewal reject incorrect
subjects, missing SANs, additional names, and duplicate names.
The Agent validates the full certificate chain against its pinned CA root and
stores the verified intermediate chain for mutual TLS. A CA response cannot
replace the pinned root.
Root bootstrap accepts exactly one pinned CA. Certificate requests trust only
that CA and require client key usage for TLS signatures. The Controller requires
a SHA-256 CA pin in step-ca mode.

The chart now keeps enrollment and Zenoh disabled by default. Provision the CA
and exact per-Agent Router policy before enabling them.

Certificate names now come from the Agent Identity. Remove
`managedGateway.certificate.stepCa.authorizedSans` from Helm values and
`step_ca_authorized_sans` from a Controller configuration before upgrading an
existing step-ca deployment.

Installed certificates now receive the same trust and identity checks before use.
The Agent repairs a cached CA against its pin and replaces obsolete certificate
names when fresh enrollment provides a one-time token. The protected key stays
unchanged. All CA endpoints require HTTPS.

Managed publication and Device Work now stop when certificate validation fails.
The Agent closes an existing session and reports the certificate error in setup.
Controller HTTPS uses separate trust before enrollment. A new invitation replaces
cached enrollment and old configured credentials without replacing the Agent key.
Concurrent enrollment calls share one request and preserve newer invitations.

Root bootstrap reads Smallstep's JSON response. Issuance and renewal use the
canonical Smallstep endpoints and retain the complete intermediate chain.

Upgrade the Agent and Controller together for gateway protocol `2026-10-03`
before enabling enrollment. Enrollment no longer sends an installed certificate,
and the Controller migration removes its unused stored certificate column.

The Controller migration removes an Agent column that old replicas still select.
Keep managed enrollment and Zenoh disabled until all Controller replicas use the
new release. A binary rollback across this migration is not supported. Recover
with the matching database backup and Release Set.

### Keep concurrent Controller documents responsive

The Controller discovers its fixed web routes once before it serves documents.
Concurrent Router construction can no longer interrupt Resource loading in an
active document stream.
