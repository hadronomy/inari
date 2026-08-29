from __future__ import annotations

import ctypes
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable


class SpoolStorageError(RuntimeError):
    """Base class for content-free Device Spool storage failures."""

    def __init__(self, message: str = "The spool storage operation failed.") -> None:
        super().__init__(message)


class SpoolStorageInputError(SpoolStorageError):
    def __init__(self) -> None:
        super().__init__("The spool storage input is invalid.")


class SpoolStorageConflictError(SpoolStorageError):
    def __init__(self) -> None:
        super().__init__("The spool storage reference already exists.")


class SpoolStorageReadinessError(SpoolStorageError):
    def __init__(self) -> None:
        super().__init__("The spool storage is not ready.")


class SpoolStorageSpecialFileError(SpoolStorageError):
    def __init__(self) -> None:
        super().__init__("The spool storage object is not a regular file.")


class SpoolCommitUncertainError(SpoolStorageError):
    """Raised after promotion when directory durability is not confirmed."""

    def __init__(self, storage_ref: str) -> None:
        super().__init__("The spool commit result is uncertain.")
        self.storage_ref = storage_ref


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    storage_ref: str
    staging_path: Path


_STORAGE_REF_BYTES: Final = 32
_DEFAULT_CHUNK_BYTES: Final = 64 * 1024
_MAX_COMMIT_ATTEMPTS: Final = 8
_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)


class ArtifactFileStore:
    """Store encrypted bytes in private, opaque, atomically promoted files."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            root = Path(root)
        self.root = root
        self.staging_root = root / "staging"
        self.objects_root = root / "objects"
        try:
            _reject_symlink_ancestors(root)
            _ensure_private_directory(root)
            _ensure_private_directory(self.staging_root)
            _ensure_private_directory(self.objects_root)
            _fsync_directory(root)
            _fsync_directory(self.staging_root)
            _fsync_directory(self.objects_root)
        except SpoolStorageError:
            raise
        except (OSError, ValueError):
            raise SpoolStorageReadinessError from None

    def stage(
        self,
        chunks: Iterable[bytes],
        *,
        declared_size: int,
        max_bytes: int,
    ) -> StagedArtifact:
        _validate_bounds(declared_size, max_bytes)
        self._assert_ready()
        for _ in range(_MAX_COMMIT_ATTEMPTS):
            storage_ref = secrets.token_hex(_STORAGE_REF_BYTES)
            staging_path = self.staging_root / f".{storage_ref}.tmp"
            try:
                fd = os.open(
                    staging_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    0o600,
                )
            except FileExistsError:
                continue
            except OSError:
                raise SpoolStorageError from None

            try:
                written = _write_bounded(
                    fd,
                    chunks,
                    declared_size=declared_size,
                    max_bytes=max_bytes,
                )
                if written != declared_size:
                    raise SpoolStorageInputError
                os.fsync(fd)
                os.close(fd)
                fd = -1
                _fsync_directory(self.staging_root)
                _chmod_private_file(staging_path)
            except SpoolStorageInputError:
                _close_fd(fd)
                _remove_file(staging_path)
                raise
            except (OSError, TypeError):
                _close_fd(fd)
                _remove_file(staging_path)
                raise SpoolStorageError from None
            except Exception:
                _close_fd(fd)
                _remove_file(staging_path)
                raise SpoolStorageError from None
            return StagedArtifact(storage_ref, staging_path)
        raise SpoolStorageConflictError

    def stage_bytes(self, content: bytes, *, max_bytes: int) -> StagedArtifact:
        if not isinstance(content, bytes):
            raise SpoolStorageInputError
        return self.stage(
            (content,),
            declared_size=len(content),
            max_bytes=max_bytes,
        )

    def commit(self, staged: StagedArtifact) -> str:
        self._assert_ready()
        source = self._staged_path(staged)
        destination = self.objects_root / staged.storage_ref
        source_fd = -1
        try:
            source_fd = os.open(source, os.O_RDONLY | _NOFOLLOW)
            source_stat = _regular_file_stat(source_fd)
            _assert_destination_absent(destination)
            # A hard link publishes the complete file without replacing a
            # concurrent object. The source is removed only after publication.
            os.link(source, destination, follow_symlinks=False)
            destination_stat = destination.lstat()
            if not stat.S_ISREG(destination_stat.st_mode) or not _same_file(
                source_stat, destination_stat
            ):
                _remove_file(destination)
                raise SpoolStorageSpecialFileError
        except FileExistsError:
            raise SpoolStorageConflictError from None
        except FileNotFoundError:
            raise SpoolStorageInputError from None
        except SpoolStorageError:
            raise
        except OSError:
            raise SpoolStorageError from None
        finally:
            _close_fd(source_fd)

        try:
            os.unlink(source)
            _fsync_directory(self.staging_root)
            _fsync_directory(self.objects_root)
        except (FileNotFoundError, OSError):
            raise SpoolCommitUncertainError(staged.storage_ref) from None
        return staged.storage_ref

    def recover_staged(self, storage_ref: str) -> StagedArtifact | None:
        """Return a verified staged artifact that can resume publication."""

        self._validate_ref(storage_ref)
        self._assert_ready()
        path = self.staging_root / f".{storage_ref}.tmp"
        try:
            _assert_regular_file(path, missing_error=FileNotFoundError)
        except FileNotFoundError:
            return None
        return StagedArtifact(storage_ref=storage_ref, staging_path=path)

    def discard_staged(self, storage_ref: str) -> None:
        """Remove one staged artifact and confirm its directory update."""

        self._validate_ref(storage_ref)
        self._assert_ready()
        path = self.staging_root / f".{storage_ref}.tmp"
        try:
            _assert_regular_file(path, missing_error=FileNotFoundError)
            os.unlink(path)
            _fsync_directory(self.staging_root)
        except FileNotFoundError:
            return
        except SpoolStorageError:
            raise
        except OSError:
            raise SpoolStorageError from None

    def complete_publication(self, storage_ref: str) -> bool:
        """Finish or verify one interrupted no-overwrite publication."""

        self._validate_ref(storage_ref)
        self._assert_ready()
        destination = self.objects_root / storage_ref
        staged = self.recover_staged(storage_ref)
        object_exists = self.exists(storage_ref)
        if not object_exists:
            if staged is None:
                return False
            self.commit(staged)
            return True
        if staged is None:
            return True

        source_fd = -1
        destination_fd = -1
        try:
            source_fd = os.open(staged.staging_path, os.O_RDONLY | _NOFOLLOW)
            destination_fd = os.open(destination, os.O_RDONLY | _NOFOLLOW)
            if not _same_file(
                _regular_file_stat(source_fd),
                _regular_file_stat(destination_fd),
            ):
                raise SpoolStorageSpecialFileError
        except SpoolStorageError:
            raise
        except OSError:
            raise SpoolStorageError from None
        finally:
            _close_fd(source_fd)
            _close_fd(destination_fd)
        self.discard_staged(storage_ref)
        _fsync_directory(self.objects_root)
        return True

    def staged_references(self) -> tuple[str, ...]:
        """List verified opaque references in the private staging directory."""

        self._assert_ready()
        references: list[str] = []
        try:
            entries = tuple(self.staging_root.iterdir())
        except OSError:
            raise SpoolStorageError from None
        for path in entries:
            name = path.name
            if not name.startswith(".") or not name.endswith(".tmp"):
                raise SpoolStorageSpecialFileError
            storage_ref = name[1:-4]
            self._validate_ref(storage_ref)
            _assert_regular_file(path)
            references.append(storage_ref)
        return tuple(sorted(references))

    def object_references(self) -> tuple[str, ...]:
        """List verified opaque references in the committed object directory."""

        self._assert_ready()
        references: list[str] = []
        try:
            entries = tuple(self.objects_root.iterdir())
        except OSError:
            raise SpoolStorageError from None
        for path in entries:
            self._validate_ref(path.name)
            _assert_regular_file(path)
            references.append(path.name)
        return tuple(sorted(references))

    def read_chunks(
        self,
        storage_ref: str,
        *,
        max_bytes: int,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> Iterable[bytes]:
        _validate_max_bytes(max_bytes)
        if (
            isinstance(chunk_bytes, bool)
            or not isinstance(chunk_bytes, int)
            or chunk_bytes < 1
        ):
            raise SpoolStorageInputError
        path = self._object_path(storage_ref)

        def read() -> Iterable[bytes]:
            fd = -1
            try:
                _assert_regular_file(path)
                fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
                _assert_regular_fd(fd)
                total = 0
                while True:
                    chunk = os.read(fd, min(chunk_bytes, max_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise SpoolStorageInputError
                    yield chunk
            except SpoolStorageInputError:
                raise
            except FileNotFoundError:
                raise SpoolStorageError from None
            except OSError:
                raise SpoolStorageError from None
            finally:
                _close_fd(fd)

        return read()

    def read_bytes(self, storage_ref: str, *, max_bytes: int) -> bytes:
        try:
            return b"".join(self.read_chunks(storage_ref, max_bytes=max_bytes))
        except SpoolStorageError:
            raise
        except (MemoryError, TypeError):
            raise SpoolStorageInputError from None

    def exists(self, storage_ref: str) -> bool:
        path = self._object_path(storage_ref)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return False
        except OSError:
            raise SpoolStorageError from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise SpoolStorageSpecialFileError
        return True

    def delete(self, storage_ref: str) -> None:
        path = self._object_path(storage_ref)
        try:
            _assert_regular_file(path)
            os.unlink(path)
            _fsync_directory(self.objects_root)
        except SpoolStorageError:
            try:
                path.lstat()
            except FileNotFoundError:
                return
            raise
        except FileNotFoundError:
            return
        except OSError:
            raise SpoolStorageError from None

    def _staged_path(self, staged: StagedArtifact) -> Path:
        if not isinstance(staged, StagedArtifact):
            raise SpoolStorageInputError
        self._validate_ref(staged.storage_ref)
        if not isinstance(staged.staging_path, Path):
            raise SpoolStorageInputError
        expected = self.staging_root / f".{staged.storage_ref}.tmp"
        if staged.staging_path != expected:
            raise SpoolStorageInputError
        return expected

    def _object_path(self, storage_ref: str) -> Path:
        self._validate_ref(storage_ref)
        self._assert_ready()
        return self.objects_root / storage_ref

    def _assert_ready(self) -> None:
        try:
            _assert_private_directory(self.root)
            _assert_private_directory(self.staging_root)
            _assert_private_directory(self.objects_root)
        except SpoolStorageError:
            raise
        except OSError:
            raise SpoolStorageReadinessError from None

    @staticmethod
    def _validate_ref(storage_ref: str) -> None:
        if (
            not isinstance(storage_ref, str)
            or len(storage_ref) != _STORAGE_REF_BYTES * 2
            or any(character not in "0123456789abcdef" for character in storage_ref)
        ):
            raise SpoolStorageInputError


def _write_bounded(
    fd: int,
    chunks: Iterable[bytes],
    *,
    declared_size: int,
    max_bytes: int,
) -> int:
    written = 0
    try:
        iterator = iter(chunks)
        for chunk in iterator:
            if not isinstance(chunk, bytes):
                raise SpoolStorageInputError
            written += len(chunk)
            if written > max_bytes or written > declared_size:
                raise SpoolStorageInputError
            if chunk:
                _write_all(fd, chunk)
    except SpoolStorageInputError:
        raise
    except (OSError, TypeError):
        raise SpoolStorageError from None
    except Exception:
        raise SpoolStorageError from None
    return written


def _write_all(fd: int, chunk: bytes) -> None:
    offset = 0
    while offset < len(chunk):
        written = os.write(fd, chunk[offset:])
        if written <= 0:
            raise OSError("The spool write made no progress.")
        offset += written


def _validate_bounds(declared_size: int, max_bytes: int) -> None:
    if (
        isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or declared_size < 0
    ):
        raise SpoolStorageInputError
    _validate_max_bytes(max_bytes)
    if declared_size > max_bytes:
        raise SpoolStorageInputError


def _validate_max_bytes(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise SpoolStorageInputError


def _assert_destination_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise SpoolStorageError from None
    raise SpoolStorageConflictError


def _assert_regular_file(
    path: Path,
    *,
    missing_error: type[Exception] = SpoolStorageError,
) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise missing_error from None
    except OSError:
        raise SpoolStorageError from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise SpoolStorageSpecialFileError


def _assert_regular_fd(fd: int) -> None:
    _regular_file_stat(fd)


def _regular_file_stat(fd: int) -> os.stat_result:
    try:
        result = os.fstat(fd)
    except OSError:
        raise SpoolStorageError from None
    if not stat.S_ISREG(result.st_mode):
        raise SpoolStorageSpecialFileError
    return result


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _reject_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    for current in (absolute, *absolute.parents):
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            raise SpoolStorageReadinessError from None
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise SpoolStorageReadinessError


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        _assert_private_directory(path)
        if os.name == "posix":
            path.chmod(0o700)
    except SpoolStorageError:
        raise
    except (OSError, ValueError):
        raise SpoolStorageReadinessError from None


def _assert_private_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise SpoolStorageReadinessError from None
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise SpoolStorageReadinessError
    if os.name == "posix" and stat.S_IMODE(mode) & 0o077:
        raise SpoolStorageReadinessError


def _chmod_private_file(path: Path) -> None:
    if os.name == "posix":
        try:
            path.chmod(0o600)
        except OSError:
            raise SpoolStorageError from None


def _fsync_directory(path: Path) -> None:
    """Flush a directory, including on Windows, or fail the operation."""

    if os.name == "nt":
        _flush_windows_directory(path)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _flush_windows_directory(path: Path) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x40000000,
        0x00000001 | 0x00000002,
        None,
        0x00000003,
        0x02000000,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise OSError(ctypes.get_last_error(), "Could not open spool directory.")
    try:
        if not kernel32.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), "Could not flush spool directory.")
    finally:
        kernel32.CloseHandle(handle)


def _close_fd(fd: int) -> None:
    if fd >= 0:
        try:
            os.close(fd)
        except OSError:
            pass


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
