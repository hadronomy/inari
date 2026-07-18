"""Canonical, packaged Inari identity assets."""

from __future__ import annotations

from enum import StrEnum
from importlib import resources


class BrandAsset(StrEnum):
    """Stable names for assets consumed by product surfaces."""

    APP_ICON = "inari-app-icon.svg"
    APP_ICON_1024 = "inari-icon-1024.png"
    FAVICON_DEVELOPMENT = "favicon-development.svg"
    FAVICON_PREVIEW = "favicon-preview.svg"
    FAVICON_PRODUCTION = "favicon.svg"
    LOCKUP = "inari-lockup.svg"
    LOCKUP_REVERSED = "inari-lockup-reversed.svg"
    MARK = "inari-mark.svg"
    MARK_MICRO = "inari-mark-micro.svg"
    MARK_REVERSED = "inari-mark-reversed.svg"
    MARK_TORII = "inari-mark-torii.svg"
    TRAY_ICON = "inari-tray-icon.svg"


def read_asset(asset: BrandAsset) -> bytes:
    """Read a packaged vector without exposing filesystem assumptions."""

    return resources.files(__package__).joinpath("assets", asset.value).read_bytes()


__all__ = ["BrandAsset", "read_asset"]
