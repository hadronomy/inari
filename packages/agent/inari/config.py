from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import get_args, get_origin
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .config_paths import (
    PathProfile,
    PlatformPathBundle,
    default_config_candidates,
    parse_path_profile,
    resolve_default_path_bundle,
    resolve_effective_path_profile,
)
from .gateway.models import (
    MutualTlsMode,
    ZenohSessionMode,
    UpstreamAuthMode,
    UpstreamCertificateMode,
    UpstreamEdgeProvider,
)
from .security.models import GatewayExposure, GatewayMode

ENV_PREFIX = "INARI_"
CONFIG_ENV_VAR = f"{ENV_PREFIX}CONFIG"
EXAMPLE_CONFIG_FILENAME = "config.example.toml"

_TEMPLATE_HEADER_COMMENTS = [
    "Generated configuration template for inari.",
    "Uncomment only the settings you want to override.",
    "Commented values show the built-in default or a recommended example.",
    "Environment variables with the INARI_ prefix still override file values.",
]

_SECTION_COMMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("agent",): ("Agent-wide deployment mode and identity-level behavior.",),
    ("api",): (
        "Local API listener and exposure settings.",
        "This is the browser, tray, and local-operator boundary.",
    ),
    ("api", "cors"): (
        "Browser access policy for local UI clients such as Odoo or the tray.",
    ),
    ("api", "tls"): (
        "Inbound TLS files for LAN exposure. Loopback-only deployments usually leave these unset.",
    ),
    ("logging",): ("Logging verbosity and log file location.",),
    ("storage",): (
        "Filesystem locations and local state storage.",
        "Leave these commented to use the platform defaults.",
    ),
    ("devices", "printing"): (
        "Default printer behavior and optional network printer definitions.",
    ),
    ("runtime", "discovery"): ("Device discovery cadence.",),
    ("runtime", "scheduler"): ("Job scheduler tuning.",),
    ("runtime", "jobs", "retry"): ("Job retry policy.",),
    ("runtime", "jobs", "lease"): ("Job lease, heartbeat, and recovery tuning.",),
    ("runtime", "jobs", "execution"): ("Job execution timeout policy.",),
    ("auth", "local"): (
        "Local auth settings for trusted loopback clients such as the tray.",
    ),
    ("auth", "zitadel"): (
        "ZITADEL service-account settings used when managed enrollment is authorized through ZITADEL.",
    ),
    ("controller",): (
        "Managed controller settings.",
        "Enrollment stays on HTTPS, while the steady-state managed data plane uses Zenoh.",
    ),
    ("controller", "bootstrap"): (
        "Bootstrap material used only for first enrollment.",
        "Use a single short-lived controller-issued enrollment token for the initial managed enrollment call.",
    ),
    ("controller", "sync"): ("Managed status publication cadence.",),
    ("controller", "queue"): ("Managed outbox publication tuning.",),
    ("controller", "reconnect"): ("Managed reconnect timing.",),
    ("controller", "backoff"): ("Managed failure backoff tuning.",),
    ("controller", "recovery"): ("Managed reconnect-recovery behavior.",),
    ("transport", "zenoh"): (
        "Zenoh data-plane overrides and local fallback settings.",
        "The controller normally returns these details during enrollment.",
    ),
    ("certificates",): ("Managed certificate provider selection.",),
    ("certificates", "step_ca"): (
        "step-ca certificate bootstrap and renewal settings.",
        "In the normal managed flow, the controller returns these values during enrollment.",
        "Leave them commented unless you want explicit local overrides or fallback knowledge of the CA.",
    ),
}

_FIELD_COMMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("config_version",): ("Schema version for this TOML format.",),
    ("agent", "mode"): (
        "Run purely local (`standalone`) or connect to an upstream controller (`managed`).",
    ),
    ("api", "host"): ("Bind host for the local HTTP API.",),
    ("api", "port"): ("Bind port for the local HTTP API.",),
    ("api", "allowed_hosts"): ("Allowed Host headers for incoming requests.",),
    ("api", "exposure"): ("Expose the API only on loopback or to the LAN.",),
    ("api", "https_redirect"): ("Redirect HTTP to HTTPS when LAN TLS is enabled.",),
    ("api", "cors", "allowed_origins"): (
        "Browser origins allowed to call the local API.",
    ),
    ("logging", "level"): ("Application log verbosity.",),
    ("logging", "directory"): ("Directory for rotated agent logs.",),
    ("storage", "profile"): (
        "Choose development or production path defaults.",
        "`production` is the recommended explicit operator setting.",
    ),
    ("storage", "data_dir"): ("Base directory for runtime state files.",),
    ("storage", "temp_dir"): ("Directory for temporary files.",),
    ("storage", "database_path"): ("SQLite database file path.",),
    ("storage", "security_state_dir"): (
        "Directory for identities, enrollment state, and fallback local secrets.",
    ),
    ("storage", "secret_store_service_name"): (
        "OS credential store namespace used by the agent.",
    ),
    ("devices", "printing", "default_printer"): (
        "Preferred printer label when jobs do not target a specific device.",
    ),
    ("devices", "printing", "default_transport"): (
        "Default transport strategy when a driver supports more than one mode.",
    ),
    ("devices", "printing", "enable_html"): ("Allow HTML receipt/document rendering.",),
    ("runtime", "discovery", "interval"): (
        "How often the agent refreshes device discovery.",
    ),
    ("runtime", "scheduler", "interval"): (
        "How often the scheduler looks for runnable jobs.",
    ),
    ("runtime", "scheduler", "batch_size"): (
        "Maximum number of jobs leased per scheduling pass.",
    ),
    ("runtime", "jobs", "retry", "max_attempts"): (
        "Maximum attempts before a job is marked failed.",
    ),
    ("runtime", "jobs", "retry", "base_delay"): (
        "Initial retry delay after a failed job attempt.",
    ),
    ("runtime", "jobs", "retry", "max_delay"): (
        "Upper bound for exponential retry delays.",
    ),
    ("runtime", "jobs", "lease", "dispatch_ttl"): (
        "How long a dispatch lease is held before another worker may reclaim it.",
    ),
    ("runtime", "jobs", "lease", "execution_ttl"): (
        "How long a worker execution lease remains valid without a heartbeat.",
    ),
    ("runtime", "jobs", "lease", "heartbeat_interval"): (
        "How often active workers refresh their job lease.",
    ),
    ("runtime", "jobs", "lease", "recovery_interval"): (
        "How often the agent scans for expired leases.",
    ),
    ("runtime", "jobs", "execution", "timeout"): (
        "Hard timeout for a single job execution.",
    ),
    ("auth", "local", "allow_loopback_bootstrap"): (
        "Allow local bootstrap token minting for trusted loopback clients.",
    ),
    ("auth", "local", "token_ttl"): ("Lifetime of locally issued bearer tokens.",),
    ("auth", "local", "audience"): ("Audience claim for locally issued tokens.",),
    ("auth", "local", "issuer"): ("Issuer claim for locally issued tokens.",),
    ("api", "tls", "cert_path"): ("PEM certificate file presented by the local API.",),
    ("api", "tls", "key_path"): ("Private key for the local API certificate.",),
    ("api", "tls", "ca_path"): ("Optional custom CA bundle trusted by the agent.",),
    ("controller", "base_url"): ("Base URL for the external controller.",),
    ("controller", "enrollment_url"): (
        "Explicit enrollment endpoint. Leave commented to derive from `base_url` if your controller supports it.",
    ),
    ("controller", "auth_provider"): (
        "How the agent authenticates to the controller during enrollment.",
    ),
    ("controller", "edge_profile"): (
        "Controller edge layout. `caddy` enables stricter profile validation.",
    ),
    ("controller", "mtls_mode"): (
        "Whether upstream client-certificate authentication is disabled, optional, or required.",
        "Recommended production posture: keep this `optional` for bootstrap, then let the connection harden to required once a managed client certificate has been issued.",
    ),
    ("controller", "trust_ca_bundle"): (
        "Trust the managed CA bundle for outbound TLS validation.",
    ),
    ("controller", "bootstrap", "enrollment_token"): (
        "Short-lived controller-issued bootstrap credential used as a Bearer token during enrollment.",
    ),
    ("controller", "sync", "status_interval"): (
        "How often the latest gateway status is published onto the managed data plane.",
    ),
    ("controller", "queue", "batch_size"): (
        "Maximum queued outbound messages sent in one batch.",
    ),
    ("controller", "queue", "poll_interval"): (
        "How often the agent flushes queued runtime events and command results onto Zenoh.",
    ),
    ("controller", "reconnect", "initial_delay"): (
        "Base reconnect delay after managed data-plane disconnects.",
    ),
    ("controller", "backoff", "base"): (
        "Starting backoff value for repeated upstream failures.",
    ),
    ("controller", "backoff", "max"): (
        "Maximum backoff value for repeated upstream failures.",
    ),
    ("controller", "recovery", "query_timeout"): (
        "Timeout used when querying controller-side command history during reconnect recovery.",
    ),
    ("transport", "zenoh", "session_mode"): ("Zenoh session mode for the agent.",),
    ("transport", "zenoh", "connect_endpoints"): (
        "Explicit Zenoh router endpoints used when you want to override the controller-provided endpoints.",
    ),
    ("transport", "zenoh", "namespace"): (
        "Explicit Zenoh namespace override for this agent's managed keyspace.",
    ),
    ("transport", "zenoh", "close_link_on_cert_expiration"): (
        "Close Zenoh links when the presented client certificate expires.",
    ),
    ("auth", "zitadel", "base_url"): ("Base URL for your ZITADEL instance.",),
    ("auth", "zitadel", "token_url"): (
        "Explicit token endpoint if it differs from the default instance URL.",
    ),
    ("auth", "zitadel", "audience"): ("Audience requested for controller tokens.",),
    ("auth", "zitadel", "service_account_key_path"): (
        "Path to the ZITADEL service-account JSON file.",
    ),
    ("auth", "zitadel", "service_user_id"): (
        "Service user id when configuring the assertion components manually.",
    ),
    ("auth", "zitadel", "key_id"): (
        "Key identifier for the service-account private key.",
    ),
    ("auth", "zitadel", "private_key_path"): (
        "Path to the service-account private key PEM.",
    ),
    ("auth", "zitadel", "assertion_algorithm"): (
        "Signing algorithm used for the private-key JWT assertion.",
    ),
    ("auth", "zitadel", "requested_scopes"): ("Scopes requested from ZITADEL.",),
    ("auth", "zitadel", "token_refresh_skew"): (
        "How early to refresh ZITADEL access tokens.",
    ),
    ("certificates", "provider"): (
        "How the agent obtains and refreshes managed client certificates.",
    ),
    ("certificates", "step_ca", "url"): (
        "Base URL for the private step-ca instance when you want to override the controller-provided CA URL.",
    ),
    ("certificates", "step_ca", "sign_url"): (
        "Explicit sign endpoint override when it cannot be derived from `url` or the controller enrollment response.",
    ),
    ("certificates", "step_ca", "renew_url"): (
        "Explicit renew endpoint override when it cannot be derived from `url` or the controller enrollment response.",
    ),
    ("certificates", "step_ca", "root_fingerprint"): (
        "Expected fingerprint of the step-ca root certificate. The controller normally provides this during enrollment.",
    ),
    ("certificates", "step_ca", "requested_sans"): (
        "Additional SANs requested for the managed client certificate beyond the agent's default identity.",
    ),
    ("certificates", "step_ca", "renewal_skew"): (
        "How early to renew managed certificates before expiry after the initial controller-mediated bootstrap.",
    ),
    ("certificates", "step_ca", "lifecycle_interval"): (
        "How often the dedicated managed-certificate lifecycle loop inspects, renews, and repairs certificate state.",
    ),
}

_FIELD_EXAMPLES: dict[tuple[str, ...], Any] = {
    ("logging", "directory"): "./logs",
    ("storage", "profile"): "production",
    ("storage", "data_dir"): "./data",
    ("storage", "temp_dir"): "./tmp",
    ("storage", "database_path"): "./data/inari.sqlite3",
    ("storage", "security_state_dir"): "./data/security",
    ("devices", "printing", "default_printer"): "Kitchen Printer",
    ("auth", "local", "issuer"): "inari.local",
    ("api", "tls", "cert_path"): "./certs/agent.crt",
    ("api", "tls", "key_path"): "./certs/agent.key",
    ("api", "tls", "ca_path"): "./certs/ca.crt",
    ("controller", "base_url"): "https://controller.example.com",
    (
        "controller",
        "enrollment_url",
    ): "https://bootstrap.controller.example.com/api/inari/enroll",
    ("controller", "bootstrap", "enrollment_token"): "enrollment-token",
    (
        "transport",
        "zenoh",
        "connect_endpoints",
    ): ["tls/router1.example.com:7447", "tls/router2.example.com:7447"],
    ("transport", "zenoh", "namespace"): "iot/v1/agents/agt_123",
    ("auth", "zitadel", "base_url"): "https://zitadel.example.com",
    ("auth", "zitadel", "token_url"): "https://zitadel.example.com/oauth/v2/token",
    ("auth", "zitadel", "audience"): "https://controller.example.com",
    (
        "auth",
        "zitadel",
        "service_account_key_path",
    ): "./secrets/zitadel-service-account.json",
    ("auth", "zitadel", "service_user_id"): "123456789012345678",
    ("auth", "zitadel", "key_id"): "key-id",
    ("auth", "zitadel", "private_key_path"): "./secrets/zitadel-private-key.pem",
    ("certificates", "step_ca", "url"): "https://ca.example.com",
    ("certificates", "step_ca", "sign_url"): "https://ca.example.com/1.0/sign",
    ("certificates", "step_ca", "renew_url"): "https://ca.example.com/1.0/renew",
    (
        "certificates",
        "step_ca",
        "root_fingerprint",
    ): "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    ("runtime", "discovery", "interval"): "3s",
    ("runtime", "scheduler", "interval"): "500ms",
    ("runtime", "jobs", "retry", "base_delay"): "2s",
    ("runtime", "jobs", "retry", "max_delay"): "30s",
    ("runtime", "jobs", "lease", "dispatch_ttl"): "15s",
    ("runtime", "jobs", "lease", "execution_ttl"): "30s",
    ("runtime", "jobs", "lease", "heartbeat_interval"): "5s",
    ("runtime", "jobs", "lease", "recovery_interval"): "5s",
    ("runtime", "jobs", "execution", "timeout"): "60s",
    ("auth", "local", "token_ttl"): "1h",
    ("auth", "zitadel", "token_refresh_skew"): "120s",
    ("controller", "sync", "status_interval"): "30s",
    ("controller", "queue", "poll_interval"): "500ms",
    ("controller", "reconnect", "initial_delay"): "5s",
    ("controller", "backoff", "base"): "1s",
    ("controller", "backoff", "max"): "60s",
    ("controller", "recovery", "query_timeout"): "10s",
    ("certificates", "step_ca", "renewal_skew"): "1h",
    ("certificates", "step_ca", "lifecycle_interval"): "60s",
}

_ARRAY_TABLE_EXAMPLES: dict[tuple[str, ...], tuple[dict[str, Any], ...]] = {
    ("devices", "printing", "printers"): (
        {
            "name": "Kitchen LAN Printer",
            "host": "192.168.1.40",
            "port": 9100,
            "default": False,
            "transport": "raw",
            "cash_drawer": True,
            "text_enabled": False,
            "document_enabled": False,
            "encoding": "utf-8",
        },
    ),
}

_ARRAY_TABLE_COMMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("devices", "printing", "printers"): (
        "Repeat this block for each raw TCP or receipt printer that should be managed directly by the agent.",
    ),
}

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
PrinterMode = Literal["auto", "raw", "text", "document"]


def _parse_duration_timedelta(value: object) -> timedelta:
    return timedelta(seconds=_parse_duration_seconds(value, field_name="duration"))


ConfigDuration = Annotated[
    timedelta,
    BeforeValidator(_parse_duration_timedelta),
    PlainSerializer(lambda value: _format_duration_literal(value), return_type=str),
    WithJsonSchema({"type": "string"}),
]
FloatDurationSeconds = Annotated[
    float,
    BeforeValidator(
        lambda value: _parse_duration_seconds(value, field_name="duration")
    ),
]
IntegralDurationSeconds = Annotated[
    int,
    BeforeValidator(
        lambda value: _parse_duration_int_seconds(value, field_name="duration")
    ),
]

_NESTED_MODEL_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)
_SETTINGS_MODEL_CONFIG = ConfigDict(extra="ignore", str_strip_whitespace=True)


class AgentConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    mode: GatewayMode = GatewayMode.STANDALONE


class ApiCorsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8069",
            "http://localhost:8069",
        ]
    )


class ApiTlsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    cert_path: Path | None = None
    key_path: Path | None = None
    ca_path: Path | None = None


class ApiConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    host: str = "127.0.0.1"
    port: int = 7310
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "testserver"]
    )
    exposure: GatewayExposure = GatewayExposure.LOOPBACK
    https_redirect: bool = True
    cors: ApiCorsConfig = Field(default_factory=ApiCorsConfig)
    tls: ApiTlsConfig = Field(default_factory=ApiTlsConfig)


class LoggingConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    level: LogLevel = "INFO"
    directory: Path | None = None


class StorageConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    profile: PathProfile = "auto"
    data_dir: Path | None = None
    temp_dir: Path | None = None
    database_path: Path | None = None
    security_state_dir: Path | None = None
    secret_store_service_name: str = "inari"


class NetworkPrinterConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    name: str
    host: str
    port: int = 9100
    is_default: bool = False
    preferred_transport: PrinterMode = "raw"
    cash_drawer: bool = True
    text_enabled: bool = False
    document_enabled: bool = False
    encoding: str = "utf-8"


class ManagedPrinterConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    name: str
    host: str
    port: int = 9100
    default: bool = False
    transport: PrinterMode = "raw"
    cash_drawer: bool = True
    text_enabled: bool = False
    document_enabled: bool = False
    encoding: str = "utf-8"


class DevicesPrintingConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    default_printer: str | None = None
    default_transport: PrinterMode = "auto"
    enable_html: bool = True
    printers: list[ManagedPrinterConfig] = Field(default_factory=list)


class DevicesConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    printing: DevicesPrintingConfig = Field(default_factory=DevicesPrintingConfig)


class RuntimeDiscoveryConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    interval: ConfigDuration = timedelta(seconds=3)


class RuntimeSchedulerConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    interval: ConfigDuration = timedelta(milliseconds=500)
    batch_size: int = 32


class RuntimeJobsRetryConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    max_attempts: int = 3
    base_delay: ConfigDuration = timedelta(seconds=2)
    max_delay: ConfigDuration = timedelta(seconds=30)


class RuntimeJobsLeaseConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    dispatch_ttl: ConfigDuration = timedelta(seconds=15)
    execution_ttl: ConfigDuration = timedelta(seconds=30)
    heartbeat_interval: ConfigDuration = timedelta(seconds=5)
    recovery_interval: ConfigDuration = timedelta(seconds=5)


class RuntimeJobsExecutionConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    timeout: ConfigDuration = timedelta(seconds=60)


class RuntimeJobsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    retry: RuntimeJobsRetryConfig = Field(default_factory=RuntimeJobsRetryConfig)
    lease: RuntimeJobsLeaseConfig = Field(default_factory=RuntimeJobsLeaseConfig)
    execution: RuntimeJobsExecutionConfig = Field(
        default_factory=RuntimeJobsExecutionConfig
    )


class RuntimeConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    discovery: RuntimeDiscoveryConfig = Field(default_factory=RuntimeDiscoveryConfig)
    scheduler: RuntimeSchedulerConfig = Field(default_factory=RuntimeSchedulerConfig)
    jobs: RuntimeJobsConfig = Field(default_factory=RuntimeJobsConfig)


class AuthLocalConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    allow_loopback_bootstrap: bool = True
    token_ttl: ConfigDuration = timedelta(hours=1)
    audience: str = "inari.local"
    issuer: str | None = None


class AuthZitadelConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    base_url: str | None = None
    token_url: str | None = None
    audience: str | None = None
    service_account_key_path: Path | None = None
    service_user_id: str | None = None
    key_id: str | None = None
    private_key_path: Path | None = None
    assertion_algorithm: str = "RS256"
    requested_scopes: list[str] = Field(default_factory=lambda: ["openid"])
    token_refresh_skew: ConfigDuration = timedelta(seconds=120)


class AuthConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    local: AuthLocalConfig = Field(default_factory=AuthLocalConfig)
    zitadel: AuthZitadelConfig = Field(default_factory=AuthZitadelConfig)


class ControllerBootstrapConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    enrollment_token: str | None = None


class ControllerSyncConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    status_interval: ConfigDuration = timedelta(seconds=30)


class ControllerQueueConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    batch_size: int = 128
    poll_interval: ConfigDuration = timedelta(milliseconds=500)


class ControllerReconnectConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    initial_delay: ConfigDuration = timedelta(seconds=5)


class ControllerBackoffConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    base: ConfigDuration = timedelta(seconds=1)
    max: ConfigDuration = timedelta(seconds=60)


class ControllerRecoveryConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    query_timeout: ConfigDuration = timedelta(seconds=10)


class ControllerConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    base_url: str | None = None
    enrollment_url: str | None = None
    auth_provider: UpstreamAuthMode = UpstreamAuthMode.CONTROLLER
    edge_profile: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    mtls_mode: MutualTlsMode = MutualTlsMode.OPTIONAL
    trust_ca_bundle: bool = True
    bootstrap: ControllerBootstrapConfig = Field(
        default_factory=ControllerBootstrapConfig
    )
    sync: ControllerSyncConfig = Field(default_factory=ControllerSyncConfig)
    queue: ControllerQueueConfig = Field(default_factory=ControllerQueueConfig)
    reconnect: ControllerReconnectConfig = Field(
        default_factory=ControllerReconnectConfig
    )
    backoff: ControllerBackoffConfig = Field(default_factory=ControllerBackoffConfig)
    recovery: ControllerRecoveryConfig = Field(default_factory=ControllerRecoveryConfig)


class TransportZenohConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    session_mode: ZenohSessionMode = ZenohSessionMode.CLIENT
    connect_endpoints: list[str] = Field(default_factory=list)
    namespace: str | None = None
    close_link_on_cert_expiration: bool = True


class TransportConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    zenoh: TransportZenohConfig = Field(default_factory=TransportZenohConfig)


class CertificatesStepCaConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    url: str | None = None
    sign_url: str | None = None
    renew_url: str | None = None
    root_fingerprint: str | None = None
    requested_sans: list[str] = Field(default_factory=list)
    renewal_skew: ConfigDuration = timedelta(hours=1)
    lifecycle_interval: ConfigDuration = timedelta(seconds=60)


class CertificatesConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    provider: UpstreamCertificateMode = UpstreamCertificateMode.CONTROLLER
    step_ca: CertificatesStepCaConfig = Field(default_factory=CertificatesStepCaConfig)


class AgentConfigFile(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    config_version: int = 1
    agent: AgentConfig = Field(default_factory=AgentConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    devices: DevicesConfig = Field(default_factory=DevicesConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    transport: TransportConfig = Field(default_factory=TransportConfig)
    certificates: CertificatesConfig = Field(default_factory=CertificatesConfig)

    def to_settings_payload(
        self, *, base_dir: Path, path_defaults: PlatformPathBundle
    ) -> dict[str, object]:
        data_dir = (
            _resolve_relative_path(self.storage.data_dir, base_dir)
            or path_defaults.data_dir
        )
        runtime_database_path = _resolve_relative_path(
            self.storage.database_path, base_dir
        ) or (data_dir / "inari.sqlite3")
        security_state_dir = _resolve_relative_path(
            self.storage.security_state_dir, base_dir
        ) or (data_dir / "security")
        return {
            "host": self.api.host,
            "port": self.api.port,
            "path_profile": path_defaults.profile,
            "trusted_hosts": list(self.api.allowed_hosts),
            "allowed_origins": list(self.api.cors.allowed_origins),
            "log_level": self.logging.level,
            "data_dir": data_dir,
            "log_dir": _resolve_relative_path(self.logging.directory, base_dir)
            or path_defaults.log_dir,
            "temp_dir": _resolve_relative_path(self.storage.temp_dir, base_dir)
            or path_defaults.temp_dir,
            "runtime_database_path": runtime_database_path,
            "security_state_dir": security_state_dir,
            "default_printer_name": self.devices.printing.default_printer,
            "default_printer_mode": self.devices.printing.default_transport,
            "html_print_enabled": self.devices.printing.enable_html,
            "network_printers": [
                {
                    "name": printer.name,
                    "host": printer.host,
                    "port": printer.port,
                    "is_default": printer.default,
                    "preferred_transport": printer.transport,
                    "cash_drawer": printer.cash_drawer,
                    "text_enabled": printer.text_enabled,
                    "document_enabled": printer.document_enabled,
                    "encoding": printer.encoding,
                }
                for printer in self.devices.printing.printers
            ],
            "discovery_poll_interval_seconds": _parse_duration_seconds(
                self.runtime.discovery.interval,
                field_name="runtime.discovery.interval",
            ),
            "scheduler_poll_interval_seconds": _parse_duration_seconds(
                self.runtime.scheduler.interval,
                field_name="runtime.scheduler.interval",
            ),
            "scheduler_batch_size": self.runtime.scheduler.batch_size,
            "job_max_attempts": self.runtime.jobs.retry.max_attempts,
            "job_retry_base_delay_seconds": _parse_duration_int_seconds(
                self.runtime.jobs.retry.base_delay,
                field_name="runtime.jobs.retry.base_delay",
            ),
            "job_retry_max_delay_seconds": _parse_duration_int_seconds(
                self.runtime.jobs.retry.max_delay,
                field_name="runtime.jobs.retry.max_delay",
            ),
            "job_dispatch_lease_seconds": _parse_duration_int_seconds(
                self.runtime.jobs.lease.dispatch_ttl,
                field_name="runtime.jobs.lease.dispatch_ttl",
            ),
            "job_execution_lease_seconds": _parse_duration_int_seconds(
                self.runtime.jobs.lease.execution_ttl,
                field_name="runtime.jobs.lease.execution_ttl",
            ),
            "job_heartbeat_interval_seconds": _parse_duration_seconds(
                self.runtime.jobs.lease.heartbeat_interval,
                field_name="runtime.jobs.lease.heartbeat_interval",
            ),
            "job_execution_timeout_seconds": _parse_duration_seconds(
                self.runtime.jobs.execution.timeout,
                field_name="runtime.jobs.execution.timeout",
            ),
            "job_lease_recovery_interval_seconds": _parse_duration_seconds(
                self.runtime.jobs.lease.recovery_interval,
                field_name="runtime.jobs.lease.recovery_interval",
            ),
            "gateway_mode": self.agent.mode,
            "gateway_exposure": self.api.exposure,
            "allow_loopback_bootstrap": self.auth.local.allow_loopback_bootstrap,
            "https_redirect_enabled": self.api.https_redirect,
            "secret_store_service_name": self.storage.secret_store_service_name,
            "local_token_ttl_seconds": _parse_duration_int_seconds(
                self.auth.local.token_ttl,
                field_name="auth.local.token_ttl",
            ),
            "token_audience": self.auth.local.audience,
            "token_issuer": self.auth.local.issuer,
            "tls_cert_path": _resolve_relative_path(self.api.tls.cert_path, base_dir),
            "tls_key_path": _resolve_relative_path(self.api.tls.key_path, base_dir),
            "tls_ca_path": _resolve_relative_path(self.api.tls.ca_path, base_dir),
            "upstream_base_url": self.controller.base_url,
            "upstream_enrollment_url": self.controller.enrollment_url,
            "upstream_auth_mode": self.controller.auth_provider,
            "upstream_certificate_mode": self.certificates.provider,
            "upstream_edge_provider": self.controller.edge_profile,
            "upstream_mutual_tls_mode": self.controller.mtls_mode,
            "upstream_trust_client_ca": self.controller.trust_ca_bundle,
            "upstream_enrollment_token": self.controller.bootstrap.enrollment_token,
            "gateway_sync_interval_seconds": _parse_duration_seconds(
                self.controller.sync.status_interval,
                field_name="controller.sync.status_interval",
            ),
            "gateway_reconnect_delay_seconds": _parse_duration_seconds(
                self.controller.reconnect.initial_delay,
                field_name="controller.reconnect.initial_delay",
            ),
            "gateway_outbox_batch_size": self.controller.queue.batch_size,
            "gateway_outbox_poll_interval_seconds": _parse_duration_seconds(
                self.controller.queue.poll_interval,
                field_name="controller.queue.poll_interval",
            ),
            "gateway_backoff_base_seconds": _parse_duration_seconds(
                self.controller.backoff.base,
                field_name="controller.backoff.base",
            ),
            "gateway_backoff_max_seconds": _parse_duration_seconds(
                self.controller.backoff.max,
                field_name="controller.backoff.max",
            ),
            "zenoh_session_mode": self.transport.zenoh.session_mode,
            "zenoh_connect_endpoints": list(self.transport.zenoh.connect_endpoints),
            "zenoh_namespace": self.transport.zenoh.namespace,
            "zenoh_query_timeout_seconds": _parse_duration_seconds(
                self.controller.recovery.query_timeout,
                field_name="controller.recovery.query_timeout",
            ),
            "zenoh_close_link_on_expiration": (
                self.transport.zenoh.close_link_on_cert_expiration
            ),
            "zitadel_base_url": self.auth.zitadel.base_url,
            "zitadel_token_url": self.auth.zitadel.token_url,
            "zitadel_audience": self.auth.zitadel.audience,
            "zitadel_service_account_key_path": _resolve_relative_path(
                self.auth.zitadel.service_account_key_path,
                base_dir,
            ),
            "zitadel_service_user_id": self.auth.zitadel.service_user_id,
            "zitadel_key_id": self.auth.zitadel.key_id,
            "zitadel_private_key_path": _resolve_relative_path(
                self.auth.zitadel.private_key_path,
                base_dir,
            ),
            "zitadel_assertion_algorithm": self.auth.zitadel.assertion_algorithm,
            "zitadel_requested_scopes": list(self.auth.zitadel.requested_scopes),
            "zitadel_token_refresh_skew_seconds": _parse_duration_int_seconds(
                self.auth.zitadel.token_refresh_skew,
                field_name="auth.zitadel.token_refresh_skew",
            ),
            "step_ca_url": self.certificates.step_ca.url,
            "step_ca_sign_url": self.certificates.step_ca.sign_url,
            "step_ca_renew_url": self.certificates.step_ca.renew_url,
            "step_ca_root_fingerprint": self.certificates.step_ca.root_fingerprint,
            "step_ca_requested_sans": list(self.certificates.step_ca.requested_sans),
            "step_ca_certificate_renewal_skew_seconds": _parse_duration_int_seconds(
                self.certificates.step_ca.renewal_skew,
                field_name="certificates.step_ca.renewal_skew",
            ),
            "step_ca_lifecycle_poll_interval_seconds": _parse_duration_seconds(
                self.certificates.step_ca.lifecycle_interval,
                field_name="certificates.step_ca.lifecycle_interval",
            ),
        }


class AgentSettings(BaseModel):
    model_config = _SETTINGS_MODEL_CONFIG

    host: str = "127.0.0.1"
    port: int = 7310
    path_profile: PathProfile = "auto"
    gateway_mode: GatewayMode = GatewayMode.STANDALONE
    gateway_exposure: GatewayExposure = GatewayExposure.LOOPBACK
    upstream_auth_mode: UpstreamAuthMode = UpstreamAuthMode.CONTROLLER
    upstream_certificate_mode: UpstreamCertificateMode = (
        UpstreamCertificateMode.CONTROLLER
    )
    upstream_edge_provider: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    upstream_mutual_tls_mode: MutualTlsMode = MutualTlsMode.OPTIONAL
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8069",
            "http://localhost:8069",
        ]
    )
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "testserver"]
    )
    default_printer_name: str | None = None
    log_level: LogLevel = "INFO"
    html_print_enabled: bool = True
    default_printer_mode: PrinterMode = "auto"
    network_printers: list[NetworkPrinterConfig] = Field(default_factory=list)
    data_dir: Path | None = None
    temp_dir: Path | None = None
    log_dir: Path | None = None
    runtime_database_path: Path | None = None
    security_state_dir: Path | None = None
    tls_cert_path: Path | None = None
    tls_key_path: Path | None = None
    tls_ca_path: Path | None = None
    https_redirect_enabled: bool = True
    local_token_ttl_seconds: IntegralDurationSeconds = 3600
    token_audience: str = "inari.local"
    token_issuer: str | None = None
    secret_store_service_name: str = "inari"
    allow_loopback_bootstrap: bool = True
    discovery_poll_interval_seconds: FloatDurationSeconds = 3.0
    scheduler_poll_interval_seconds: FloatDurationSeconds = 0.5
    scheduler_batch_size: int = 32
    job_max_attempts: int = 3
    job_retry_base_delay_seconds: IntegralDurationSeconds = 2
    job_retry_max_delay_seconds: IntegralDurationSeconds = 30
    job_dispatch_lease_seconds: IntegralDurationSeconds = 15
    job_execution_lease_seconds: IntegralDurationSeconds = 30
    job_heartbeat_interval_seconds: FloatDurationSeconds = 5.0
    job_execution_timeout_seconds: FloatDurationSeconds = 60.0
    job_lease_recovery_interval_seconds: FloatDurationSeconds = 5.0
    upstream_base_url: str | None = None
    upstream_enrollment_url: str | None = None
    upstream_enrollment_token: str | None = None
    upstream_trust_client_ca: bool = True
    zenoh_session_mode: ZenohSessionMode = ZenohSessionMode.CLIENT
    zenoh_connect_endpoints: list[str] = Field(default_factory=list)
    zenoh_namespace: str | None = None
    zenoh_query_timeout_seconds: FloatDurationSeconds = 10.0
    zenoh_close_link_on_expiration: bool = True
    zitadel_base_url: str | None = None
    zitadel_token_url: str | None = None
    zitadel_audience: str | None = None
    zitadel_service_account_key_path: Path | None = None
    zitadel_service_user_id: str | None = None
    zitadel_key_id: str | None = None
    zitadel_private_key_path: Path | None = None
    zitadel_assertion_algorithm: str = "RS256"
    zitadel_requested_scopes: list[str] = Field(default_factory=lambda: ["openid"])
    zitadel_token_refresh_skew_seconds: IntegralDurationSeconds = 120
    step_ca_url: str | None = None
    step_ca_sign_url: str | None = None
    step_ca_renew_url: str | None = None
    step_ca_root_fingerprint: str | None = None
    step_ca_requested_sans: list[str] = Field(default_factory=list)
    step_ca_certificate_renewal_skew_seconds: IntegralDurationSeconds = 3600
    step_ca_lifecycle_poll_interval_seconds: FloatDurationSeconds = 60.0
    gateway_sync_interval_seconds: FloatDurationSeconds = 30.0
    gateway_reconnect_delay_seconds: FloatDurationSeconds = 5.0
    gateway_outbox_batch_size: int = 128
    gateway_outbox_poll_interval_seconds: FloatDurationSeconds = 0.5
    gateway_backoff_base_seconds: FloatDurationSeconds = 1.0
    gateway_backoff_max_seconds: FloatDurationSeconds = 60.0

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir is None:
            raise RuntimeError("Agent settings are missing data_dir.")
        return self.data_dir

    @property
    def resolved_temp_dir(self) -> Path:
        if self.temp_dir is None:
            raise RuntimeError("Agent settings are missing temp_dir.")
        return self.temp_dir

    @property
    def resolved_log_dir(self) -> Path:
        if self.log_dir is None:
            raise RuntimeError("Agent settings are missing log_dir.")
        return self.log_dir

    @property
    def resolved_runtime_database_path(self) -> Path:
        if self.runtime_database_path is None:
            raise RuntimeError("Agent settings are missing runtime_database_path.")
        return self.runtime_database_path

    @property
    def resolved_security_state_dir(self) -> Path:
        if self.security_state_dir is None:
            raise RuntimeError("Agent settings are missing security_state_dir.")
        return self.security_state_dir

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def normalize_allowed_origins(cls, value: object) -> object:
        return _normalize_list_like(value)

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> object:
        return _normalize_list_like(value)

    @field_validator(
        "data_dir",
        "temp_dir",
        "log_dir",
        "runtime_database_path",
        "security_state_dir",
        "tls_cert_path",
        "tls_key_path",
        "tls_ca_path",
        "zitadel_service_account_key_path",
        "zitadel_private_key_path",
        mode="before",
    )
    @classmethod
    def normalize_paths(cls, value: object) -> object:
        if isinstance(value, str):
            value = Path(value)
        if isinstance(value, Path) and not value.is_absolute():
            return value.resolve()
        return value

    @field_validator(
        "upstream_base_url",
        "upstream_enrollment_url",
        "zitadel_base_url",
        "zitadel_token_url",
        "step_ca_url",
        "step_ca_sign_url",
        "step_ca_renew_url",
        mode="before",
    )
    @classmethod
    def normalize_urls(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().rstrip("/")
            return normalized or None
        return value

    @field_validator(
        "zitadel_requested_scopes",
        "step_ca_requested_sans",
        "zenoh_connect_endpoints",
        mode="before",
    )
    @classmethod
    def normalize_list_values(cls, value: object) -> object:
        return _normalize_list_like(value)

    @field_validator("network_printers", mode="before")
    @classmethod
    def normalize_network_printers(cls, value: object) -> object:
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        return value

    @model_validator(mode="after")
    def apply_path_defaults(self) -> AgentSettings:
        defaults = resolve_default_path_bundle(
            profile=self.path_profile,
            working_directory=Path.cwd(),
        )
        self.path_profile = resolve_effective_path_profile(
            profile=self.path_profile,
            working_directory=Path.cwd(),
        )
        data_dir = self.data_dir or defaults.data_dir
        self.data_dir = data_dir
        self.log_dir = self.log_dir or defaults.log_dir
        self.temp_dir = self.temp_dir or defaults.temp_dir
        self.runtime_database_path = self.runtime_database_path or (
            data_dir / "inari.sqlite3"
        )
        self.security_state_dir = self.security_state_dir or (data_dir / "security")
        return self


def load_settings(
    config_path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> AgentSettings:
    env = dict(environ or os.environ)
    working_directory = Path(cwd) if cwd is not None else Path.cwd()
    resolved_config_path = resolve_config_path(
        config_path=config_path, environ=env, cwd=working_directory
    )
    config_payload: dict[str, Any] = {}
    base_dir = working_directory
    dotenv_path = working_directory / ".env"

    if resolved_config_path is not None:
        config_payload = _load_config_payload(resolved_config_path)
        base_dir = resolved_config_path.parent
        dotenv_path = resolved_config_path.parent / ".env"

    file_config = AgentConfigFile.model_validate(config_payload or {})
    env_payload = _build_env_override_payload(
        environment=env,
        dotenv_values=_read_dotenv(dotenv_path),
    )
    requested_path_profile = parse_path_profile(
        env_payload.get("path_profile") or file_config.storage.profile
    )
    resolved_path_defaults = resolve_default_path_bundle(
        profile=requested_path_profile,
        working_directory=working_directory,
        config_path=resolved_config_path,
    )
    env_payload["path_profile"] = resolved_path_defaults.profile
    settings_payload = file_config.to_settings_payload(
        base_dir=base_dir, path_defaults=resolved_path_defaults
    )
    merged_payload = {
        **settings_payload,
        **env_payload,
    }
    return AgentSettings.model_validate(merged_payload)


@lru_cache(maxsize=8)
def _get_settings_cached(config_key: str | None) -> AgentSettings:
    config_path = Path(config_key) if config_key is not None else None
    return load_settings(config_path=config_path)


def get_settings(config_path: Path | str | None = None) -> AgentSettings:
    key = None
    if config_path is not None:
        key = str(Path(config_path).resolve())
    return _get_settings_cached(key)


def clear_settings_cache() -> None:
    _get_settings_cached.cache_clear()


def resolve_config_path(
    config_path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> Path | None:
    env = dict(environ or os.environ)
    working_directory = Path(cwd) if cwd is not None else Path.cwd()
    requested_path = config_path or env.get(CONFIG_ENV_VAR)
    if requested_path is not None:
        candidate = _resolve_config_candidate(Path(requested_path), working_directory)
        if not candidate.exists():
            raise FileNotFoundError(f"Config file not found: {candidate}")
        return candidate

    requested_profile = env.get(f"{ENV_PREFIX}PATH_PROFILE", "auto")
    for candidate in default_config_candidates(
        working_directory=working_directory,
        profile=requested_profile,
    ):
        if candidate.exists():
            return candidate
    return None


def generate_taplo_schema() -> dict[str, Any]:
    schema = AgentConfigFile.model_json_schema(mode="serialization")
    converted = _convert_schema_for_taplo(schema)
    converted["$schema"] = "http://json-schema.org/draft-04/schema#"
    converted.setdefault("title", "Inari Config")
    converted.setdefault("description", "Schema for the Inari TOML configuration file.")
    return converted


def render_example_toml(
    *,
    schema_path: str | None = "./schemas/inari-config.schema.json",
    config: AgentConfigFile | None = None,
    active_fields: set[tuple[str, ...]] | None = None,
) -> str:
    document = (
        config.model_copy(deep=True) if config is not None else _build_example_config()
    )
    lines: list[str] = []
    if schema_path is not None:
        lines.extend([f"#:schema {schema_path}", ""])
    _append_comment_block(lines, _TEMPLATE_HEADER_COMMENTS)
    lines.append("")
    _render_model_template(
        lines, (), document, root=True, active_fields=active_fields or set()
    )
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def write_generated_config_artifacts(
    *,
    schema_output_path: Path,
    example_output_path: Path,
    schema_reference: str = "./schemas/inari-config.schema.json",
) -> tuple[Path, Path]:
    schema_output_path.parent.mkdir(parents=True, exist_ok=True)
    example_output_path.parent.mkdir(parents=True, exist_ok=True)
    schema_output_path.write_text(
        json.dumps(generate_taplo_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    example_output_path.write_text(
        render_example_toml(schema_path=schema_reference),
        encoding="utf-8",
    )
    return schema_output_path, example_output_path


def write_default_config_file(
    path: Path,
    *,
    profile: PathProfile = "production",
    overwrite: bool = False,
    schema_path: str | None = None,
) -> Path:
    if path.exists() and not overwrite:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    document = AgentConfigFile()
    document.storage.profile = profile
    path.write_text(
        render_example_toml(
            schema_path=schema_path,
            config=document,
            active_fields={("storage", "profile")},
        ),
        encoding="utf-8",
    )
    return path


def generate_schema_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the Inari TOML schema and example config."
    )
    parser.add_argument(
        "--schema-output",
        type=Path,
        default=Path("schemas") / "inari-config.schema.json",
        help="Where to write the generated JSON Schema.",
    )
    parser.add_argument(
        "--example-output",
        type=Path,
        default=Path(EXAMPLE_CONFIG_FILENAME),
        help="Where to write the generated example TOML file.",
    )
    parser.add_argument(
        "--schema-reference",
        default="./schemas/inari-config.schema.json",
        help="Schema reference inserted at the top of the generated example TOML.",
    )
    args = parser.parse_args(argv)
    write_generated_config_artifacts(
        schema_output_path=args.schema_output,
        example_output_path=args.example_output,
        schema_reference=args.schema_reference,
    )
    return 0


def _normalize_list_like(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
    return [item.strip() for item in stripped.split(",") if item.strip()]


_DURATION_MULTIPLIERS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def _parse_duration_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a duration, not a boolean.")
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a duration string or number.")
    stripped = value.strip().lower()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty.")
    for unit, multiplier in _DURATION_MULTIPLIERS.items():
        if stripped.endswith(unit):
            numeric_part = stripped[: -len(unit)].strip()
            if not numeric_part:
                break
            return float(numeric_part) * multiplier
    return float(stripped)


def _parse_duration_int_seconds(value: object, *, field_name: str) -> int:
    seconds = _parse_duration_seconds(value, field_name=field_name)
    if not seconds.is_integer():
        raise ValueError(f"{field_name} must resolve to a whole number of seconds.")
    return int(seconds)


def _resolve_relative_path(value: Path | str | None, base_dir: Path) -> Path | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = Path(value)
    if value.is_absolute():
        return value
    return (base_dir / value).resolve()


def _resolve_config_candidate(candidate: Path, working_directory: Path) -> Path:
    if candidate.is_absolute():
        return candidate.resolve()
    return (working_directory / candidate).resolve()


def _load_config_payload(path: Path) -> dict[str, Any]:
    base_payload = _read_toml(path)
    local_path = path.with_name(f"{path.stem}.local{path.suffix}")
    if local_path.exists():
        base_payload = _deep_merge_dicts(base_payload, _read_toml(local_path))
    return base_payload


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if (
            value
            and len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def _build_env_override_payload(
    *,
    environment: Mapping[str, str],
    dotenv_values: Mapping[str, str],
) -> dict[str, str]:
    combined = {
        **dotenv_values,
        **environment,
    }
    payload: dict[str, str] = {}
    for field_name in AgentSettings.model_fields:
        env_name = f"{ENV_PREFIX}{field_name.upper()}"
        if env_name in combined:
            payload[field_name] = combined[env_name]
    return payload


def _deep_merge_dicts(
    base: Mapping[str, Any], override: Mapping[str, Any]
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def _convert_schema_for_taplo(value: Any) -> Any:
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = "definitions" if key == "$defs" else key
            if normalized_key in {
                "$id",
                "$anchor",
                "$dynamicAnchor",
                "$dynamicRef",
                "unevaluatedProperties",
            }:
                continue
            if normalized_key == "const":
                converted["enum"] = [_convert_schema_for_taplo(item)]
                continue
            converted_item = _convert_schema_for_taplo(item)
            if normalized_key == "default":
                converted_item = _normalize_schema_default(converted_item)
            converted[normalized_key] = converted_item
        if "$ref" in converted and isinstance(converted["$ref"], str):
            converted["$ref"] = converted["$ref"].replace("#/$defs/", "#/definitions/")
        return converted
    if isinstance(value, list):
        return [_convert_schema_for_taplo(item) for item in value]
    return value


def _serialize_for_toml(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _serialize_for_toml(value.model_dump(mode="python", exclude_none=True))
    if isinstance(value, dict):
        return {
            str(key): _serialize_for_toml(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_serialize_for_toml(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix() if value.drive == "" else str(value)
    if isinstance(value, timedelta):
        return _format_duration_literal(value)
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


def _format_duration_literal(value: timedelta) -> str:
    total_seconds = value.total_seconds()
    if total_seconds == 0:
        return "0s"
    if total_seconds < 1 and (total_seconds * 1000).is_integer():
        return f"{int(total_seconds * 1000)}ms"
    for divisor, suffix in (
        (86400, "d"),
        (3600, "h"),
        (60, "m"),
        (1, "s"),
    ):
        scaled = total_seconds / divisor
        if scaled.is_integer():
            return f"{int(scaled)}{suffix}"
    return f"{total_seconds:g}s"


_ISO_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def _normalize_schema_default(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = _ISO_DURATION_PATTERN.fullmatch(value)
    if match is None:
        return value
    total_seconds = 0.0
    if match.group("days") is not None:
        total_seconds += int(match.group("days")) * 86400
    if match.group("hours") is not None:
        total_seconds += int(match.group("hours")) * 3600
    if match.group("minutes") is not None:
        total_seconds += int(match.group("minutes")) * 60
    if match.group("seconds") is not None:
        total_seconds += float(match.group("seconds"))
    return _format_duration_literal(timedelta(seconds=total_seconds))


def _render_model_template(
    lines: list[str],
    path: tuple[str, ...],
    model: BaseModel,
    *,
    root: bool = False,
    active_fields: set[tuple[str, ...]],
) -> None:
    scalar_fields: list[tuple[str, Any]] = []
    nested_models: list[tuple[str, BaseModel]] = []
    table_lists: list[tuple[str, list[Any], type[BaseModel] | None]] = []

    for field_name, field_info in model.__class__.model_fields.items():
        value = getattr(model, field_name)
        if isinstance(value, BaseModel):
            nested_models.append((field_name, value))
            continue
        item_model_type = _list_item_model_type(field_info.annotation)
        if isinstance(value, list) and item_model_type is not None:
            table_lists.append((field_name, value, item_model_type))
            continue
        scalar_fields.append((field_name, value))

    should_render_header = not root and bool(
        scalar_fields or (not nested_models and not table_lists)
    )

    if should_render_header:
        if path in _SECTION_COMMENTS:
            _append_comment_block(lines, _SECTION_COMMENTS[path])
        lines.append(f"[{'.'.join(path)}]")

    for field_name, raw_value in scalar_fields:
        field_path = (*path, field_name)
        _render_commented_field(
            lines, field_path, raw_value, active_fields=active_fields
        )

    if scalar_fields or should_render_header:
        lines.append("")

    for field_name, child_model in nested_models:
        _render_model_template(
            lines, (*path, field_name), child_model, active_fields=active_fields
        )

    for field_name, field_items, item_model_type in table_lists:
        _render_array_of_tables_template(
            lines,
            (*path, field_name),
            field_items,
            item_model_type,
        )


def _render_commented_field(
    lines: list[str],
    field_path: tuple[str, ...],
    raw_value: Any,
    *,
    active_fields: set[tuple[str, ...]],
) -> None:
    if field_path in _FIELD_COMMENTS:
        _append_comment_block(lines, _FIELD_COMMENTS[field_path])
    example_value = _field_example_value(field_path, raw_value)
    active_value = _serialize_for_toml(raw_value)
    if field_path == ("config_version",):
        lines.append(f"{field_path[-1]} = {_toml_literal(example_value)}")
        return
    if field_path in active_fields:
        lines.append(f"{field_path[-1]} = {_toml_literal(active_value)}")
        return
    lines.append(f"# {field_path[-1]} = {_toml_literal(example_value)}")


def _render_array_of_tables_template(
    lines: list[str],
    path: tuple[str, ...],
    items: list[Any],
    item_model_type: type[BaseModel] | None,
) -> None:
    if path in _ARRAY_TABLE_COMMENTS:
        _append_comment_block(lines, _ARRAY_TABLE_COMMENTS[path])

    example_items: list[dict[str, Any]] = []
    if items:
        for item in items:
            if isinstance(item, BaseModel):
                example_items.append(
                    _serialize_for_toml(
                        item.model_dump(mode="python", exclude_none=False)
                    )
                )
            elif isinstance(item, dict):
                example_items.append(_serialize_for_toml(item))
    elif path in _ARRAY_TABLE_EXAMPLES:
        example_items.extend(
            _serialize_for_toml(item) for item in _ARRAY_TABLE_EXAMPLES[path]
        )

    if not example_items and item_model_type is not None:
        lines.append(f"# {path[-1]} = []")
        lines.append("")
        return

    for item in example_items:
        lines.append(f"# [[{'.'.join(path)}]]")
        for key, value in item.items():
            lines.append(f"# {key} = {_toml_literal(value)}")
        lines.append("")


def _append_comment_block(lines: list[str], comments: Sequence[str]) -> None:
    for comment in comments:
        lines.append(f"# {comment}")


def _field_example_value(field_path: tuple[str, ...], raw_value: Any) -> Any:
    if field_path in _FIELD_EXAMPLES:
        return _FIELD_EXAMPLES[field_path]
    return _serialize_for_toml(raw_value)


def _list_item_model_type(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin not in {list, Sequence}:
        return None
    args = get_args(annotation)
    if not args:
        return None
    item_type = args[0]
    if isinstance(item_type, type) and issubclass(item_type, BaseModel):
        return item_type
    return None


def _build_example_config() -> AgentConfigFile:
    document = AgentConfigFile()
    document.storage.profile = "auto"
    document.api.allowed_hosts = [
        host for host in document.api.allowed_hosts if host != "testserver"
    ]
    return document


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    if value is None:
        return '""'
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
