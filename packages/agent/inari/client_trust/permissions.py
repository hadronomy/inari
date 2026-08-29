from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Iterable, Mapping

from .errors import ClientTrustError, ClientTrustErrorCode, PermissionDeniedError


class Permission(StrEnum):
    RECEIPT_IMAGE = "device_work:receipt_image"
    DRAWER = "device_work:drawer"
    SCALE = "device_read:scale"
    SCANNER = "device_read:scanner"
    DEVICE_TEST = "device_test:run"
    EVENTS_READ = "events:read"
    JOBS_READ = "jobs:read"
    JOBS_SUBMIT = "jobs:submit"


PermissionSet = frozenset[Permission]


_OPERATION_PERMISSIONS: Mapping[str, PermissionSet] = MappingProxyType(
    {
        "receipt_image": frozenset({Permission.RECEIPT_IMAGE}),
        "drawer": frozenset({Permission.DRAWER}),
        "scale": frozenset({Permission.SCALE}),
        "scanner": frozenset({Permission.SCANNER}),
        "device_test": frozenset({Permission.DEVICE_TEST}),
        "events": frozenset({Permission.EVENTS_READ}),
        "jobs": frozenset({Permission.JOBS_READ, Permission.JOBS_SUBMIT}),
    }
)


class PermissionCatalog:
    """The finite permission vocabulary for local Client Trust.

    Callers select permissions through this catalog. The catalog rejects
    misspelled or future-looking strings instead of treating them as granted.
    """

    RECEIPT_IMAGE = Permission.RECEIPT_IMAGE
    DRAWER = Permission.DRAWER
    SCALE = Permission.SCALE
    SCANNER = Permission.SCANNER
    DEVICE_TEST = Permission.DEVICE_TEST
    EVENTS_READ = Permission.EVENTS_READ
    JOBS_READ = Permission.JOBS_READ
    JOBS_SUBMIT = Permission.JOBS_SUBMIT

    @classmethod
    def permissions_for(cls, operation: str) -> PermissionSet:
        try:
            return _OPERATION_PERMISSIONS[operation]
        except KeyError as exc:
            raise ClientTrustError(
                ClientTrustErrorCode.UNKNOWN_PERMISSION,
                "The requested Device Work permission is not registered.",
                details={"operation": operation},
            ) from exc

    @classmethod
    def normalize(cls, permissions: Iterable[Permission | str]) -> PermissionSet:
        normalized: set[Permission] = set()
        for permission in permissions:
            try:
                normalized.add(Permission(permission))
            except ValueError as exc:
                raise ClientTrustError(
                    ClientTrustErrorCode.UNKNOWN_PERMISSION,
                    "The Client Grant contains an unknown permission.",
                    details={"permission": str(permission)},
                ) from exc
        return frozenset(normalized)

    @classmethod
    def require(
        cls,
        granted: Iterable[Permission | str],
        required: Iterable[Permission | str] | Permission | str,
    ) -> None:
        granted_set = cls.normalize(granted)
        if isinstance(required, (Permission, str)):
            required_set = cls.normalize((required,))
        else:
            required_set = cls.normalize(required)
        missing = required_set - granted_set
        if missing:
            permission = sorted(missing, key=lambda value: value.value)[0]
            raise PermissionDeniedError(permission.value)

    @classmethod
    def all(cls) -> PermissionSet:
        return frozenset(Permission)


__all__ = ["Permission", "PermissionCatalog", "PermissionSet"]
