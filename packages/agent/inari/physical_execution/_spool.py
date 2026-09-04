from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from ..db.schema import (
    spool_artifacts_table,
    spool_nonce_reservations_table,
)
from ..printing.renderers import EscPosImageReceiptRenderer
from ..runtime.store import RuntimeStore
from ..spool import (
    ArtifactFileStore,
    ArtifactKind,
    EncryptedArtifact,
    SpoolCrypto,
    WrappedDataKey,
)
from ..spool.keys import SpoolRootKeyService
from ..spool.manifest import timestamp
from .models import (
    ExecutionClaim,
    ExecutionReceipt,
    PreparationFailed,
    PreparedDeviceWork,
)


_MAX_RECEIPT_BYTES = 2 * 1024 * 1024
_MAX_REPORT_BYTES = 10 * 1024 * 1024
_MAX_LABEL_BYTES = 2 * 1024 * 1024
_MAX_DERIVED_BYTES = 16 * 1024 * 1024
_RECEIPT_MEDIA_TYPE = "image/jpeg"
_REPORT_MEDIA_TYPE = "application/pdf"
_LABEL_MEDIA_TYPE = "application/vnd.zebra-zpl"
_ESC_POS_MEDIA_TYPE = "application/vnd.inari.escpos"


class EncryptedExecutionSpool:
    """Prepare deterministic printer bytes without plaintext files."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        files: ArtifactFileStore,
        root_keys: SpoolRootKeyService,
        renderer: EscPosImageReceiptRenderer,
        crypto: SpoolCrypto | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._store = store
        self._files = files
        self._root_keys = root_keys
        self._renderer = renderer
        self._crypto = crypto or SpoolCrypto()
        self._clock = clock

    def prepare(self, claim: ExecutionClaim) -> PreparedDeviceWork:
        try:
            data_key = self._data_key(claim)
            content, media_type = self._prepare_content(claim, data_key=data_key)
            return PreparedDeviceWork(
                device_id=claim.device_id,
                driver_key=claim.driver_key,
                device_name=claim.device_name,
                operation=claim.operation,
                media_type=media_type,
                content=content,
                content_sha256=sha256(content).digest(),
                deadline=claim.expires_at,
            )
        except PreparationFailed:
            raise
        except Exception:
            raise PreparationFailed() from None

    def _prepare_content(
        self, claim: ExecutionClaim, *, data_key: bytes
    ) -> tuple[bytes, str]:
        if claim.media_type == _RECEIPT_MEDIA_TYPE:
            return self._prepare_receipt(claim, data_key=data_key), _ESC_POS_MEDIA_TYPE
        limit = {
            _REPORT_MEDIA_TYPE: _MAX_REPORT_BYTES,
            _LABEL_MEDIA_TYPE: _MAX_LABEL_BYTES,
        }.get(claim.media_type)
        if limit is None:
            raise PreparationFailed(
                "document_policy_rejected", "print.document_policy_rejected"
            )
        return (
            self._decrypt(
                claim,
                claim.original,
                data_key=data_key,
                max_plaintext_bytes=limit,
            ),
            claim.media_type,
        )

    def _prepare_receipt(self, claim: ExecutionClaim, *, data_key: bytes) -> bytes:
        derived = self._derived(claim)
        if derived is not None:
            return self._decrypt(
                claim,
                derived,
                data_key=data_key,
                max_plaintext_bytes=_MAX_DERIVED_BYTES,
            )
        original = self._decrypt(
            claim,
            claim.original,
            data_key=data_key,
            max_plaintext_bytes=_MAX_RECEIPT_BYTES,
        )
        rendered = self._renderer.render(original, mime_type=claim.media_type)
        if not rendered or len(rendered) > _MAX_DERIVED_BYTES:
            raise PreparationFailed(
                "document_policy_rejected", "print.document_policy_rejected"
            )
        return self._persist_derived(claim, rendered=rendered, data_key=data_key)

    def release(self, claim: ExecutionClaim, receipt: ExecutionReceipt) -> None:
        del receipt
        now = self._clock().astimezone(UTC)
        with self._store.connection() as connection:
            rows = tuple(
                connection.execute(
                    select(spool_artifacts_table).where(
                        spool_artifacts_table.c.job_id == claim.job_id,
                        spool_artifacts_table.c.state == "delete_pending",
                        spool_artifacts_table.c.delete_after <= timestamp(now),
                    )
                ).mappings()
            )
        for row in rows:
            self._files.delete(str(row["storage_ref"]))
            with self._store.immediate_transaction() as connection:
                connection.execute(
                    update(spool_artifacts_table)
                    .where(
                        spool_artifacts_table.c.id == row["id"],
                        spool_artifacts_table.c.state == "delete_pending",
                    )
                    .values(
                        state="deleted",
                        deleted_at=timestamp(now),
                        last_delete_error_code=None,
                    )
                )

    def _data_key(self, claim: ExecutionClaim) -> bytes:
        root_key = self._root_keys.read(claim.key.root_version)
        if root_key is None:
            raise PreparationFailed()
        return self._crypto.unwrap_data_key(
            wrapped=WrappedDataKey(
                format_version=claim.key.format_version,
                root_key_version=claim.key.root_version,
                job_id=claim.job_id,
                intent_id=claim.intent_id,
                nonce=claim.key.wrap_nonce,
                ciphertext=claim.key.wrapped_key,
            ),
            root_key=root_key,
            job_id=claim.job_id,
            intent_id=claim.intent_id,
        )

    def _derived(self, claim: ExecutionClaim):
        with self._store.connection() as connection:
            row = (
                connection.execute(
                    select(spool_artifacts_table).where(
                        spool_artifacts_table.c.job_id == claim.job_id,
                        spool_artifacts_table.c.artifact_kind
                        == ArtifactKind.DERIVED_RASTER.value,
                        spool_artifacts_table.c.state == "committed",
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        from .models import ArtifactRef

        return ArtifactRef(
            artifact_id=str(row["id"]),
            storage_ref=str(row["storage_ref"]),
            kind=ArtifactKind(str(row["artifact_kind"])),
            format_version=int(row["format_version"]),
            nonce=bytes(row["nonce"]),
            plaintext_size_bytes=int(row["plaintext_size_bytes"]),
            ciphertext_size_bytes=int(row["ciphertext_size_bytes"]),
            plaintext_sha256=bytes(row["plaintext_sha256"]),
        )

    def _decrypt(
        self,
        claim: ExecutionClaim,
        artifact_ref,
        *,
        data_key: bytes,
        max_plaintext_bytes: int,
    ) -> bytes:
        ciphertext = self._files.read_bytes(
            artifact_ref.storage_ref,
            max_bytes=artifact_ref.ciphertext_size_bytes,
        )
        if len(ciphertext) != artifact_ref.ciphertext_size_bytes:
            raise PreparationFailed()
        content = b"".join(
            self._crypto.decrypt_artifact_chunks(
                data_key=data_key,
                artifact=EncryptedArtifact(
                    format_version=artifact_ref.format_version,
                    job_id=claim.job_id,
                    intent_id=claim.intent_id,
                    kind=artifact_ref.kind,
                    nonce=artifact_ref.nonce,
                    ciphertext=ciphertext,
                ),
                max_plaintext_bytes=max_plaintext_bytes,
            )
        )
        if (
            len(content) != artifact_ref.plaintext_size_bytes
            or sha256(content).digest() != artifact_ref.plaintext_sha256
        ):
            raise PreparationFailed()
        return content

    def _persist_derived(
        self, claim: ExecutionClaim, *, rendered: bytes, data_key: bytes
    ) -> bytes:
        encrypted = self._crypto.encrypt_artifact(
            data_key=data_key,
            job_id=claim.job_id,
            intent_id=claim.intent_id,
            kind=ArtifactKind.DERIVED_RASTER,
            plaintext=rendered,
            reserve_nonce=lambda nonce: self._reserve_nonce(claim, nonce=nonce),
        )
        staged = self._files.stage_bytes(
            encrypted.ciphertext, max_bytes=_MAX_DERIVED_BYTES + 16
        )
        storage_ref = self._files.commit(staged)
        now = self._clock().astimezone(UTC)
        with self._store.immediate_transaction() as connection:
            original = (
                connection.execute(
                    select(spool_artifacts_table).where(
                        spool_artifacts_table.c.id == claim.original.artifact_id
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(spool_artifacts_table).values(
                    id=uuid4().hex,
                    admission_id=claim.admission_id,
                    reservation_id=original["reservation_id"],
                    job_id=claim.job_id,
                    aad_job_id=claim.job_id,
                    intent_id=claim.intent_id,
                    format_version=encrypted.format_version,
                    artifact_kind=ArtifactKind.DERIVED_RASTER.value,
                    storage_ref=storage_ref,
                    nonce=encrypted.nonce,
                    plaintext_size_bytes=len(rendered),
                    ciphertext_size_bytes=len(encrypted.ciphertext),
                    plaintext_sha256=sha256(rendered).digest(),
                    state="committed",
                    retention_policy="active",
                    created_at=timestamp(now),
                    committed_at=timestamp(now),
                    delete_after=None,
                    deleted_at=None,
                    delete_attempts=0,
                    last_delete_error_code=None,
                )
            )
        return rendered

    def _reserve_nonce(self, claim: ExecutionClaim, *, nonce: bytes) -> bool:
        try:
            with self._store.immediate_transaction() as connection:
                connection.execute(
                    insert(spool_nonce_reservations_table).values(
                        id=uuid4().hex,
                        domain="artifact",
                        root_version=None,
                        planned_job_id=claim.job_id,
                        purpose="derived_raster",
                        admission_id=claim.admission_id,
                        nonce=nonce,
                        created_at=timestamp(self._clock().astimezone(UTC)),
                    )
                )
        except IntegrityError:
            return False
        return True


__all__ = ["EncryptedExecutionSpool"]
