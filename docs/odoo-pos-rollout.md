# Odoo POS receipt rollout

The receipt path runs from the POS browser to the local Agent over HTTPS.
The Agent sends the prepared ESC/POS receipt through the Windows printer queue.
The Odoo server does not connect directly to the USB printer.

## Required setup

Before opening the production register:

1. Install the matching Inari Odoo Addon and its Python dependencies.
2. Configure the company Pairing Assertion key in OpenBao.
3. Install and start the Inari Agent on the computer connected to the printer.
4. Configure an Agent Endpoint whose HTTPS certificate the POS browser trusts.
5. Authorize the exact Odoo browser origin and company scope.
6. Install the USB printer driver and confirm that its Windows queue exists.
7. Activate the receipt Driver Profile and its exact Device Capability.
8. Complete a Device Test, then activate the POS receipt Binding Revision.
9. Pair the POS browser and approve its Client Grant.

An Odoo login page, a running Odoo IoT service, or a visible Windows queue does
not prove that this Inari path is ready. The Inari Agent, addon, trust, and
Binding must all be present.

## Browser checks

Run the addon browser tests on a test database:

```text
/web/tests?headless&filter=%40inari_devices&debug=assets
```

Use the `@inari_devices` filter. Confirm that the runner reports the Inari tests,
not zero tests. Also open the POS without `debug=assets` to check the production
asset bundle.

Complete a test sale through the real POS screens. Confirm that the receipt
contains the expected product, tax, total, payment, and receipt identifier.

Check these failure cases before rollout:

- Repeat a receipt click while its first submission is unresolved. It must keep
  the same Print Intent.
- Lose the submission response, then use recovery. The browser must query the
  Agent before it retries the saved receipt.
- Reload the POS while a receipt is unresolved. Recovery must retain its
  identity and must not invent missing receipt content.
- Disconnect the printer. The sale must remain paid, and the receipt must stay
  available in recovery without an implicit native print.
- Restore the connection and retry from recovery. An uncertain physical outcome
  requires review before another copy.

## Automated checks

Run the browser-independent receipt and recovery checks from the repository root:

```sh
node --import ./packages/odoo-inari/tests/register_odoo_loader.mjs \
  --test packages/odoo-inari/tests/*.test.mjs
```

The native Odoo test `TestInariPosAssets` passes each declared POS module through
Odoo's asset compiler and checks the result as a browser script. It requires
Node on the test host.

## Inari addon artifact

`deploy/odoo/build_artifact.py` stages one artifact with
`addons/inari_devices`, `python/inari_print_contracts`, and the locked
`rfc8785` dependency. The `requirements.txt` in the artifact records the
dependency hash. Odoo must list the `addons` directory in `addons_path` and
the `python` directory in `PYTHONPATH`.

The artifact also includes `compatibility.json`. It declares the addon version,
Odoo version range, exact Agent and Controller versions, shared-contract version,
Contract Majors, Device authority contract, and gateway protocol version.
Verify these fields against the Release Set before deployment. The shared-contract
wheel uses the pinned `setuptools==83.0.0` build backend.

The `release.yaml` workflow publishes the artifact to
`ghcr.io/hadronomy/inari-odoo-addons:sha-<commit>` from `main`. It signs the
digest with keyless Cosign and attaches provenance and an SPDX SBOM. The
deployment must pin the digest and verify the signer identity
`https://github.com/hadronomy/inari/.github/workflows/release.yaml@refs/heads/main`
with issuer `https://token.actions.githubusercontent.com` before unpacking.
Keep the Inari artifact separate from the MZE artifact.

## Controller image

The `controller-image.yaml` workflow builds the Controller and its matching
browser assets. Pull requests verify the container runtime and asset files.
Main publishes `ghcr.io/hadronomy/inari-server:sha-<commit>` with provenance,
an SBOM, and a keyless Cosign signature.

Pin the image digest in the Controller chart. Verify the signer identity
`https://github.com/hadronomy/inari/.github/workflows/controller-image.yaml@refs/heads/main`
with issuer `https://token.actions.githubusercontent.com`. The image contains
no deployment credentials. PostgreSQL, OIDC, certificates, and Device authority
still require the production configuration.

## Recovery retention

A recovery task retains its original Agent channel independently of its Binding
Revision. The channel must remain in the same Odoo company, Organization, Site,
and POS configuration before the browser restores its Client Grant.

After Output Confirmed or an explicit resolution, the browser deletes the active
task and releases its Receipt Payload and client reference. A content-free journal
retains the receipt copy identity for 90 days. A POS reload reads that identity
before it allocates another Print Intent. Expired journal entries are deleted.

Peripheral stream requests remain unavailable until a production Driver supplies
Scale Readings or Barcode Events. Receipt submission and recovery use separate
Interfaces and remain available.

## Physical acceptance

A captured JPEG or simulated admission proves software behavior only. Before
use at the counter, print a receipt on the target USB printer. Check width,
clipping, accents, logo, QR code, paper feed, and cut behavior. Confirm that one
requested copy produces one ticket.

`Accepted` means that the Agent stored the Print Job. It does not prove that
paper left the printer. Recovery must show the strongest available Output
Evidence and must preserve an uncertain outcome.

## September 8 readiness check

The September 9 rollout is blocked by the deployed setup. Connecting the USB
printer alone will not complete the Inari path.

| Surface | Observed state | Required action |
| --- | --- | --- |
| Production Odoo | `inari_devices` is absent from database `odoo`. | Publish the addon and dependencies, then install it during a planned maintenance window. |
| Windows workstation | No Inari Agent service or process is running. The existing Odoo IoT process is separate. | Install the tested Agent version and configure browser trust and pairing. |
| Printer | The 80-VII-UL USB printer is disconnected. Its driver and queue are absent. | Connect it, install its driver, and verify ESC/POS support through a Device Test. |
| Network | The workstation receives HTTP 200 from the production Odoo login page with trusted TLS. | Verify the separate POS-browser-to-Agent HTTPS connection after Agent setup. |

Local Odoo 19 checks passed for a paid POS sale, receipt rendering, and the
production asset bundle. The addon browser suite passed 20 tests with 58
assertions. Browser-independent checks passed 36 tests. Agent ingress,
admission, and policy checks passed 106 tests. Spooler, physical execution,
and printer driver checks passed 40 tests.

The captured sample receipt converted to a 512 × 760 ESC/POS raster with feed
and cut commands. Its browser submission used a simulated Agent response.
These checks do not establish production pairing, Windows USB delivery, or
physical output on the 80-VII-UL.

When updating an existing addon, rebuild Odoo's asset attachments and restart
its workers. Verify the POS without `debug=assets`; a cached production bundle
can retain old code after source files change.

The native Windows run found two additional blockers: text-mode spool writes
changed encrypted bytes, and the service secret store misread a pywin32 ACL
result. Both fixes passed on the workstation. The spool and execution suites
passed 81 tests with two POSIX-only skips. Both secret-store tests passed,
including real DPAPI storage and the restricted service ACL.

An isolated production-mode Agent then started with a temporary HTTPS
certificate. Certificate verification passed against the explicit test trust
anchor. The exact production Odoo origin received HTTP 200 for its receipt
preflight. An untrusted origin received HTTP 400. An unauthenticated receipt
submission received HTTP 401. The probe stopped the Agent after these checks.
This does not install a production certificate or pair the live POS browser.

## October 3 WORKSTATION upgrade and Controller recovery

WORKSTATION runs signed candidate `1.20.0-alpha.13`, MSIX `1.20.0.1013`, from
source `01bad52b83ba977fe88b60f03ac5e060cb57f7f0`. The package includes the
integrated Device Center UI, Client Pairing controls, and local connection fix.
Its build is [run 37099088646](https://github.com/hadronomy/inari/actions/runs/37099088646).
The MSIX SHA-256 is
`8f97b310776f419a1d1f70ef0c68c083b79c2b921c10ea82c6cf4366e0b36b2d`.

All seven artifact checksums passed. GitHub provenance and the SPDX attestation
passed for the exact branch and source commit. Windows accepted the publisher
signature through its existing trust chain. The Agent and Device Center paths
both point to the alpha.13 package. The Agent runs as `LocalService`.

The upgrade preserved the Agent configuration, identity, pairing public keys,
and TLS files. The database backup passed verification. The protected backup
is `C:\ProgramData\InariUpgradeBackups\01bad52b83ba\state`.
The completed installation tasks and temporary signing branch policy were removed.

Trusted HTTPS returns `401` for unpaired access to `/system/status`.
Device Center authenticated through loopback: `/auth/local-challenge`,
`/auth/local-token`, and `/onboarding/status` returned HTTP 200. The new UI
shows the required organization invitation step. Operations-window and Client
Pairing acceptance still require enrollment.

The Controller runs the signed image from main source
`4141b9629f2a5c07f392f7bb59cf1a3109cd6ce3`. Its image digest is
`sha256:735b475e5428197860c98b75466137a0cbf87e82d144f78ef56188af75a49e47`.
The keyless signature passed verification for `controller-image.yaml` on
`refs/heads/main` and the GitHub Actions OIDC issuer. Infrastructure PR 34 is
merged at `a269d65218a04ef86dc8d855aa1ed58f315a3420`. Flux and Helm report Ready.
Trusted HTTPS health and readiness checks passed. PostgreSQL and OIDC are ready.

The Controller redirects to ZITADEL with `hadronomy@mizonaecologica.es`.
The browser is at the password step. Human sign-in and administrator,
Organization, and Site verification remain incomplete. Certificate issuance,
enrollment, and Zenoh remain disabled. No receipt Binding Revision was activated
and no physical receipt passed the full Inari path.
