from pathlib import Path

from inari.spool import ArtifactFileStore


def test_artifact_storage_preserves_every_byte(tmp_path: Path) -> None:
    content = bytes(range(256)) * 3
    store = ArtifactFileStore(tmp_path / "spool")

    staged = store.stage_bytes(content, max_bytes=len(content))
    assert staged.staging_path.read_bytes() == content

    storage_ref = store.commit(staged)
    restored = b"".join(
        store.read_chunks(storage_ref, max_bytes=len(content), chunk_bytes=17)
    )
    assert restored == content
