from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .security.models import GatewayExposure, GatewayMode

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
PrinterMode = Literal["auto", "raw", "text", "document"]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IOT_AGENT_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 7310
    gateway_mode: GatewayMode = GatewayMode.STANDALONE
    gateway_exposure: GatewayExposure = GatewayExposure.LOOPBACK
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8069",
            "http://localhost:8069",
        ]
    )
    trusted_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost", "testserver"])
    default_printer_name: str | None = None
    log_level: LogLevel = "INFO"
    html_print_enabled: bool = True
    default_printer_mode: PrinterMode = "auto"
    temp_dir: Path = Path("./tmp")
    log_dir: Path = Path("./logs")
    runtime_database_path: Path = Path("./data/iot-agent.sqlite3")
    security_state_dir: Path = Path("./data/security")
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    tls_ca_path: Path | None = None
    https_redirect_enabled: bool = True
    local_token_ttl_seconds: int = 3600
    token_audience: str = "iot-agent.local"
    token_issuer: str | None = None
    secret_store_service_name: str = "iot-agent"
    allow_loopback_bootstrap: bool = True
    discovery_poll_interval_seconds: float = 3.0
    scheduler_poll_interval_seconds: float = 0.5
    scheduler_batch_size: int = 32
    job_max_attempts: int = 3
    job_retry_base_delay_seconds: int = 2
    job_retry_max_delay_seconds: int = 30
    job_dispatch_lease_seconds: int = 15
    job_execution_lease_seconds: int = 30
    job_heartbeat_interval_seconds: float = 5.0
    job_execution_timeout_seconds: float = 60.0
    job_lease_recovery_interval_seconds: float = 5.0
    upstream_base_url: str | None = None
    upstream_enrollment_url: str | None = None
    upstream_status_url: str | None = None
    upstream_events_url: str | None = None
    upstream_bootstrap_token: str | None = None
    gateway_sync_interval_seconds: float = 30.0
    gateway_reconnect_delay_seconds: float = 5.0
    gateway_event_timeout_seconds: float = 30.0

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def normalize_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "trusted_hosts",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "temp_dir",
        "log_dir",
        "runtime_database_path",
        "security_state_dir",
        "tls_cert_path",
        "tls_key_path",
        "tls_ca_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value)
        return value

    @field_validator("upstream_base_url", "upstream_enrollment_url", "upstream_status_url", "upstream_events_url", mode="before")
    @classmethod
    def normalize_urls(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().rstrip("/")
            return normalized or None
        return value


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    return AgentSettings()
