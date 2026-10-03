---
packages:
  "group:edge": patch
  "group:controller-chart": patch
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
