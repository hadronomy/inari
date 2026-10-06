# Receipt Device Test contract

The Local Agent Interface executes one standard receipt Device Test before a
Binding Revision enters use. It requires a Device Manager Client Grant with
`device_test:run`, an installed signed graph, and an approved Agent evidence
key. It does not require a Controller connection.

The routes use the existing Client Pairing, Client Grant, and DPoP contract.
Header authorization runs before body parsing. Result queries and repeated
submissions also check the stored Client Grant and Client Pairing lifecycle.
Each record belongs to one database, company, Organization, Site, POS
configuration, Client Pairing, and actor.

## Routes

| Method and path | Contract |
| --- | --- |
| `POST /v1/device-tests` | Accept a Test ID, Device ID, and Binding Revision ID. Return 202 for new work or 200 for a replay. |
| `GET /v1/device-tests/{test_id}` | Return the owned Device Test record and its current state. |
| `POST /v1/device-tests/{test_id}/checks` | Record all physical answers and return the immutable signed result. |

Submission contains `contract_major: 1`, `device_test_id`, `device_id`, and
`binding_revision_id`. Exactly one `Idempotency-Key` header is required.
Its value must equal `device_test_id`. A missing header returns 422.
Duplicate headers return 400. The Agent
owns the pattern. The caller cannot supply business content, a printer name,
renderer options, or another operation.

Test IDs contain 1 to 256 ASCII characters and start with a letter or digit.
The remaining characters can be letters, digits, `.`, `_`, `:`, or `-`.
The submission and both routes enforce the same Test ID rule.

The OpenAPI files in [contracts](../contracts/local-agent.openapi.json) define
the request and response models. The HTTP regression tests in
[test_device_tests.py](../packages/agent/tests/test_device_tests.py) exercise
the submission, query, physical-answer, replay, and authorization contracts.

## Pattern and physical answers

`receipt-v1` is a fixed 576-dot JPEG. It uses the receipt imaging module and
requires feed followed by partial cut. This first contract supports
`pos_receipt`, `receipt_image`, JPEG, Contract Major 1, and empty options.

The JPEG SHA-256 digest is
`b744c997b1de32da1f812287e476ec4c67eb5402cb851270457ea497c32fc57c`.
Both code symbols contain `INARI-TEST-V1`.

| Answer field | Physical check |
| --- | --- |
| `text` | Text and numbers are complete and legible. |
| `accents` | Accented characters and the euro symbol match the pattern. |
| `code128` | The printed Code 128 decodes to `INARI-TEST-V1`. |
| `qr` | The printed QR code decodes to `INARI-TEST-V1`. |
| `feed_and_cut` | The receipt feeds clear of the mechanism and receives a partial cut. |

Every field requires `correct`, `incorrect`, or `not_run`. An omitted answer
is rejected. A `not_run` answer produces Failed Environment. An `incorrect`
answer produces Failed Contract when all checks ran. Passed requires all
answers to be `correct`, confirmed platform output, and Output Evidence at
least as strong as the Driver Profile requires.

Spooler evidence alone does not prove these physical checks. A Test without
confirmed output and the required evidence can produce only Failed Environment.
Incorrect answers cannot turn missing execution evidence into Failed Contract.

## Diagnostic receipt

The explicit `print_test_page` Device command also prints the fixed `receipt-v1`
pattern. It uses the same packaged JPEG, 576-dot renderer, feed, and partial cut.
The command accepts no receipt content or renderer options.

This diagnostic records the Driver result through the existing command ledger.
It creates no signed Device Test Result and cannot activate a Binding Revision.
The Local Agent Device Test routes remain the authority for physical answers
and signed certification evidence.

## Execution and retry

New work enters `accepted`. The Agent commits one worker claim before it starts
preparation. The claim keeps the Device reserved and rejects another execution
of the same Test. The Agent checks current authority again under the SQLite
writer lock and commits its I/O marker before it permits Device I/O.
The marker retains the exact signed graph and observation digest.

Device Tests and business work reserve the same Device for physical execution.
A Test that did not start worker preparation loses its reservation at its
execution deadline. Preparation and `in_progress` keep the reservation until
the worker stops. An expired Test cannot start Device I/O. The Agent preserves a
confirmed result that arrives at the deadline boundary.

A worker stop failure keeps the reservation in either state, even after the
execution deadline. The deadline cannot prove that the worker stopped.

Only the matching worker claim can record I/O or cleanup. Successful cleanup
releases the claim. A failed stop retains it until repair and restart recovery.

Confirmed output enters `awaiting_checks`. Uncertain output enters
`outcome_unknown`. Failure before Device I/O enters `failed_environment`.
The Agent records a signed result only after the physical-answer request.

A replay returns the existing record. It never starts another worker or sends
another receipt. Changed submission fields or changed finalized answers are
rejected. After an Agent restart, abandoned unstarted work becomes
`failed_environment`; abandoned Device I/O becomes `outcome_unknown`.
Neither state restarts automatically. Another physical receipt requires an
explicit Device Manager action with a new Test ID.

If the worker cannot stop, the record reports `worker_stop_failed`. Its Device
reservation remains held. This failure needs Agent Administrator repair:
stop the worker before restarting the Agent. A receipt retry cannot release
the reservation or start another worker. Recovery retains `worker_stop_failed`
as the reason for an unknown outcome.

## Signed result and activation

The Agent's protected Ed25519 key signs the canonical result. The envelope
contains `result`, `digest`, `signer_key_id`, and a hexadecimal `signature`.
The result binds the Test ID, record identity, actor, full scope, Device,
Binding Revision, pattern, answers, outcome, graph, times, platform evidence,
Platform Job Identity, and error code. When platform evidence exists, it also
contains separately signed Device Test evidence for authority installation.

Finalization checks graph revocations, current manifest membership, signer
purpose, signer lifecycle, and authority expiry. Withdrawal or revocation
prevents a historical graph from producing a new signed result. Repeating an
already finalized answer returns the original result without signing again.

Odoo and the Controller must verify the full signed result against the approved
evidence key and expected scope before they accept a Device Test Result.
Client Pairing and actor belong to the signed result, not to a browser claim.
The Agent retains the immutable result and evidence in one transaction.

This Agent contract does not issue Controller authority or activate a Binding
Revision. The Controller authorizes activation through a new signed bundle
after it verifies a passed result. The initial graph can authorize a Device
Test without previous evidence or activation.

## Controller record contract

The Controller uses typed records from `inari_gateway::device_authority`.
`CanonicalRecord` validates each record and retains its immutable RFC 8785
payload and SHA-256 digest. Only Authority Revision, Driver Profile,
Hardware Certification Matrix Row, and Binding Revision implement
`ControllerRecordPayload`. Agent observation and Device Test evidence remain
separate signature purposes.

Signed payload timestamps always use UTC with six microsecond digits. Bundle
JSON omits the fractional part when the microsecond value is zero. The
manifest digest covers this bundle JSON. These two formats must remain exact
because the Agent verifies both hashes independently.

The Controller bundle verifier requires an independently trusted root key,
the exact Agent and Binding Scope, and a current, bounded Authority Revision.
It verifies every record signature before it checks the activation graph.
An initial bundle can contain no evidence or activations. An active Binding
Revision requires matching Passed evidence and current certification records.

The shared [conformance vectors](../contracts/device-authority.test-vectors.json)
contain only test keys and test hardware facts. The
[Agent generator](../packages/agent/tests/support/device_authority_vectors.py)
and [Rust tests](../crates/inari-gateway/tests/device_authority_contract.rs)
verify all six signature purposes at zero, millisecond, and microsecond
precision. Observation vectors also cover Unicode, control characters,
whitespace, and an empty reason. The Agent drift test rejects a stale vector file.

This record boundary does not issue authority, approve hardware facts, or
activate a Binding Revision. Those actions require the Controller issuer.

## Controller signing boundary

`AuthoritySigningKey` uses the shared OpenBao client. Each instance binds one
Controller signature purpose to an approved Transit key name, positive key
version, and public key. It checks the key metadata before each signature.
The key must use Ed25519 with derivation, export, and plaintext backup disabled.

For each approved signing key, the shared workload role needs `read` on
`<mount>/keys/<key>` and `update` on `<mount>/sign/<key>`. These are OpenBao
policy paths, without the HTTP `/v1/` prefix. The
[OpenBao signing policy example](https://openbao.org/blog/flux-openbao-secrets-signatures/#step-1-generate-the-signing-key-in-openbao)
shows these capabilities.

Signing requires a current approval and a validated `CanonicalRecord` for the
same purpose. The request contains the canonical bytes, the exact approved key
version, and `prehashed=false`. The OpenBao 2.5.4 request omits `hash_algorithm`.
The value `none` is invalid for this raw Ed25519 operation. The
[versioned Transit implementation](https://github.com/openbao/openbao/blob/v2.5.4/builtin/logical/transit/path_sign_verify.go)
defines this behavior.

The boundary accepts only `vault:v<approved-version>:` signatures. It decodes
the 64-byte signature and verifies it against the approved public key and
canonical bytes before returning it. A metadata change, another key version,
or an invalid signature stops the operation.

This boundary does not create keys or approvals. It cannot sign Agent evidence
or observations. Durable issuance, approval audit records, and bundle export
remain responsibilities of the Controller issuer.
