from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from inari.device_authority import (
    SignedAuthorityRevision,
    SignedDeviceObservation,
    SignerPurpose,
    SignerRecord,
    SignerState,
    canonical_digest,
    canonical_json_bytes,
)
from inari.device_authority.bundle import AuthorityBundle, BundleModel

from .. import test_device_capability_authority as fixtures


class SignerJson(BundleModel):
    signer: SignerRecord


class ObservationJson(BundleModel):
    signed: SignedDeviceObservation


def vectors() -> dict[str, object]:
    cases = []
    for microsecond in (0, 1_000, 123_456):
        now = fixtures.NOW.replace(microsecond=microsecond)

        def test_key(purpose, name):
            seed = sha256(
                b"Inari cross-language fixture key: " + purpose.value.encode()
            ).digest()
            private = Ed25519PrivateKey.from_private_bytes(seed)
            public = private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            return (
                SignerRecord(
                    name,
                    purpose,
                    public,
                    SignerState.ACTIVE,
                    now - timedelta(days=1),
                    now + timedelta(days=90),
                    None,
                ),
                private,
            )

        with (
            patch.object(fixtures, "NOW", now),
            patch.object(fixtures, "_key", test_key),
        ):
            projections, observations, _, _ = fixtures._fixture()
        manifest = projections.manifest
        if manifest is None:
            raise ValueError("The fixture requires an authority manifest.")
        signed = projections.authority_state.current_revision
        root = projections.signers[signed.signer_key_id]
        private = projections.private_keys[root.key_id]
        activated = AuthorityBundle(revision=signed, manifest=manifest)
        initial_manifest = manifest.model_copy(
            update={"evidence": (), "activations": ()}
        )
        revision = replace(
            signed.revision,
            revision_id="initial-authority-revision",
            manifest_digest=canonical_digest(initial_manifest.model_dump(mode="json")),
        )
        initial = AuthorityBundle(
            revision=SignedAuthorityRevision(
                revision,
                canonical_digest(revision),
                root.key_id,
                private.sign(canonical_json_bytes(revision)),
            ),
            manifest=initial_manifest,
        )
        for bundle in (initial, activated):
            bundle.verify(
                trusted_signer=root,
                agent_id="agent-1",
                scope=manifest.scope,
                now=now,
            )
        wire = activated.model_dump(mode="json")
        records = []
        for purpose, payload, record in (
            (SignerPurpose.AUTHORITY_REVISION, signed.revision, wire["revision"]),
            (
                SignerPurpose.DRIVER_PROFILE,
                projections.profile.profile,
                wire["manifest"]["profiles"][0],
            ),
            (
                SignerPurpose.CERTIFICATION_MATRIX,
                projections.row.row,
                wire["manifest"]["certification_rows"][0],
            ),
            (
                SignerPurpose.BINDING_REVISION,
                projections.binding.revision,
                wire["manifest"]["bindings"][0],
            ),
            (
                SignerPurpose.DEVICE_TEST_EVIDENCE,
                projections.evidence.evidence,
                wire["manifest"]["evidence"][0],
            ),
            (
                SignerPurpose.DEVICE_OBSERVATION,
                observations.current.observation,
                ObservationJson(signed=observations.current).model_dump(mode="json")[
                    "signed"
                ],
            ),
        ):
            signer = projections.signers[record["signer_key_id"]]
            records.append(
                {
                    "purpose": purpose.value,
                    "record": record,
                    "signer": SignerJson(signer=signer).model_dump(mode="json")[
                        "signer"
                    ],
                    "canonical": canonical_json_bytes(payload).decode(),
                }
            )
        cases.append(
            {
                "microsecond": microsecond,
                "now": now.isoformat().replace("+00:00", "Z"),
                "agent_id": "agent-1",
                "scope": wire["manifest"]["scope"],
                "trusted_signer": SignerJson(signer=root).model_dump(mode="json")[
                    "signer"
                ],
                "initial_bundle": initial.model_dump(mode="json"),
                "activated_bundle": wire,
                "records": records,
            }
        )
    return {
        "contract": "inari.device-authority.test-vectors.v1",
        "notice": "Keys, hardware facts, and Passed results are test fixtures.",
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(vectors(), ensure_ascii=False, indent=2, sort_keys=True))
