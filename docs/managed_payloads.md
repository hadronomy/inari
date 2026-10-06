# Managed Payload storage

The Controller stores pending Dispatch Envelopes in `managed_payloads`.
Each row uses a random AES-256-GCM data key and nonce. OpenBao Transit wraps
the data key. The Dispatch Envelope also retains its encryption to the exact
target Agent.

The `commands` table stores only a Managed Work reference for these dispatches.
Live delivery and command history load the same encrypted envelope. They require
OpenBao to unwrap its data key before dispatch.

Canonical authenticated data binds the Organization, Managed Work, Idempotency
Key, document fingerprint, and full Device Work fingerprint. A changed Device,
Print Origin, Binding Revision, or device option conflicts with an existing
Idempotency Key. A new preflight does not change the identity of an exact Retry.

## OpenBao configuration

The `[openbao]` section configures the shared OpenBao connection and workload
authentication. The `[managed_gateway.payload_protection]` section selects the
Transit mount and encryption key. Managed dispatch requires payload protection. The
[Controller configuration example](../crates/inari-server/config.example.toml)
contains both sections.

| Setting | Meaning |
| --- | --- |
| `openbao.address` | HTTPS origin for OpenBao. Paths, credentials, queries, and fragments are rejected. |
| `openbao.kubernetes_role` | OpenBao role for the Controller workload identity. |
| `openbao.kubernetes_auth_mount` | Kubernetes authentication mount. The default is `kubernetes`. |
| `managed_gateway.payload_protection.transit_mount` | Transit secrets mount. The default is `transit`. |
| `managed_gateway.payload_protection.transit_key_name` | Existing environment key. The default is `inari-managed-payload`. |
| `openbao.service_account_token_file` | Mounted Kubernetes service account token. |
| `openbao.namespace` | Optional OpenBao namespace. |
| `openbao.ca_certificate_file` | Optional PEM certificate authority for the OpenBao HTTPS connection. |
| `openbao.request_timeout` | Timeout for each OpenBao request. The default is five seconds. |

The workload role needs access to the named Transit key's `encrypt` and `decrypt`
endpoints. It does not need key creation, export, backup, or deletion privileges.
The client uses base64 `associated_data` on both operations, as specified by the
[OpenBao Transit API](https://openbao.org/docs/next/api/secret/transit/).

The client refreshes its authentication before the token lease ends. An
authentication rejection clears the cached token. The next request authenticates
again. Redirects and environment proxies are disabled. Workload token files and OpenBao responses have a
64 KiB limit, including responses without a `Content-Length` header.

For a manual Controller configuration, move the connection and authentication
values from `[managed_gateway.payload_protection]` to `[openbao]`. The Controller
rejects those values in the old section. The Helm chart renders the new section
from the existing `managedGateway.payloadProtection` values.

## Failure and deletion behavior

If OpenBao cannot wrap a key, new Managed Work returns `503` before Controller
Admission. If OpenBao cannot unwrap a key, affected dispatch stops. Existing
Managed Work queries and exact Retry receipts remain available.

Verified Agent State updates Managed Work and deletes its ciphertext and wrapped
key in one PostgreSQL transaction. The deletion time remains in Managed Work
metadata. Unsigned acceptance and rejection receipts cannot change this state
or delete content. A conflicting later observation cannot change a terminal
Print Job result. A delayed publication marker cannot reset an accepted command.

The Controller checks expired content once per second, including while Zenoh is
offline. Expired Pending Agent work becomes Expired Managed Work. Expired
Dispatching Managed Work becomes Recovery Uncertain because Agent acceptance
can remain unproved. Both transitions delete the content.

Command history cannot replay deleted or expired content. Such requests stop
with an error and require reconciliation. Live deletion does not erase retained
PostgreSQL backups or WAL archives.

## Upgrade behavior

Migration `m20260905_223029_protect_managed_payloads` preserves the earlier schema
history. It removes `sealed_document` from Managed Work and replaces stored
dispatch content with Managed Work references.

The migration rejects unexpired Pending Agent or Dispatching Managed Work before
changing the schema. Stop new admissions and let existing work finish or expire
before the upgrade. A rejected upgrade retains the earlier schema and content.

The migration removes expired and terminal content. Expired Pending Agent work
becomes Expired Managed Work. Expired Dispatching work becomes Recovery Uncertain.
Existing acceptance and rejection records retain their state. Idempotency Keys
remain reserved.

The migration rejects missing or duplicate Organization Idempotency Keys. Its
transaction rolls back on failure. Older request fingerprints remain reserved
but do not support exact replay under the new fingerprint contract.

The previous Controller binary cannot use the upgraded schema. This change
requires a coordinated Controller upgrade and reconciliation of expired dispatching
work. The general migration commands are in
[Controller database operations](controller_database.md).
