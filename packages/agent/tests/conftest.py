from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import shutil

import pytest

from inari.config import clear_settings_cache


@pytest.fixture(autouse=True)
def _clear_cached_settings() -> Iterator[None]:
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def ample_spool_volume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Capacity tests override this observation to exercise each reserve limit.
    usage_type = type(shutil.disk_usage(tmp_path))
    usage = usage_type(100 * 1024**3, 10 * 1024**3, 90 * 1024**3)
    monkeypatch.setattr("inari.spool.limits.shutil.disk_usage", lambda path: usage)
