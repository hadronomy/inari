# POS production rollout

## Target

Complete a sale in `MIZONA ECOLÓGICA`, then print its receipt through Inari on
the workstation's 80-VII-UL USB printer.

- Odoo: `https://odoo.eden.mizonaecologica.es`
- Database: `odoo`
- POS configuration: `2`
- Infrastructure: `mze-infra`, cluster `shadow`
- Agent Host: Windows `workstation`
- Odoo release commit: `53d0572853885ed19a066af177a4410f8b45f606`
- Odoo shared contracts: `1.20.0a11`
- Windows candidate source: `01bad52b83ba977fe88b60f03ac5e060cb57f7f0`
- Windows candidate: `1.20.0-alpha.13`, MSIX `1.20.0.1013`
- Controller source: `4141b9629f2a5c07f392f7bb59cf1a3109cd6ce3`

Status: **in progress**. Odoo and the Windows Agent are installed. The receipt
Binding Revision is inactive. No physical receipt passed the full Inari path.

## Critical path

Work through this list in order. Update each item with its deployed identity
and test result before marking it complete.

- [x] Fix Odoo 19 POS asset startup and service registration.
- [x] Preserve the Print Intent across receipt retries and browser recovery.
- [x] Complete a local Odoo sale and render its receipt.
- [x] Fix Windows binary spool storage and protected-key setup.
- [x] Run the current Agent wheel on Windows with production storage behavior.
- [x] Verify HTTPS and CORS for the exact production Odoo origin in isolation.
- [x] Stage matching Agent and shared-contract wheels with checksums.
- [x] Complete PR review and release checks.
  - [x] Fix the first DPoP nonce challenge and bind renewal to one Client Pairing.
  - [x] Renew execution leases during preparation.
  - [x] Keep recovery on its original Agent channel after a Binding Revision changes.
  - [x] Retire completed recovery tasks and retain receipt identity for 90 days.
  - [x] Add a scoped lookup for the public Print Job ID.
  - [x] Add signer retirement and exact Driver Profile selection.
  - [x] Add the artifact compatibility manifest and pin its build backend.
  - [x] Run the Controller PostgreSQL regressions and merge the six PR layers.
- [ ] Install Controller-signed authority through a supported runtime path.
  - [x] Add and test the atomic `inari authority install` command.
  - [ ] Provision the production Controller trust and signed bundle.
- [ ] Publish current signed Device observations from real Driver discovery.
  - [x] Sign fresh discovery records with a separate protected Agent key.
  - [x] Read current Windows queue, driver, media width, and readiness facts.
  - [ ] Read the actual Device firmware identity through its supported protocol.
- [x] Prepare the Odoo runtime with the addon and shared-contract dependency.
  - [x] Stage the addon, shared contracts, and locked dependency in one Inari artifact.
  - [x] Publish a signed digest from the `main` release workflow.
  - [x] Pin, verify, and mount the artifact in the Odoo runtime.
- [ ] Prepare the required Controller, Organization, Site, and policy setup.
- [x] Configure OpenBao to sign scoped Odoo Pairing Assertions.
  - [x] Merge infrastructure PR 24 and verify Flux revision `e9ff0530`.
  - [x] Provision the non-exportable company key, narrow policy, and Kubernetes role.
  - [x] Install service account `odoo-inari` in namespace `odoo`.
  - [x] Verify service account login and mount its token and CA in Odoo.
- [x] Prepare the Windows Agent service and its trusted HTTPS certificate.
- [x] Publish matching signed artifacts and pin the Odoo artifact in `mze-infra`.
- [ ] Activate the complete Release Set after its Device Test and acceptance checks.
- [x] Complete the final Odoo backup before its planned restart.
  - [x] Verify the database and filestore baseline backup.
  - [x] Finish active payments and synchronize paid orders in all POS windows.
- [x] Install the addon and verify the production POS asset bundle.
- [x] Start the Windows Agent service and verify service restart and HTTPS.
- [ ] Synchronize authoritative Agent and Device projections into Odoo.
- [ ] Connect the USB printer and install its actual Windows driver.
  - [x] Connect the USB printer and register queue `POS-80` on `USB002`.
  - [x] Configure and observe an initialized 80 mm media width.
  - [ ] Identify the manufacturer and its supported firmware observation protocol.
- [ ] Complete the receipt Device Test and activate the exact Driver Profile.
- [ ] Activate the receipt Binding Revision for POS configuration `2`.
- [ ] Pair the production POS browser and approve its scoped Client Grant.
- [ ] Complete a sale and print one physical receipt through the full path.
- [ ] Verify retries, browser reload, printer disconnect, and recovery.
- [ ] Record the deployed versions, checksums, backup, and acceptance evidence.

## Acceptance

The flow is ready only after a paid sale produces one requested receipt on the
target printer. Check product names, accents, totals, tax, payment, QR code,
paper width, feed, and cut behavior.

Repeat clicks must preserve the Print Intent. An uncertain physical outcome
must require review before another copy. A printer failure must preserve the
paid sale and its recovery record.

## Current dependencies

The printer is connected to WORKSTATION. The operator supplied its self-test
ticket. It identifies model `80-VII-UL`, ESC/POS, a 576-dot print width, and a
firmware revision date of February 26, 2025. The ticket does not identify the
manufacturer. USB firmware queries returned no response. This ticket is hardware
evidence, not a passed Inari Device Test.

The operator authorized ZITADEL for the Controller's production OIDC provider.
Infrastructure PRs 29 and 30 deploy it at
`https://auth.eden.mizonaecologica.es` and permit its exact OpenBao secret path.
The Helm release, database, API, and OIDC discovery are ready. The configured
client redirects to ZITADEL with `hadronomy@mizonaecologica.es`. Human sign-in
and administrator, Organization, and Site verification remain incomplete.
Certificate issuance, enrollment, and Zenoh remain disabled.

The signed alpha.13 package runs on WORKSTATION. Device Center discovers the
protected Agent Endpoint and authenticates through trusted HTTPS over loopback.
The challenge, token, and onboarding requests returned HTTP 200. The new UI
shows the organization invitation step. Enrollment must finish before the
operations window and Client Pairing acceptance checks.

The operator supplied rear-label model `POS-8370` and serial `25103000100009`.
Keep these facts separate from the self-test model and USB descriptor serial.
The manufacturer remains unconfirmed.

The software also needs a supported path that runs a real Device Test before
binding activation. Existing signed Device Test models and the authority
installer do not perform that Device I/O. Do not create a passed result to
replace the missing execution path.

## Newly confirmed implementation blockers

The Agent can now install signed Device authority through the
[administrator command](device-authority-installation.md). The SQLite-backed
admission test passes after that import. Production still needs its Controller
trust and signed bundle. The Agent can sign current discovery records, but the
Windows receipt Driver does not yet report all certification facts. Admission
stays closed until the Driver reports those facts and the Controller authorizes
the Agent's Device observation key.

Keep the Inari artifact separate from the MZE artifact, as required by
[ADR 0021](adr/0021-keep-addon-artifacts-separate.md). Package the shared Python
dependency with the Inari artifact and add its installed module directory to
the Odoo runtime. Keep the existing Odoo image and MZE addon ownership intact.

## Evidence

See [the rollout checks](odoo-pos-rollout.md) for the completed Odoo and native
Windows results, including the October 3 upgrade and Controller recovery.
The October 2 deployment evidence follows. Full Release Readiness and physical
acceptance remain incomplete.

## Production deployment on October 2

- All six implementation PRs, `61` through `66`, are merged.
- The merged native Odoo suite passed 22 tests, including POS asset compilation.
- The Agent suite passed 635 tests, with four skips. All four Desktop CI jobs
  passed for the production branch, including the frozen Windows Agent.
- The Controller suite passed its PostgreSQL enrollment and state-replay tests.
- The deployed Inari Odoo artifact comes from release commit `53d05728`. Its
  digest is `sha256:0a309a294b18deed5f2ce394166a5a80bd97c68497311b1b0c5b61909a7da519`.
  Keyless signature verification passed for the repository's `release.yaml`
  workflow on `refs/heads/main` and the GitHub Actions OIDC issuer.
- Odoo installed `inari_devices` version `19.0.1.1.0` and shared contracts
  `1.20.0a11`. The Odoo image and MZE addon artifact stay at their existing
  production identities. Infrastructure PRs 27 and 28 own the cutover changes.
- Both production POS JavaScript bundles returned HTTP 200. They contain the
  submission context, receipt recovery, Client Pairing, Agent client, printer,
  and device service modules. The POS asset watcher returns
  `17888594851790967871`.
- The OpenBao pairing role uses the non-exportable Ed25519 key
  `inari-odoo-pairing-odoo-1`. The Odoo Pod authenticated with its projected
  Kubernetes identity and signed a content-free verification claim. Its
  Ed25519 signature passed verification against the production public key.
  Unrelated-key denial checks also passed.
- The dedicated certificate for `inari-workstation.eden.mizonaecologica.es` is
  ready. It expires on December 31 at `17:25:09 UTC`.
- WORKSTATION runs the signed MSIX `1.20.0.1011`. Its SHA-256 is
  `53ca4a51b83480c1b96344d3af9e0616b4edf18ff76408cc42762c25518fd342`.
  GitHub provenance and the Windows publisher signature passed verification.
- `InariAgent` runs automatically as `LocalService`. It uses the protected
  production config and TLS files. A service restart passed. Its runtime
  database is at revision `20260906_0016`.
- The production Odoo browser reached the Agent through trusted HTTPS on
  port 7310. Unpaired access returned `401 trust_required`. The response
  permits the exact Odoo origin and gives no CORS access to an unrelated origin.
- Windows discovered `POS-80` on `USB002` with driver `Generic / Text Only`.
  The driver accepted an initialized 80 mm media width. Firmware identity
  remains unavailable. The Windows form label still reads `Letter`; it does
  not identify the initialized width or certify the physical media.
- The operator signed in to production Odoo. User 2 received the Inari System
  Administrator role. The Organization, Site, Agent, Device, Binding, and
  Release Set projections are empty. POS configuration 2 has no receipt binding.
- The verified baseline backup is on `shadow`, at
  `/var/backups/inari-pos/20261002T173509Z`. The database and filestore archives
  passed their archive checks and have a checksum manifest.
- After the operator confirmed POS Drain, Odoo stopped writes for the final
  consistent backup at `/var/backups/inari-pos/20261002T191214Z-cutover`.
  The database and filestore archives passed their checks. Odoo then completed
  the migration and restart. The backup precedes that schema change.

The receipt Device still needs complete Device observations, Controller
authority, a real Device Test, an active Binding Revision, and Client Pairing.
Then run the sale and recovery acceptance checks. Moving the printer to the
store's POS lane requires a separate Agent Host setup and Device Test.
