from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from inari.service.models import DEFAULT_SERVICE_SCOPE, default_service_name

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
TrayControlMode = Literal["monitor", "spawn", "service"]
TrayServiceScope = Literal["system", "user"]


class TraySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INARI_TRAY_",
        env_file=".env",
        extra="ignore",
    )

    title: str = "Inari"
    agent_api_base_url: str = "http://127.0.0.1:7310"
    control_mode: TrayControlMode = "spawn"
    service_name: str = Field(default_factory=default_service_name)
    service_scope: TrayServiceScope = DEFAULT_SERVICE_SCOPE
    log_level: LogLevel = "INFO"
    auto_start_agent: bool = True
    auth_client_name: str = "inari-tray"
    status_reconcile_interval_seconds: float = 30.0
    event_reconnect_delay_seconds: float = 3.0
    connect_timeout_seconds: float = 2.0
    event_timeout_seconds: float = 1.0
    startup_grace_period_seconds: float = 15.0
    shutdown_started_process_on_exit: bool = True
    log_dir: Path = Path("./logs")

    @field_validator("agent_api_base_url", mode="before")
    @classmethod
    def normalize_agent_api_base_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    @field_validator("log_dir", mode="before")
    @classmethod
    def normalize_log_dir(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value)
        return value

    @property
    def agent_docs_url(self) -> str:
        return f"{self.agent_api_base_url}/docs"

    @property
    def agent_devices_url(self) -> str:
        return f"{self.agent_api_base_url}/devices"

    @property
    def agent_jobs_url(self) -> str:
        return f"{self.agent_api_base_url}/jobs"

    @property
    def agent_events_url(self) -> str:
        parts = urlsplit(self.agent_api_base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, "/events", "", ""))


@lru_cache(maxsize=1)
def get_settings() -> TraySettings:
    return TraySettings()
