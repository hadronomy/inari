from __future__ import annotations

from collections.abc import Iterator

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
