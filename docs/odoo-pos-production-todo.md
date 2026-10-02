# POS production rollout

## Target

Complete a sale in `MIZONA ECOLÓGICA`, then print its receipt through Inari on
the workstation's 80-VII-UL USB printer.

- Odoo: `https://odoo.eden.mizonaecologica.es`
- Database: `odoo`
- POS configuration: `2`
- Infrastructure: `mze-infra`, cluster `shadow`
- Agent Host: Windows `workstation`
- Merged implementation: `24948ff3d94967a6bbf06115ec5c4af1fdcb8469`
- Windows observation branch: `t3code/inari-pos-windows-observation`

Status: **in progress**. Production does not yet have the required Inari setup.

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
- [ ] Complete PR review and release checks.
  - [x] Fix the first DPoP nonce challenge and bind renewal to one Client Pairing.
  - [x] Renew execution leases during preparation.
  - [x] Keep recovery on its original Agent channel after a Binding Revision changes.
  - [x] Retire completed recovery tasks and retain receipt identity for 90 days.
  - [x] Add a scoped lookup for the public Print Job ID.
  - [x] Add signer retirement and exact Driver Profile selection.
  - [x] Add the artifact compatibility manifest and pin its build backend.
  - [ ] Run the Controller PostgreSQL regressions and merge the six PR layers.
- [ ] Install Controller-signed authority through a supported runtime path.
  - [x] Add and test the atomic `inari authority install` command.
  - [ ] Provision the production Controller trust and signed bundle.
- [ ] Publish current signed Device observations from real Driver discovery.
  - [x] Sign fresh discovery records with a separate protected Agent key.
  - [x] Read current Windows queue, driver, media width, and readiness facts.
  - [ ] Read the actual Device firmware identity through its supported protocol.
- [ ] Prepare the Odoo runtime with the addon and shared-contract dependency.
  - [x] Stage the addon, shared contracts, and locked dependency in one Inari artifact.
  - [ ] Publish a signed digest from the `main` release workflow.
  - [ ] Pin, verify, and mount the artifact in the Odoo runtime.
- [ ] Prepare the required Controller, Organization, Site, and policy setup.
- [x] Configure OpenBao to sign scoped Odoo Pairing Assertions.
  - [x] Merge infrastructure PR 24 and verify Flux revision `e9ff0530`.
  - [x] Provision the non-exportable company key, narrow policy, and Kubernetes role.
  - [x] Install service account `odoo-inari` in namespace `odoo`.
  - [ ] Verify service account login and mount its token and CA in Odoo.
- [ ] Prepare the Windows Agent service and its trusted HTTPS certificate.
- [ ] Publish the tested Release Set and pin its artifacts in `mze-infra`.
- [ ] Complete the final Odoo backup before its planned restart.
  - [x] Verify the database and filestore baseline backup.
  - [x] Finish active payments and synchronize paid orders in all POS windows.
- [ ] Install the addon and verify the production POS asset bundle.
- [ ] Start the Windows Agent service and verify service restart and HTTPS.
- [ ] Synchronize authoritative Agent and Device projections into Odoo.
- [ ] Connect the USB printer and install its actual Windows driver.
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

## Current external dependency

The USB printer is disconnected. The operator will connect it after the software
is ready. Complete the software tasks before requesting that connection.

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
Windows results. The staged artifacts are test candidates, not an installed
production Release Set.

## Production preparation on October 2

- All six implementation PRs, `61` through `66`, are merged.
- The merged native Odoo suite passed 22 tests, including POS asset compilation.
- The Agent suite passed 635 tests, with four skips. All four Desktop CI jobs
  passed for the production branch, including the frozen Windows Agent.
- The Controller suite passed its PostgreSQL enrollment and state-replay tests.
- The signed Inari Odoo artifact from `24948ff` is published. Its digest is
  `sha256:82730ab0533febe2df5c2732ff0b5382ea7f8d8f18366aac033717fa509efad0`.
- The OpenBao pairing role uses the non-exportable Ed25519 key
  `inari-odoo-pairing-odoo-1`. Login and unrelated-key denial checks passed.
- The dedicated certificate for `inari-workstation.eden.mizonaecologica.es` is
  ready. It expires on December 31 at `17:25:09 UTC`.
- Windows now has protected production config, public Odoo signing trust, and
  the dedicated TLS files. The Agent service is not installed yet.
- The verified baseline backup is on `shadow`, at
  `/var/backups/inari-pos/20261002T173509Z`. The database and filestore archives
  passed their archive checks and have a checksum manifest.
- The operator confirmed that active payments are complete and paid orders are
  synchronized. The final backup and planned restart remain pending.

The Controller image build and Version Packages release remain in progress.
The receipt Device still needs its real Windows queue and firmware observation,
Controller authority, Device Test, Binding Revision, and Client Pairing.
