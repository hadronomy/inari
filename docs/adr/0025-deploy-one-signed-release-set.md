---
status: accepted
---

# Deploy one signed Release Set

One signed Release Set pins every artifact, Contract Major, migration revision,
and POS asset build that enters production together.

Pinned executable artifacts include Agent, Controller, Zenoh, qpdf, PDFium,
CUPS filters, ZPL parser, Inari and MZE Odoo addons, POS assets, and signed
Device Center packages for each operating system and architecture.

The signed manifest records every artifact digest, signature identity,
Contract Major, migration revision, and compatibility matrix. Verification
failure blocks promotion and activation.

The protected MZE workflow signs the manifest with keyless Cosign. The verifier
pins its issuer, repository, workflow, and source-ref identity.

The Release Set maps each supported Odoo 19 patch build to its immutable image
digest and test evidence. Release Readiness reports that build and digest. An
image digest outside the tested set has no production support.

Production Helm rendering fails when a required digest is absent.

`mze-infra` owns and signs the environment-specific Release Set after it
checks each source artifact. Promotion uses the same digests without a rebuild.

Activation uses maintenance mode, database migration, full Odoo asset deletion,
module checks, server build checks, and a POS asset smoke test before traffic.

The gate first completes the POS Drain. An authenticated Release Readiness
route reports the active Release Set, contract, schema, module, database, and
POS asset identities. The POS bundle carries the same asset identity.

Before traffic reopens, rollback restores the prior database and Release Set.
After reopening, database recovery uses a forward fix.

An approved operating-system package installs Device Center and the Agent
before Client Pairing. The local readiness check reports package version,
signature state, service registration, startup state, and repair result.

A missing, damaged, or incompatible package blocks Client Pairing and opens
the install or repair path. Odoo never hosts or runs the installer. Server-side
Release Set activation does not require every Agent Host to be online.
