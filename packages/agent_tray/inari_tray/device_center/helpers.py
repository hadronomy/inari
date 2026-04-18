from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QLabel

from inari.models import DeviceResponse

SETTINGS_ORGANIZATION = "Inari"
SETTINGS_APPLICATION = "Inari Tray"
SETTINGS_GROUP = "device_center"
DEFAULT_EVENT_LIMIT = 50


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def coerce_geometry(value: object | None) -> QByteArray | None:
    if isinstance(value, QByteArray):
        return value
    if isinstance(value, bytes):
        return QByteArray(value)
    if isinstance(value, bytearray):
        return QByteArray(bytes(value))
    return None


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def compact_timestamp(value: datetime | None) -> str:
    if value is None:
        return "—"
    local = value.astimezone()
    now = datetime.now(tz=local.tzinfo)
    if local.date() == now.date():
        return local.strftime("%H:%M")
    if local.year == now.year:
        return local.strftime("%b %d")
    return local.strftime("%Y-%m-%d")


def yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def pretty_json(value: Any) -> str:
    if value in (None, {}, [], ()):
        return "{}"
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def string_value(value: object) -> str:
    return value if isinstance(value, str) and value else ""


def device_endpoint(device: DeviceResponse) -> str:
    host = string_value(device.metadata.get("host"))
    port = device.metadata.get("port")
    if host and port is not None:
        return f"{host}:{port}"
    if host:
        return host
    for key in ("device_uri", "queue_name", "encoding"):
        value = string_value(device.metadata.get(key))
        if value:
            return value
    return "—"


def humanize_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return type(exc).__name__
