# Install Device authority

The Agent Administrator installs Controller-signed Device authority with:

```sh
inari authority install --config /etc/inari/config.toml \
  --trust /etc/inari/controller-authority.json \
  --bundle /var/lib/inari/authority.json
```

Start the Agent once before installation to establish its Agent Identity. Use
the service's config and protected storage. Keep the trust file under the same
administrator access controls as that config. The trust file contains public
key material; it must come from the approved Controller setup.

Before the Controller signs the bundle, export the Agent's Device observation
public key from the same protected storage:

```sh
inari authority observation-key --config /etc/inari/config.toml
```

The command prints `key_id` and `public_key` as JSON. Add that public key to the
Controller-signed manifest as a signer with purpose `device_observation`. Keep
the private key on the Agent Host. Receipt admission rejects observations if
the manifest signer does not match that protected key.

The trust document contains `scope` and `signer`. `scope` specifies the database,
Organization, Site, scope kind, and POS configuration. `signer` is the
Controller's `authority_revision` Ed25519 key record, including its validity
interval. The bundle cannot change this trust key or its permitted scope.

## Bundle contract

The Controller provides an operator command for the approved bundle:

```sh
inari-server authority sign-bundle --draft draft.json \
  --approval approval.json --output authority.json
```

Run signing with a dedicated operator identity. The standard Controller pod
does not mount an OpenBao identity when Managed Work dispatch is disabled.
Signing does not require dispatch. Use the
[standalone signing Job](../deploy/controller/authority-job.yaml) for Kubernetes.
Replace its image placeholder with the approved Controller image digest. In the
Job's namespace, provide `inari-authority-input` with `draft.json` and
`approval.json`, and `inari-authority-openbao-ca` with the approved `ca.crt`.
The input ConfigMap must fit Kubernetes' 1 MiB limit.

Configure the `inari-authority-signer` OpenBao Kubernetes role for only that
ServiceAccount, namespace, and projected token audience. Its policy needs read
access to `transit/keys/<approved-key>` and update access to
`transit/sign/<approved-key>` for each of the four approved keys. Give it no
other key access. The Job mounts a short-lived identity token, the CA, and a
writable output claim. It stops after one signing attempt and never serves the
Controller API. Read the completed file from `inari-authority-output` using an
operator review pod. Retain the approved bundle, then remove the Job, input
ConfigMaps, ServiceAccount, and output claim. Use a new output claim for the next
revision; signing refuses an existing output file.

For an operator installation outside Kubernetes, configure `[openbao]` in the
file selected by `INARI_SERVER_CONFIG`. Set `address`, `kubernetes_role`,
`service_account_token_file`, and `ca_certificate_file` to that installation's
approved identity and trust files.

The command uses this configured OpenBao Kubernetes identity. Give this identity
read access to the four approved Transit keys and sign access to those keys.
Keep each key non-exportable and disable plaintext backup. The command checks
the approved public key and key version before every signature.

The approval contains `agent_id`, `scope`, `transit_mount`, `agent_signers`, and
four key approvals: `root`, `profile`, `matrix`, and `binding`. Each key approval
contains `key_name`, `key_version`, and the purpose-bound `signer` record.
Each Controller purpose needs a distinct Transit key and distinct public key.
Agent signing keys must also use separate key material.
The root purpose is `authority_revision`. Agent purposes are
`device_observation` and `device_test_evidence`.

The draft contains `agent_id`, `scope`, `revision_id`, `revision_number`,
`effective_at`, `expires_at`, `profiles`, `certification_rows`, `bindings`,
`evidence`, and `activations`. Profile, matrix, and binding entries contain the
unsigned records from the bundle contract. Evidence entries retain the Agent
signature. Use the independently approved keys and exact observed hardware
facts. The command does not infer certification from discovery.

Both inputs accept at most 4 MiB and reject unknown fields. The command requires
a current expiry and `effective_at <= now`. It verifies the completed bundle before it writes a new
output file. It cannot replace an existing file. It does not write Controller
database records or install authority on an Agent.

For the first Device Test, leave `evidence` and `activations` empty. After the
operator confirms the physical checks, add the Agent-signed result and its
activation to a newer draft. Sign and install that bundle to open receipt
admission.

The `inari.device-authority.v1` manifest contains the exact Agent identity and
scope, purpose-bound signer records, Driver Profiles, Hardware Certification
Matrix rows, Binding Revisions, Device Test evidence, and binding activations.
An activation names one Binding Revision and one passed Device Test. Empty
activation lists are valid and disable the scope's previous active bindings.

The bundle contains `revision` and `manifest`. The revision signs the manifest's
SHA-256 digest. Compute the manifest digest from the JSON representation of
`AuthorityManifest`, using RFC 8785 canonical JSON. Binary keys and signatures
use hexadecimal strings. Each projection also carries its own signed digest.
Projection signatures use the existing Device Capability authority payloads.

The Python models in `inari.device_authority.bundle` define the exact document
shape. Unknown fields and unsupported contract identities are rejected. The
command limits the trust document to 16 KiB and the bundle to 8 MiB.

## Activation and failure behavior

The installer verifies the manifest, signatures, scope, active graph, and expiry
before it changes authority. It requires an expiry for the authority revision.
Referenced Devices must already exist in the Agent's discovery records.

One SQLite transaction installs all records and switches the active bindings.
A failed insert rolls back the whole transaction. Repeating the same signed
revision has no effect. Older revisions, changed immutable records, and
same-number revision replacements are rejected. Import cannot clear Agent
Quarantine. Existing signed records remain available for audit and recovery.

A later bundle can retain the exact signed Driver Profile, matrix row, Binding
Revision, and Device Test evidence. Their storage records retain the revision
that first installed them. Receipt admission uses membership in the signed
manifest, including the exact record digests and binding activation. The
publication and Device I/O checks require that membership in both the admission
revision and the current revision. A withdrawn record closes admission.

The Agent migration `20261008_0019` changes the authority proof trigger to check
manifest membership. It preserves the signed records and their installation
provenance. Database migration creates a backup before it changes the trigger.
The original bundle dates on a stored Binding Revision describe installation
provenance. Admission uses the signed authority revision's validity window.
Driver Profiles, matrix rows, Device Test evidence, and signers retain their
own validity checks.

Each revision retains its signed manifest. Device Test authorization requires
the exact Binding Revision, Driver Profile, and certification row in the current
manifest. A newer manifest can withdraw a graph while its historical records
remain available for audit. Withdrawal blocks new tests and the pre-I/O check.

Reissue authority with a newer signed revision after an upgrade from a database
that did not retain manifests. A historical manifest digest cannot prove record
membership. Such revisions cannot authorize a Device Test.

This command installs authority. It does not manufacture Device Test evidence,
perform Device I/O, or establish Client Pairing. At admission, the Agent signs
the latest Driver discovery record with its protected Device observation key.
The Driver must report the platform backend, connection, media, firmware, and
operating system. If a required fact is absent or the Device is offline,
admission stays closed.

The Windows receipt Driver reads current spooler information on each discovery
pass. It reports the queue port, Windows driver, initialized media width, and
spooler readiness. Unknown status flags and failed reads cannot claim readiness.
Pooled and unknown ports cannot claim a USB connection. Windows spooler data
does not identify Device firmware. Firmware fields remain unavailable until a
Device protocol reports them.

The Driver must report `unavailable` explicitly for firmware that its protocol
cannot observe. A signed Hardware Certification Matrix row can qualify this
exact Device with the same values. The row still pins the Device identity,
Driver Profile, platform backend, connection, media, and operating system. An
observed firmware value that differs from the row blocks admission. An absent
firmware field cannot claim readiness.

This qualification does not prove the firmware version. Its audit evidence
references the matrix row that records this limitation. If a later Driver can
observe firmware, install a new matrix row and Binding Revision, then run a new
Device Test. Existing immutable rows cannot change in place.

## Device Test authority

The Agent has a separate `DeviceTestPermit` for standard non-business Device
Work. `authorize_test` checks the installed Binding Revision, Driver Profile,
Hardware Certification Matrix row, current signed observation, scope, expiry,
and revocations. It does not require activation or a previous passed test.
The caller supplies scope from the authenticated Client Grant. The authority
requires that this scope matches the signed Binding Revision exactly.

`check_test` repeats these checks immediately before Device I/O. It rejects
expired permits, permits from another authority instance, changed graphs,
revoked records, and authority rollback. The permit has a 30-second maximum
lifetime. A shorter configured permit lifetime or signed expiry reduces it.

A Device Test permit has no business authority proof. Receipt admission,
Drawer Intent execution, and the Device Spool cannot use it.

Export the Agent's Device Test public key from the service's protected storage:

```sh
inari authority test-key --config /etc/inari/config.toml
```

Add the returned key to the Controller-signed manifest with purpose
`device_test_evidence`. The private key remains on the Agent Host. Device Test
submission requires this key in the current manifest. The first bundle can
contain empty evidence and activation lists.

The [Device Test contract](device-tests.md) specifies execution, physical
answers, and signed results. A passed result does not activate a Binding
Revision. Activation requires a newer Controller-signed bundle that includes
the verified evidence and names its Binding Revision.
