---
status: accepted
---

# Keep addon artifacts separate

The Odoo deployment pins the MZE and Inari addon artifacts independently. It
checks their signatures and unpacks them into separate addon directories.

Odoo lists both roots in `addons_path`. A declarative module list controls
installation and updates. This structure preserves artifact ownership and
prevents one release from mutating another artifact.

GitHub Actions uses keyless Cosign to sign each artifact and publishes an SBOM
and build provenance. Flux checks the exact issuer, repository, workflow, and
source-ref identity. Odoo starts only with a verified digest.

Only each repository `release.yaml` workflow on `main` can sign production
artifacts. The policy rejects pull-request refs, tag refs, arbitrary workflows,
and reusable-workflow identities.
