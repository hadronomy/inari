from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .http_client import RemoteServiceError
from .managed_work import identifier, utc_datetime
from .workload_identity import WorkloadScope


@dataclass(frozen=True, slots=True)
class InventorySite:
    site_id: str
    name: str


@dataclass(frozen=True, slots=True)
class InventoryAgent:
    agent_id: str
    site_id: str
    state: str
    last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class InventoryDevice:
    device_id: str
    agent_id: str
    site_id: str
    name: str
    kind: str
    device_class: str
    state: str
    transport: str
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class Inventory:
    organization_name: str
    observed_at: datetime
    sites: tuple[InventorySite, ...]
    agents: tuple[InventoryAgent, ...]
    devices: tuple[InventoryDevice, ...]


def inventory(document: object, scope: WorkloadScope) -> Inventory:
    """Check the whole company inventory before changing any Projection."""
    if not isinstance(document, dict) or not scope.matches(document.get("scope")):
        raise RemoteServiceError("The Controller returned another company inventory.")
    sites = tuple(
        InventorySite(identifier(item.get("site_id")), _name(item.get("name")))
        for item in _records(document.get("sites"))
    )
    site_ids = _unique(item.site_id for item in sites)
    agents = []
    for item in _records(document.get("agents")):
        health = item.get("health")
        if not isinstance(health, dict):
            raise RemoteServiceError("The Agent inventory has no connection state.")
        agent = InventoryAgent(
            identifier(item.get("agent_id")),
            identifier(item.get("site_id")),
            _choice(
                health.get("state"), {"online", "offline", "awaiting_first_contact"}
            ),
            _timestamp(health["last_seen_at"]) if health.get("last_seen_at") else None,
        )
        if agent.site_id not in site_ids:
            raise RemoteServiceError("The Agent inventory refers to another Site.")
        agents.append(agent)
    _unique(item.agent_id for item in agents)
    agent_sites = {item.agent_id: item.site_id for item in agents}
    devices = []
    for item in _records(document.get("devices")):
        device = InventoryDevice(
            identifier(item.get("device_id")),
            identifier(item.get("agent_id")),
            identifier(item.get("site_id")),
            _name(item.get("display_name"), max_bytes=1024),
            _choice(item.get("kind"), {"printer", "scale", "scanner", "display"}),
            _choice(item.get("device_class"), {"physical", "virtual"}),
            _choice(
                item.get("state"),
                {
                    "discovered",
                    "pending_approval",
                    "online",
                    "offline",
                    "degraded",
                    "blocked",
                },
            ),
            _choice(
                item.get("transport"), {"spooler", "network", "usb", "hid", "serial"}
            ),
            _timestamp(item.get("last_seen_at")),
        )
        if agent_sites.get(device.agent_id) != device.site_id:
            raise RemoteServiceError(
                "The Device inventory refers to another Agent or Site."
            )
        devices.append(device)
    _unique((item.agent_id, item.device_id) for item in devices)
    return Inventory(
        _name(document.get("organization_name")),
        _timestamp(document.get("observed_at")),
        sites,
        tuple(agents),
        tuple(devices),
    )


def _records(value: object) -> list[dict]:
    if (
        not isinstance(value, list)
        or len(value) > 2_000
        or any(not isinstance(item, dict) for item in value)
    ):
        raise RemoteServiceError(
            "The Controller inventory exceeds its contract limits."
        )
    return value


def _name(value: object, *, max_bytes: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= max_bytes
        or any(ord(char) < 32 for char in value)
    ):
        raise RemoteServiceError("The Controller inventory name is invalid.")
    return value


def _choice(value: object, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise RemoteServiceError("The Controller inventory state is invalid.")
    return value


def _timestamp(value: object) -> datetime:
    return utc_datetime(value).astimezone(UTC).replace(tzinfo=None)


def _unique(values):
    values = list(values)
    if len(set(values)) != len(values):
        raise RemoteServiceError("The Controller inventory repeats a record identity.")
    return set(values)
