from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_owner_only(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    write_bytes_owner_only(path, content.encode(encoding))


def write_bytes_owner_only(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_owner_only(tmp_path)
        os.replace(tmp_path, path)
        _chmod_owner_only(path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _chmod_owner_only(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)
