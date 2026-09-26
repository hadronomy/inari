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

The `release.yaml` workflow publishes the artifact to
`ghcr.io/hadronomy/inari-odoo-addons:sha-<commit>` from `main`. It signs the
digest with keyless Cosign and attaches provenance and an SPDX SBOM. The
deployment must pin the digest and verify the signer identity
`https://github.com/hadronomy/inari/.github/workflows/release.yaml@refs/heads/main`
with issuer `https://token.actions.githubusercontent.com` before unpacking.
Keep the Inari artifact separate from the MZE artifact.

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
