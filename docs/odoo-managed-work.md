# Odoo Managed Work client

Each Odoo company uses one Organization Workload Identity. Configure its
`client_id`, exact OIDC `issuer_url`, and Organization in Odoo. The company
setup record supplies the Controller HTTPS origin. Neither record stores a
credential or access token.

## Provision the credential

Store the client credential in OpenBao KV v2 at:

```text
<mount>/inari/odoo/<sha256-of-exact-database-name>/companies/<company-id>/workload
```

The mount defaults to `secret`. `INARI_OPENBAO_CREDENTIAL_MOUNT` selects another
mount. The database digest uses the UTF-8 database name, including its exact case.
Use this JSON shape for the KV secret data:

```json
{
  "database": "production",
  "company_id": "7",
  "organization_id": "org_example",
  "client_id": "odoo-company-7",
  "issuer_url": "https://identity.example.com",
  "client_secret": "<client-secret>",
  "scopes": ["openid", "urn:zitadel:iam:org:project:id:<controller-project-id>:aud"]
}
```

Include any scope that the OIDC provider requires for the Controller audience.
The access token must carry that audience and these exact claims:
`inari_database`, `inari_company_id`, `inari_organization_id`, and
`inari_permissions`. The issuer assigns permissions to the approved service
identity. OAuth request scopes cannot grant Inari permissions. The Controller
checks them before it permits Managed Work. The client rejects KV data whose
identity fields differ from the selected Odoo company identity.

For inventory synchronization, assign only `inventory:read`. Managed Work
requires `managed_work:read` and `managed_work:write`. In ZITADEL, select JWT
access tokens for the service account. A Complement Token action must match the
exact service user, Organization owner, and client identity before it assigns
the Inari claims. Do not derive permissions from requested scopes or editable
user metadata.

## Connect and synchronize inventory

In the Odoo Devices list, select **Connect Controller**. Enter the company,
Controller HTTPS origin, Organization identity, identity issuer, and workload
client identity. The wizard obtains the protected credential from OpenBao and
checks the exact company inventory before it creates the connection.

**Synchronize** refreshes the same company. A scheduled task runs every five
minutes. A complete response updates Organizations, Sites, Agents, and Devices,
then archives absent records. Invalid responses and failed requests preserve
the previous inventory. An older snapshot cannot replace a newer snapshot.

The Devices list shows connection state separately from Device Health.
Discovery does not create a certified capability or activate a Device Binding.
A signed Driver Profile and a passed Device Test remain required for receipts.

The Odoo pod uses `INARI_OPENBAO_ADDR` and `INARI_OPENBAO_KUBERNETES_ROLE` to
exchange its projected ServiceAccount token for a short-lived OpenBao token.
The same OpenBao connection settings serve Pairing Assertions and Managed Work.
Grant this workload role read access only to its required company credential
paths, plus the Pairing Assertion Transit operations it needs.

OpenBao CA and client-certificate settings retain the `INARI_OPENBAO_*` names.
`INARI_OIDC_CACERT` and `INARI_CONTROLLER_CACERT` optionally select PEM CA bundles
for the other HTTPS connections. TLS certificate verification is required.

## Authentication and Retry

The client uses OIDC discovery and `client_credentials` with
`client_secret_basic`. Discovery must return the configured issuer and a token
endpoint on the same HTTPS origin. Each credential component uses form encoding
before HTTP Basic authentication.

Access tokens must expire within 15 minutes. The client caches them in memory
and refreshes before expiry. Refresh reads the current OpenBao credential, so
credential rotation needs no Odoo database change. An HTTP `401` permits one
reauthentication attempt. Redirects are disabled on all credential-bearing
requests, and response bodies have explicit size and time limits.

A timeout during submission can leave admitted Managed Work. The client does
not automatically repeat that submission. Before a Retry, it calls:

```http
GET /api/inari/v1/managed-work/by-idempotency-key
Authorization: Bearer <access-token>
Idempotency-Key: <original-key>
```

This lookup requires `managed_work:read`. It uses the authenticated Organization
and checks the returned database and company scope. A `404` means no record
exists. Other failures keep the outcome unresolved. The client also supports
preflight, submission, and lookup by Managed Work identity.

These client operations support the Report Binding workflow. They do not
render reports, choose Devices, or authorize physical output. Signed Agent State
remains necessary for authoritative Print Job observations.

## Shared document contract

Install the matching `inari-print-contracts` package in the Odoo Python runtime.
The Agent and Odoo use its `fingerprint_device_work` function for the Payload
Fingerprint. The contract uses RFC 8785 for Device options and includes the exact
Controller deadline. A document digest alone cannot replace this fingerprint.

## Report request preparation

`inari.report.binding.prepare_managed_report` accepts a stored contextual action
identity and this versioned request:

```json
{
  "version": 1,
  "binding_id": 2,
  "binding_revision_id": 3,
  "report_action_id": 4,
  "source_model": "res.partner",
  "source_ids": [7, 5],
  "site_id": 8,
  "report_type": "qweb-pdf",
  "copies": 1,
  "wizard_data": {}
}
```

Version 1 accepts at most 500 distinct source identifiers and 10 copies. It
preserves source order. Generic requests accept empty wizard data only. Device
identities, document bytes, Idempotency Keys, Copy Ordinals, and sequence
identities are not request fields.

Preparation checks the stored action marker, current company, report groups,
source-record rules, Site, active Binding Revision, and tested Driver Profile.
A POS order resolves its Site through `pos.config.inari_site_id`. An automatic
report needs a registered Site resolver. A generic manual request must name the
Site of its selected Report Binding.

Successful preparation creates a content-free report sequence and returns a
random action ticket. It creates no Print Intent. Odoo stores only the ticket
digest and binds it to the actor and company. Operators can read sequence
records but cannot change their authority fields. Invalid requests return a
handled result with `report.request_invalid` before sequence creation.

## Verification

Run the isolated transport and identity tests from the repository root:

```sh
uv run --all-packages --group odoo-tests pytest \
  packages/odoo-inari/tests/test_managed_work_client.py \
  packages/odoo-inari/tests/test_report_fingerprint.py
```

Run the model and access-rule tests against a disposable Odoo 19 database with
the addon installed:

```sh
odoo --addons-path=addons,packages/odoo-inari -d inari_test \
  -u inari_devices --test-enable --test-tags /inari_devices --stop-after-init
```
