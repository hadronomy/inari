from __future__ import annotations

import os
import stat
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from inari.spool.crypto import (
    ArtifactIntegrityError,
    ArtifactMalformedError,
    NonceReservationError,
    RandomSourceError,
    SpoolCrypto,
    artifact_aad,
    wrapped_key_aad,
)
from inari.spool.filesystem import (
    ArtifactFileStore,
    SpoolCommitUncertainError,
    SpoolStorageConflictError,
    SpoolStorageInputError,
    SpoolStorageReadinessError,
    SpoolStorageSpecialFileError,
    StagedArtifact,
)
from inari.spool.models import ArtifactKind


class FixedBytesSource:
    def __init__(self, *values: bytes) -> None:
        self.values = iter(values)

    def __call__(self, size: int) -> bytes:
        value = next(self.values)
        assert len(value) == size
        return value


class CollisionThenReserve:
    def __init__(self, collision_count: int) -> None:
        self.collision_count = collision_count
        self.seen: list[bytes] = []

    def __call__(self, nonce: bytes) -> bool:
        self.seen.append(nonce)
        return len(self.seen) > self.collision_count


def reserve_every_nonce(nonce: bytes) -> bool:
    del nonce
    return True


def test_aad_has_the_exact_versioned_length_prefixed_format() -> None:
    assert artifact_aad(
        job_id="job-1", intent_id="intent-1", kind=ArtifactKind.ORIGINAL
    ) == (
        b"inari-device-spool\0"
        + b"\x01"
        + struct.pack(">I", 5)
        + b"job-1"
        + struct.pack(">I", 8)
        + b"intent-1"
        + struct.pack(">I", 8)
        + b"original"
    )
    assert wrapped_key_aad(
        job_id="job-1", intent_id="intent-1", root_key_version=7
    ) == (
        b"inari-device-spool\0"
        + b"\x01"
        + struct.pack(">I", 5)
        + b"job-1"
        + struct.pack(">I", 8)
        + b"intent-1"
        + b"\0wrapped-data-key"
        + struct.pack(">I", 7)
    )


def test_artifact_encryption_binds_all_metadata() -> None:
    crypto = SpoolCrypto(
        nonce_bytes_source=FixedBytesSource(b"a" * 12),
        data_key_bytes_source=FixedBytesSource(b"k" * 32),
    )
    key = crypto.new_data_key()
    artifact = crypto.encrypt_artifact(
        data_key=key,
        job_id="job-1",
        intent_id="intent-1",
        kind=ArtifactKind.ORIGINAL,
        plaintext=b"receipt bytes",
        reserve_nonce=reserve_every_nonce,
    )

    assert crypto.decrypt_artifact(data_key=key, artifact=artifact) == b"receipt bytes"
    mutations = (
        replace(artifact, job_id="job-2"),
        replace(artifact, intent_id="intent-2"),
        replace(artifact, kind=ArtifactKind.DERIVED_RASTER),
        replace(artifact, ciphertext=artifact.ciphertext[:-1] + b"x"),
    )
    for mutation in mutations:
        with pytest.raises(ArtifactIntegrityError) as error:
            crypto.decrypt_artifact(data_key=key, artifact=mutation)
        assert str(error.value) == "The spool artifact failed integrity verification."

    with pytest.raises(ArtifactIntegrityError):
        crypto.decrypt_artifact(data_key=b"x" * 32, artifact=artifact)


def test_artifact_nonce_collision_retries_until_reservation_succeeds() -> None:
    reservation = CollisionThenReserve(collision_count=1)
    crypto = SpoolCrypto(nonce_bytes_source=FixedBytesSource(b"a" * 12, b"b" * 12))

    artifact = crypto.encrypt_artifact(
        data_key=b"k" * 32,
        job_id="job-1",
        intent_id="intent-1",
        kind=ArtifactKind.ORIGINAL,
        plaintext=b"receipt",
        reserve_nonce=reservation,
    )

    assert artifact.nonce == b"b" * 12
    assert reservation.seen == [b"a" * 12, b"b" * 12]


def test_artifact_nonce_collision_retry_is_bounded() -> None:
    crypto = SpoolCrypto(nonce_bytes_source=lambda size: b"n" * size)

    with pytest.raises(NonceReservationError) as error:
        crypto.encrypt_artifact(
            data_key=b"k" * 32,
            job_id="job-1",
            intent_id="intent-1",
            kind=ArtifactKind.ORIGINAL,
            plaintext=b"receipt",
            reserve_nonce=lambda nonce: False,
        )

    assert str(error.value) == "The spool nonce could not be reserved."


def test_data_key_wrap_binds_root_key_version_and_job() -> None:
    crypto = SpoolCrypto(nonce_bytes_source=FixedBytesSource(b"w" * 12))
    data_key = b"k" * 32
    root_key = b"r" * 32
    wrapped = crypto.wrap_data_key(
        data_key=data_key,
        root_key=root_key,
        root_key_version=7,
        job_id="job-1",
        intent_id="intent-1",
        reserve_nonce=reserve_every_nonce,
    )

    assert len(wrapped.ciphertext) == 48
    assert (
        crypto.unwrap_data_key(
            wrapped=wrapped,
            root_key=root_key,
            job_id="job-1",
            intent_id="intent-1",
        )
        == data_key
    )
    mutations = (
        replace(wrapped, root_key_version=8),
        replace(wrapped, ciphertext=wrapped.ciphertext[:-1] + b"x"),
    )
    for mutation in mutations:
        with pytest.raises(ArtifactIntegrityError):
            crypto.unwrap_data_key(
                wrapped=mutation,
                root_key=root_key,
                job_id="job-1",
                intent_id="intent-1",
            )
    with pytest.raises(ArtifactIntegrityError):
        crypto.unwrap_data_key(
            wrapped=wrapped,
            root_key=b"x" * 32,
            job_id="job-1",
            intent_id="intent-1",
        )


@pytest.mark.parametrize("identifier", ["", False, None])
def test_empty_or_non_string_identifiers_are_malformed(identifier: object) -> None:
    crypto = SpoolCrypto(nonce_bytes_source=FixedBytesSource(b"n" * 12))

    with pytest.raises(ArtifactMalformedError):
        crypto.encrypt_artifact(
            data_key=b"k" * 32,
            job_id=identifier,  # type: ignore[arg-type]
            intent_id="intent-1",
            kind=ArtifactKind.ORIGINAL,
            plaintext=b"data",
            reserve_nonce=reserve_every_nonce,
        )


def test_malformed_objects_and_wrapped_ciphertext_are_rejected() -> None:
    crypto = SpoolCrypto()

    with pytest.raises(ArtifactMalformedError):
        crypto.decrypt_artifact(data_key=b"k" * 32, artifact=object())  # type: ignore[arg-type]
    with pytest.raises(ArtifactMalformedError):
        crypto.unwrap_data_key(
            wrapped=object(),  # type: ignore[arg-type]
            root_key=b"r" * 32,
            job_id="job-1",
            intent_id="intent-1",
        )
    wrapped = crypto.wrap_data_key(
        data_key=b"k" * 32,
        root_key=b"r" * 32,
        root_key_version=1,
        job_id="job-1",
        intent_id="intent-1",
        reserve_nonce=reserve_every_nonce,
    )
    with pytest.raises(ArtifactMalformedError):
        crypto.unwrap_data_key(
            wrapped=replace(wrapped, ciphertext=b"x" * 47),
            root_key=b"r" * 32,
            job_id="job-1",
            intent_id="intent-1",
        )
    with pytest.raises(ArtifactMalformedError):
        crypto.wrap_data_key(
            data_key=b"k" * 32,
            root_key=b"r" * 32,
            root_key_version=True,
            job_id="job-1",
            intent_id="intent-1",
            reserve_nonce=reserve_every_nonce,
        )


def test_random_sources_and_reservation_errors_are_normalized() -> None:
    def broken_source(size: int) -> bytes:
        del size
        raise OSError("secret platform detail")

    crypto = SpoolCrypto(data_key_bytes_source=broken_source)
    with pytest.raises(RandomSourceError) as random_error:
        crypto.new_data_key()
    assert str(random_error.value) == "The spool random source failed."

    crypto = SpoolCrypto(nonce_bytes_source=lambda size: b"n" * size)

    def broken_reservation(nonce: bytes) -> bool:
        del nonce
        raise OSError("database path")

    with pytest.raises(NonceReservationError) as reservation_error:
        crypto.encrypt_artifact(
            data_key=b"k" * 32,
            job_id="job-1",
            intent_id="intent-1",
            kind=ArtifactKind.ORIGINAL,
            plaintext=b"data",
            reserve_nonce=broken_reservation,
        )
    assert str(reservation_error.value) == "The spool nonce could not be reserved."


def test_opaque_artifact_store_streams_with_hard_bounds(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage(
        (b"cipher", b"text only"),
        declared_size=15,
        max_bytes=15,
    )

    assert staged.storage_ref
    assert "ciphertext" not in staged.storage_ref
    assert store.exists(staged.storage_ref) is False

    storage_ref = store.commit(staged)

    assert store.exists(storage_ref)
    assert b"".join(store.read_chunks(storage_ref, max_bytes=15, chunk_bytes=4)) == (
        b"ciphertext only"
    )
    assert store.read_bytes(storage_ref, max_bytes=15) == b"ciphertext only"
    assert list((tmp_path / "spool" / "staging").iterdir()) == []

    store.delete(storage_ref)
    assert store.exists(storage_ref) is False


def test_artifact_store_recovers_or_discards_staged_work(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"ciphertext", max_bytes=10)

    assert store.recover_staged(staged.storage_ref) == staged
    assert store.staged_references() == (staged.storage_ref,)
    assert store.object_references() == ()
    store.discard_staged(staged.storage_ref)
    assert store.recover_staged(staged.storage_ref) is None
    assert store.staged_references() == ()
    store.discard_staged(staged.storage_ref)


def test_artifact_store_rejects_a_special_staging_object(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"ciphertext", max_bytes=10)
    staged.staging_path.unlink()
    staged.staging_path.symlink_to(tmp_path)

    with pytest.raises(SpoolStorageSpecialFileError):
        store.recover_staged(staged.storage_ref)
    with pytest.raises(SpoolStorageSpecialFileError):
        store.discard_staged(staged.storage_ref)


def test_artifact_store_finishes_an_interrupted_publication(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"ciphertext", max_bytes=10)
    object_path = store.objects_root / staged.storage_ref
    os.link(staged.staging_path, object_path)

    assert store.complete_publication(staged.storage_ref) is True
    assert store.exists(staged.storage_ref)
    assert store.recover_staged(staged.storage_ref) is None
    assert store.read_bytes(staged.storage_ref, max_bytes=10) == b"ciphertext"


def test_artifact_store_rejects_mismatched_publication_links(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"ciphertext", max_bytes=10)
    (store.objects_root / staged.storage_ref).write_bytes(b"different")

    with pytest.raises(SpoolStorageSpecialFileError):
        store.complete_publication(staged.storage_ref)


def test_artifact_store_rejects_oversize_and_size_mismatch(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")

    with pytest.raises(SpoolStorageInputError):
        store.stage((b"too large",), declared_size=9, max_bytes=8)
    with pytest.raises(SpoolStorageInputError):
        store.stage((b"short",), declared_size=6, max_bytes=6)


def test_staging_handle_cannot_be_constructed_or_reused(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")

    with pytest.raises(TypeError):
        StagedArtifact()

    staged = store.stage_bytes(b"ciphertext", max_bytes=10)
    store.commit(staged)
    with pytest.raises(SpoolStorageInputError):
        store.commit(staged)


def test_commit_never_overwrites_an_existing_object(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"new", max_bytes=3)
    destination = tmp_path / "spool" / "objects" / staged.storage_ref
    destination.write_bytes(b"existing")

    with pytest.raises(SpoolStorageConflictError):
        store.commit(staged)

    assert destination.read_bytes() == b"existing"


def test_commit_rejects_a_changed_source_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"expected", max_bytes=8)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"different")
    real_link = os.link

    def link_replacement(
        source: Path,
        destination: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        del source
        real_link(replacement, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("inari.spool.filesystem.os.link", link_replacement)

    with pytest.raises(SpoolStorageSpecialFileError):
        store.commit(staged)

    assert not (tmp_path / "spool" / "objects" / staged.storage_ref).exists()
    assert staged.staging_path.read_bytes() == b"expected"


def test_post_promotion_fsync_failure_is_explicitly_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"ciphertext", max_bytes=10)

    def fail_directory_sync(path: Path) -> None:
        del path
        raise OSError("volume unavailable")

    monkeypatch.setattr("inari.spool.filesystem._fsync_directory", fail_directory_sync)

    with pytest.raises(SpoolCommitUncertainError) as error:
        store.commit(staged)

    assert error.value.storage_ref == staged.storage_ref
    assert (tmp_path / "spool" / "objects" / staged.storage_ref).is_file()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_spool_directories_and_artifacts_are_private(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")
    staged = store.stage_bytes(b"ciphertext", max_bytes=10)
    staging_path = next((tmp_path / "spool" / "staging").iterdir())
    assert stat.S_IMODE(staging_path.stat().st_mode) == 0o600
    storage_ref = store.commit(staged)
    object_path = tmp_path / "spool" / "objects" / storage_ref

    for directory in (
        tmp_path / "spool",
        tmp_path / "spool" / "staging",
        tmp_path / "spool" / "objects",
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(object_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem contract")
def test_spool_rejects_symlink_parent_and_special_objects(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(SpoolStorageReadinessError):
        ArtifactFileStore(linked_parent / "spool")

    store = ArtifactFileStore(real_parent / "spool")
    with pytest.raises(SpoolStorageReadinessError):
        ArtifactFileStore(linked_parent / "spool")

    storage_ref = "a" * 64
    special_path = real_parent / "spool" / "objects" / storage_ref
    os.mkfifo(special_path)

    with pytest.raises(SpoolStorageSpecialFileError):
        store.exists(storage_ref)
    with pytest.raises(SpoolStorageSpecialFileError):
        store.read_bytes(storage_ref, max_bytes=32)


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path / "spool")

    store.delete("a" * 64)
