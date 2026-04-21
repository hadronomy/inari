from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from platformdirs import PlatformDirs

PathProfile = Literal["auto", "development", "production"]
ResolvedPathProfile = Literal["development", "production"]

APP_DISPLAY_NAME = "Inari"
APP_SLUG = "inari"


@dataclass(frozen=True, slots=True)
class PlatformPathBundle:
    profile: ResolvedPathProfile
    config_file: Path
    data_dir: Path
    log_dir: Path
    temp_dir: Path
    security_state_dir: Path
    runtime_database_path: Path


def parse_path_profile(value: object) -> PathProfile:
    match value:
        case "auto":
            return "auto"
        case "development":
            return "development"
        case "production":
            return "production"
        case _:
            raise ValueError(f"Unsupported path profile: {value!r}")


def resolve_default_path_bundle(
    *,
    profile: PathProfile = "auto",
    working_directory: Path,
    config_path: Path | None = None,
    platform_system: str | None = None,
) -> PlatformPathBundle:
    effective_profile = resolve_effective_path_profile(
        profile=profile,
        working_directory=working_directory,
        config_path=config_path,
    )
    if effective_profile == "development":
        return _development_path_bundle(
            working_directory=working_directory, config_path=config_path
        )
    return _production_path_bundle(platform_system=platform_system)


def resolve_effective_path_profile(
    *,
    profile: PathProfile = "auto",
    working_directory: Path,
    config_path: Path | None = None,
) -> ResolvedPathProfile:
    profile = parse_path_profile(profile)
    if profile == "development":
        return "development"
    if profile == "production":
        return "production"
    anchor = config_path.parent if config_path is not None else working_directory
    return (
        "development"
        if find_development_workspace_root(anchor) is not None
        else "production"
    )


def default_config_candidates(
    *,
    working_directory: Path,
    profile: PathProfile = "auto",
    platform_system: str | None = None,
) -> tuple[Path, ...]:
    production_config = _production_path_bundle(
        platform_system=platform_system
    ).config_file
    development_root = _development_root(
        working_directory=working_directory, config_path=None
    )
    development_candidates = (
        (development_root / "config" / "inari.toml").resolve(),
        (development_root / "config.toml").resolve(),
    )
    if profile == "development":
        return development_candidates
    if profile == "production":
        return (production_config,)
    effective_profile = resolve_effective_path_profile(
        profile=profile,
        working_directory=working_directory,
    )
    ordered = (
        (*development_candidates, production_config)
        if effective_profile == "development"
        else (production_config, *development_candidates)
    )
    return _dedupe_paths(ordered)


def find_development_workspace_root(anchor: Path) -> Path | None:
    resolved_anchor = anchor.resolve()
    for candidate in (resolved_anchor, *resolved_anchor.parents):
        if _is_development_workspace(candidate):
            return candidate
    return None


def _development_path_bundle(
    *, working_directory: Path, config_path: Path | None
) -> PlatformPathBundle:
    workspace_root = _development_root(
        working_directory=working_directory, config_path=config_path
    )
    data_dir = (workspace_root / "data").resolve()
    return PlatformPathBundle(
        profile="development",
        config_file=(workspace_root / "config" / "inari.toml").resolve(),
        data_dir=data_dir,
        log_dir=(workspace_root / "logs").resolve(),
        temp_dir=(workspace_root / "tmp").resolve(),
        security_state_dir=(data_dir / "security").resolve(),
        runtime_database_path=(data_dir / "inari.sqlite3").resolve(),
    )


def _production_path_bundle(*, platform_system: str | None) -> PlatformPathBundle:
    current_platform = platform_system or platform.system()
    if current_platform == "Windows":
        dirs = PlatformDirs(appname=APP_DISPLAY_NAME, appauthor=False, opinion=False)
        base_dir = Path(dirs.site_data_path)
        config_dir = Path(dirs.site_config_path)
        log_dir = base_dir / "logs"
        temp_dir = base_dir / "tmp"
    elif current_platform == "Darwin":
        dirs = PlatformDirs(appname=APP_DISPLAY_NAME, appauthor=False, opinion=False)
        base_dir = Path(dirs.site_data_path)
        config_dir = Path(dirs.site_config_path)
        log_dir = Path("/Library/Logs") / APP_DISPLAY_NAME
        temp_dir = base_dir / "tmp"
    else:
        base_dir = Path("/var/lib") / APP_SLUG
        config_dir = Path("/etc") / APP_SLUG
        log_dir = Path("/var/log") / APP_SLUG
        temp_dir = Path("/var/cache") / APP_SLUG
    data_dir = base_dir / "data"
    return PlatformPathBundle(
        profile="production",
        config_file=(config_dir / "config.toml").resolve(),
        data_dir=data_dir.resolve(),
        log_dir=log_dir.resolve(),
        temp_dir=temp_dir.resolve(),
        security_state_dir=(data_dir / "security").resolve(),
        runtime_database_path=(data_dir / "inari.sqlite3").resolve(),
    )


def _development_root(*, working_directory: Path, config_path: Path | None) -> Path:
    anchor = config_path.parent if config_path is not None else working_directory
    return find_development_workspace_root(anchor) or anchor.resolve()


def _is_development_workspace(path: Path) -> bool:
    return (path / "pyproject.toml").exists() and (path / "packages" / "agent").exists()


def _dedupe_paths(
    paths: tuple[Path, ...] | list[Path] | tuple[Path, Path, Path],
) -> tuple[Path, ...]:
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return tuple(ordered)
