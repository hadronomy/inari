from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from inari.host_service.models import DEFAULT_SERVICE_SCOPE, default_service_name
from inari.windows_identity import current_package_family_name
from platformdirs import user_log_path
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
TrayControlMode = Literal["monitor", "spawn", "service"]
TrayServiceScope = Literal["system", "user"]
TrayProfile = Literal["auto", "development", "installed"]


class TraySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INARI_TRAY_",
        env_file=".env",
        extra="ignore",
    )

    profile: TrayProfile = "auto"
    title: str = "Inari"
    agent_api_base_url: str = "http://127.0.0.1:7310"
    control_mode: TrayControlMode = "spawn"
    service_name: str = Field(default_factory=default_service_name)
    service_scope: TrayServiceScope = DEFAULT_SERVICE_SCOPE
    log_level: LogLevel = "INFO"
    auto_start_agent: bool = True
    auth_client_name: str = "inari-tray"
    trust_store_service_name: str = "inari-tray"
    trust_store_path: Path | None = None
    status_reconcile_interval_seconds: float = 30.0
    event_reconnect_delay_seconds: float = 3.0
    connect_timeout_seconds: float = 2.0
    event_timeout_seconds: float = 1.0
    startup_grace_period_seconds: float = 15.0
    shutdown_started_process_on_exit: bool = True
    device_center_refresh_interval_seconds: float = 60.0
    log_dir: Path = Path("./logs")

    @model_validator(mode="after")
    def apply_runtime_profile(self) -> TraySettings:
        profile = self.profile
        if profile == "auto":
            profile = "installed" if current_package_family_name() else "development"
            self.profile = profile
        if profile == "installed":
            self.title = "Inari Device Center"
            self.control_mode = "service"
            self.auto_start_agent = False
            self.shutdown_started_process_on_exit = False
            self.trust_store_path = None
            self.log_dir = user_log_path("Inari Device Center", "Inari")
        return self

    @field_validator("agent_api_base_url", mode="before")
    @classmethod
    def normalize_agent_api_base_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    @field_validator("log_dir", "trust_store_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: object) -> object:
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
