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

This command installs authority. It does not manufacture Device Test evidence,
perform Device I/O, or establish Client Pairing. At admission, the Agent signs
the latest Driver discovery record with its protected Device observation key.
The Driver must report the platform backend, connection, media, firmware, and
operating system. If a fact is absent or the Device is offline, admission stays
closed.

The Windows receipt Driver reads current spooler information on each discovery
pass. It reports the queue port, Windows driver, initialized media width, and
spooler readiness. Unknown status flags and failed reads cannot claim readiness.
Pooled and unknown ports cannot claim a USB connection. Windows spooler data
does not identify Device firmware. Firmware fields remain unavailable until a
Device protocol reports them.
