from __future__ import annotations

import json
import logging
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import AgentSettings, write_default_config_file
from ..core.config_paths import resolve_default_path_bundle
from ..core.exceptions import AgentError
from ..runtime.models import utc_now
from ..security.files import write_text_owner_only
from ..security.models import GatewayMode
from ..security.secrets import SecretStore
from .enrollment.service import UPSTREAM_ENROLLMENT_TOKEN_KEY
from .models import UpstreamCertificateMode, UpstreamConnectionState

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..runtime.models import DeviceRecord
    from ..security.models import AgentIdentity
    from .models import UpstreamStatus


class OnboardingGateway(Protocol):
    """Gateway state needed to present onboarding progress."""

    def get_upstream_status(self) -> UpstreamStatus: ...

    def get_identity(self) -> AgentIdentity: ...


class DeviceInventory(Protocol):
    """Read-only device inventory used during onboarding."""

    def list_devices(self) -> Iterable[DeviceRecord]: ...


_INVITE_CODE_PATTERN = re.compile(
    r"^INR-?([A-Z2-7]{12})-?([A-Z2-7-]+)$",
    re.IGNORECASE,
)


class OnboardingPhase(StrEnum):
    NOT_STARTED = "not_started"
    RESTART_REQUIRED = "restart_required"
    SECURING_CONNECTION = "securing_connection"
    CONNECTING = "connecting"
    FINDING_DEVICES = "finding_devices"
    READY = "ready"
    FAILED = "failed"


@dataclass(slots=True, frozen=True)
class ParsedOnboardingInvite:
    controller_url: str
    invite_id: str
    credential: str


@dataclass(slots=True, frozen=True)
class OnboardingControllerPreview:
    invite_id: str
    controller_url: str
    controller_name: str | None
    controller_instance_id: str | None
    expires_at: datetime
    status: str
    supported_protocol_versions: tuple[str, ...]
    certificate_mode: UpstreamCertificateMode
    requires_mutual_tls_after_issuance: bool


@dataclass(slots=True, frozen=True)
class OnboardingStatus:
    phase: OnboardingPhase
    detail: str
    restart_required: bool = False
    controller_url: str | None = None
    controller_name: str | None = None
    agent_id: str | None = None
    protocol_version: str | None = None
    zenoh_namespace: str | None = None
    certificate_expires_at: datetime | None = None
    devices: tuple[DeviceRecord, ...] = ()
    completed_at: datetime | None = None
    last_error: str | None = None


class OnboardingRecord(BaseModel):
    """Validated setup progress persisted across agent restarts."""

    model_config = ConfigDict(extra="forbid")

    phase: OnboardingPhase = OnboardingPhase.NOT_STARTED
    controller_url: str | None = None
    controller_name: str | None = None
    invite_id: str | None = None
    started_at: datetime | None = None
    previous_overlay: dict[str, Any] = Field(default_factory=dict)
    overlay_path: Path | None = None
    confirmed_device_ids: tuple[str, ...] = ()
    devices_confirmed_at: datetime | None = None


@dataclass(slots=True)
class ManagedOnboardingService:
    settings: AgentSettings
    secret_store: SecretStore
    gateway_service: OnboardingGateway
    device_catalog: DeviceInventory
    status_path: Path
    http_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient

    async def preview(
        self,
        invitation: str,
        *,
        controller_url: str | None = None,
    ) -> OnboardingControllerPreview:
        parsed = self.parse_invitation(invitation, controller_url=controller_url)
        async with self.http_client_factory(timeout=5.0) as client:
            response = await client.get(
                f"{parsed.controller_url}/api/inari/v1/invitations/{parsed.invite_id}"
            )
            response.raise_for_status()
        payload = response.json()
        try:
            return OnboardingControllerPreview(
                invite_id=str(payload["invitation_id"]),
                controller_url=parsed.controller_url,
                controller_name=_optional_string(payload.get("controller_name")),
                controller_instance_id=_optional_string(
                    payload.get("controller_instance_id")
                ),
                expires_at=datetime.fromisoformat(
                    str(payload["expires_at"]).replace("Z", "+00:00")
                ),
                status=str(payload["state"]),
                supported_protocol_versions=tuple(
                    str(value)
                    for value in payload.get("supported_protocol_versions") or ()
                ),
                certificate_mode=UpstreamCertificateMode(
                    str(payload.get("certificate_mode") or "step_ca")
                ),
                requires_mutual_tls_after_issuance=bool(
                    payload.get("requires_mutual_tls_after_issuance", True)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentError(
                "ONBOARDING_PREVIEW_INVALID",
                "The Inari server returned an invalid invitation preview.",
                status_code=502,
            ) from exc

    async def start(
        self,
        invitation: str,
        *,
        controller_url: str | None = None,
    ) -> tuple[OnboardingControllerPreview, bool]:
        parsed = self.parse_invitation(invitation, controller_url=controller_url)
        preview = await self.preview(invitation, controller_url=controller_url)
        if preview.status != "created":
            raise AgentError(
                "ONBOARDING_INVITE_UNAVAILABLE",
                f"This invitation is {preview.status} and cannot be used.",
                status_code=409,
            )

        config_path, overlay_path = self._config_paths()
        if not config_path.exists():
            write_default_config_file(
                config_path,
                profile=self.settings.path_profile
                if self.settings.path_profile != "auto"
                else "production",
            )
        previous_overlay = _read_toml_if_present(overlay_path)
        overlay = _deep_merge(
            previous_overlay,
            {
                "agent": {"mode": "managed"},
                "controller": {
                    "base_url": parsed.controller_url,
                    "enrollment_url": (
                        f"{parsed.controller_url}/api/inari/v1/enrollments"
                    ),
                    "auth_provider": "controller",
                    "mtls_mode": (
                        "optional"
                        if preview.requires_mutual_tls_after_issuance
                        else "disabled"
                    ),
                    "trust_ca_bundle": True,
                },
                "certificates": {"provider": preview.certificate_mode.value},
            },
        )

        self.secret_store.set_secret(UPSTREAM_ENROLLMENT_TOKEN_KEY, parsed.credential)
        try:
            _write_toml_owner_only(overlay_path, overlay)
            self._save_record(
                OnboardingRecord(
                    phase=OnboardingPhase.RESTART_REQUIRED,
                    controller_url=parsed.controller_url,
                    controller_name=preview.controller_name,
                    invite_id=parsed.invite_id,
                    started_at=utc_now(),
                    previous_overlay=previous_overlay,
                    overlay_path=overlay_path,
                )
            )
        except Exception:
            self.secret_store.delete_secret(UPSTREAM_ENROLLMENT_TOKEN_KEY)
            raise
        return preview, self.settings.gateway_mode is not GatewayMode.MANAGED

    def status(self) -> OnboardingStatus:
        record = self._load_record()
        upstream = self.gateway_service.get_upstream_status()
        identity = self.gateway_service.get_identity()
        devices = tuple(self.device_catalog.list_devices())
        common: dict[str, Any] = {
            "controller_url": upstream.base_url or record.controller_url,
            "controller_name": upstream.controller_name or record.controller_name,
            "agent_id": identity.agent_id,
            "protocol_version": upstream.protocol_version,
            "zenoh_namespace": upstream.data_plane_namespace,
            "certificate_expires_at": (
                upstream.certificate_lifecycle.current_expires_at
                if upstream.certificate_lifecycle is not None
                else None
            ),
            "devices": devices,
            "completed_at": record.devices_confirmed_at,
            "last_error": upstream.last_error,
        }
        if (
            record.phase is OnboardingPhase.RESTART_REQUIRED
            and self.settings.gateway_mode is not GatewayMode.MANAGED
        ):
            return OnboardingStatus(
                phase=OnboardingPhase.RESTART_REQUIRED,
                detail="Restarting Inari to apply the secure connection.",
                restart_required=True,
                **common,
            )
        if upstream.state is UpstreamConnectionState.ONLINE:
            phase = (
                OnboardingPhase.READY if devices else OnboardingPhase.FINDING_DEVICES
            )
            return OnboardingStatus(
                phase=phase,
                detail=(
                    "Inari is connected and ready."
                    if devices
                    else "Inari is connected and looking for devices."
                ),
                **common,
            )
        if upstream.state in {
            UpstreamConnectionState.AUTH_FAILED,
            UpstreamConnectionState.PROTOCOL_MISMATCH,
        }:
            return OnboardingStatus(
                phase=OnboardingPhase.FAILED,
                detail=upstream.detail
                or "The secure connection could not be completed.",
                **common,
            )
        if self.settings.gateway_mode is GatewayMode.MANAGED:
            phase = (
                OnboardingPhase.CONNECTING
                if upstream.state
                in {
                    UpstreamConnectionState.CONNECTING,
                    UpstreamConnectionState.RECOVERING,
                }
                else OnboardingPhase.SECURING_CONNECTION
            )
            return OnboardingStatus(
                phase=phase,
                detail=upstream.detail or "Securing the connection to Inari.",
                **common,
            )
        return OnboardingStatus(
            phase=OnboardingPhase.NOT_STARTED,
            detail="This computer is not connected to an Inari server.",
            **common,
        )

    def confirm_devices(
        self,
        *,
        device_ids: tuple[str, ...],
        labels: dict[str, str],
        default_printer_device_id: str | None,
    ) -> OnboardingStatus:
        devices = {device.id: device for device in self.device_catalog.list_devices()}
        selected = set(device_ids)
        unknown = selected.difference(devices)
        if unknown:
            raise AgentError(
                "ONBOARDING_DEVICE_NOT_FOUND",
                "One or more selected devices are no longer available.",
                status_code=409,
                details={"device_ids": sorted(unknown)},
            )
        if not set(labels).issubset(selected):
            raise AgentError(
                "ONBOARDING_DEVICE_LABEL_INVALID",
                "Device labels may only be set for selected devices.",
                status_code=422,
            )
        normalized_labels = {
            device_id: label.strip()
            for device_id, label in labels.items()
            if label.strip()
        }
        if any(len(label) > 80 for label in normalized_labels.values()):
            raise AgentError(
                "ONBOARDING_DEVICE_LABEL_TOO_LONG",
                "Device labels must be 80 characters or fewer.",
                status_code=422,
            )
        default_printer_name = None
        if default_printer_device_id is not None:
            if default_printer_device_id not in selected:
                raise AgentError(
                    "ONBOARDING_DEFAULT_PRINTER_INVALID",
                    "The default printer must be one of the selected devices.",
                    status_code=422,
                )
            default_device = devices[default_printer_device_id]
            if default_device.kind.value != "printer":
                raise AgentError(
                    "ONBOARDING_DEFAULT_PRINTER_INVALID",
                    "The selected default device is not a printer.",
                    status_code=422,
                )
            default_printer_name = default_device.name

        _, overlay_path = self._config_paths()
        overlay = _read_toml_if_present(overlay_path)
        devices_config = overlay.setdefault("devices", {})
        devices_config["labels"] = normalized_labels
        printing = devices_config.setdefault("printing", {})
        if default_printer_name is None:
            printing.pop("default_printer", None)
        else:
            printing["default_printer"] = default_printer_name
        _write_toml_owner_only(overlay_path, overlay)
        self.settings.device_labels = normalized_labels
        self.settings.default_printer_name = default_printer_name
        record = self._load_record().model_copy(
            update={
                "confirmed_device_ids": tuple(sorted(selected)),
                "devices_confirmed_at": utc_now(),
            }
        )
        self._save_record(record)
        return self.status()

    def cancel(self) -> OnboardingStatus:
        self.secret_store.delete_secret(UPSTREAM_ENROLLMENT_TOKEN_KEY)
        record = self._load_record()
        if record.overlay_path is not None:
            if record.previous_overlay:
                _write_toml_owner_only(record.overlay_path, record.previous_overlay)
            elif record.overlay_path.exists():
                record.overlay_path.unlink()
        if self.status_path.exists():
            self.status_path.unlink()
        return OnboardingStatus(
            phase=OnboardingPhase.NOT_STARTED,
            detail="The setup invitation was cancelled.",
        )

    def parse_invitation(
        self,
        invitation: str,
        *,
        controller_url: str | None = None,
    ) -> ParsedOnboardingInvite:
        value = invitation.strip()
        if not value:
            raise _invalid_invitation()
        parsed_url = urlsplit(value)
        if parsed_url.scheme in {"inari", "http", "https"}:
            query = parse_qs(parsed_url.query)
            fragment = parse_qs(parsed_url.fragment)
            credential = _first(fragment.get("code"))
            if parsed_url.scheme == "inari":
                controller = _first(query.get("controller"))
                invite_id = _first(query.get("invite_id"))
            else:
                controller = urlunsplit(
                    (parsed_url.scheme, parsed_url.netloc, "", "", "")
                )
                invite_id = _invite_id_from_setup_path(parsed_url.path)
            if credential and controller and invite_id:
                return ParsedOnboardingInvite(
                    controller_url=_normalize_controller_url(controller),
                    invite_id=invite_id,
                    credential=credential,
                )
            raise _invalid_invitation()

        match = _INVITE_CODE_PATTERN.fullmatch(value)
        if match is None or len(match.group(2).replace("-", "")) != 32:
            raise _invalid_invitation()
        controller = controller_url or self.settings.upstream_base_url
        if controller is None:
            raise AgentError(
                "ONBOARDING_CONTROLLER_REQUIRED",
                "Enter the Inari server address with this invitation code.",
                status_code=422,
            )
        return ParsedOnboardingInvite(
            controller_url=_normalize_controller_url(controller),
            invite_id=match.group(1).upper(),
            credential=value.upper(),
        )

    def _config_paths(self) -> tuple[Path, Path]:
        config_path = self.settings.resolved_config_path
        if config_path is None:
            config_path = resolve_default_path_bundle(
                profile=self.settings.path_profile,
                working_directory=Path.cwd(),
            ).config_file
        overlay_path = config_path.with_name(
            f"{config_path.stem}.local{config_path.suffix}"
        )
        return config_path, overlay_path

    def _load_record(self) -> OnboardingRecord:
        if not self.status_path.exists():
            return OnboardingRecord()
        try:
            return OnboardingRecord.model_validate_json(
                self.status_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError):
            logger.warning(
                "Ignoring invalid onboarding progress; setup remains incomplete",
                extra={"component": "onboarding"},
            )
            return OnboardingRecord()

    def _save_record(self, record: OnboardingRecord) -> None:
        write_text_owner_only(
            self.status_path,
            json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _normalize_controller_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _invalid_invitation()
    if parsed.username is not None or parsed.password is not None:
        raise _invalid_invitation()
    host = parsed.hostname or ""
    if parsed.scheme != "https" and host not in {"127.0.0.1", "::1", "localhost"}:
        raise AgentError(
            "ONBOARDING_HTTPS_REQUIRED",
            "The Inari server must use HTTPS.",
            status_code=422,
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _invite_id_from_setup_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "setup":
        return parts[1]
    return None


def _read_toml_if_present(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload if isinstance(payload, dict) else {}


def _write_toml_owner_only(path: Path, payload: dict[str, Any]) -> None:
    write_text_owner_only(path, tomli_w.dumps(payload), encoding="utf-8")


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _invalid_invitation() -> AgentError:
    return AgentError(
        "ONBOARDING_INVITATION_INVALID",
        "This does not look like a valid Inari invitation.",
        status_code=422,
    )
