from __future__ import annotations

import argparse
import json
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import get_args, get_origin
from typing import Any, Literal, Mapping, Sequence

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .config_paths import (
    PathProfile,
    PlatformPathBundle,
    default_config_candidates,
    resolve_default_path_bundle,
    resolve_effective_path_profile,
)
from .gateway.models import (
    MutualTlsMode,
    UpstreamAuthMode,
    UpstreamCertificateMode,
    UpstreamEdgeProvider,
)
from .security.models import GatewayExposure, GatewayMode

ENV_PREFIX = "IOT_AGENT_"
CONFIG_ENV_VAR = f"{ENV_PREFIX}CONFIG"
EXAMPLE_CONFIG_FILENAME = "config.example.toml"

_TEMPLATE_HEADER_COMMENTS = [
    "Generated configuration template for iot-agent.",
    "Uncomment only the settings you want to override.",
    "Commented values show the built-in default or a recommended example.",
    "Environment variables with the IOT_AGENT_ prefix still override file values.",
]

_SECTION_COMMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("server",): ("HTTP API binding and host filtering.",),
    ("cors",): (
        "Browser access policy for local UI clients such as Odoo or the tray.",
    ),
    ("logging",): ("Logging verbosity and log file location.",),
    ("paths",): (
        "Filesystem locations for runtime state. Leave these commented to use OS-specific defaults.",
    ),
    ("printing",): (
        "Default printer behavior and optional network printer definitions.",
    ),
    ("runtime", "discovery"): ("Device discovery cadence.",),
    ("runtime", "scheduler"): ("Job scheduler tuning.",),
    ("runtime", "jobs"): ("Job execution leases, retries, and timeouts.",),
    ("security",): ("Local API exposure and local-auth defaults.",),
    ("security", "local_tokens"): (
        "Short-lived tokens issued to local clients like the tray.",
    ),
    ("security", "tls"): (
        "Inbound TLS files for LAN exposure. Loopback-only deployments usually leave these unset.",
    ),
    ("gateway",): (
        "Managed-mode controller connectivity. Leave commented for standalone/local-only deployments.",
    ),
    ("gateway", "bootstrap"): (
        "Bootstrap material used only for first enrollment.",
        "Use a single short-lived controller-issued enrollment token for the initial managed enrollment call.",
    ),
    ("gateway", "sync"): ("Managed-mode reconnect, polling, and backoff tuning.",),
    ("gateway", "zitadel"): (
        "ZITADEL service-account settings for controller authentication.",
    ),
    ("gateway", "step_ca"): (
        "step-ca certificate bootstrap and renewal settings.",
        "In the normal managed flow, the controller returns these values during enrollment.",
        "Leave them commented unless you want explicit local overrides or fallback knowledge of the CA.",
    ),
}

_FIELD_COMMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("config_version",): ("Schema version for this TOML format.",),
    ("server", "host"): ("Bind host for the local HTTP API.",),
    ("server", "port"): ("Bind port for the local HTTP API.",),
    ("server", "trusted_hosts"): ("Allowed Host headers for incoming requests.",),
    ("cors", "allowed_origins"): ("Browser origins allowed to call the local API.",),
    ("logging", "level"): ("Application log verbosity.",),
    ("logging", "directory"): ("Directory for rotated agent logs.",),
    ("paths", "profile"): (
        "Choose development or production path defaults. `auto` detects based on the working directory.",
    ),
    ("paths", "data_dir"): ("Base directory for runtime state files.",),
    ("paths", "temp_dir"): ("Directory for temporary files.",),
    ("paths", "runtime_database"): ("SQLite database file path.",),
    ("paths", "security_state_dir"): (
        "Directory for identities, enrollment state, and fallback local secrets.",
    ),
    ("printing", "default_printer_name"): (
        "Preferred printer when jobs do not target a specific device.",
    ),
    ("printing", "default_transport"): (
        "Default transport strategy when a driver supports more than one mode.",
    ),
    ("printing", "html_enabled"): ("Allow HTML receipt/document rendering.",),
    ("runtime", "discovery", "poll_interval_seconds"): (
        "How often the agent refreshes device discovery.",
    ),
    ("runtime", "scheduler", "poll_interval_seconds"): (
        "How often the scheduler looks for runnable jobs.",
    ),
    ("runtime", "scheduler", "batch_size"): (
        "Maximum number of jobs leased per scheduling pass.",
    ),
    ("runtime", "jobs", "max_attempts"): (
        "Maximum attempts before a job is marked failed.",
    ),
    ("runtime", "jobs", "retry_base_delay_seconds"): (
        "Initial retry delay after a failed job attempt.",
    ),
    ("runtime", "jobs", "retry_max_delay_seconds"): (
        "Upper bound for exponential retry delays.",
    ),
    ("runtime", "jobs", "dispatch_lease_seconds"): (
        "How long a dispatch lease is held before another worker may reclaim it.",
    ),
    ("runtime", "jobs", "execution_lease_seconds"): (
        "How long a worker execution lease remains valid without a heartbeat.",
    ),
    ("runtime", "jobs", "heartbeat_interval_seconds"): (
        "How often active workers refresh their job lease.",
    ),
    ("runtime", "jobs", "execution_timeout_seconds"): (
        "Hard timeout for a single job execution.",
    ),
    ("runtime", "jobs", "lease_recovery_interval_seconds"): (
        "How often the agent scans for expired leases.",
    ),
    ("security", "gateway_mode"): (
        "Run purely local (`standalone`) or connect to an upstream controller (`managed`).",
    ),
    ("security", "gateway_exposure"): (
        "Expose the API only on loopback or to the LAN.",
    ),
    ("security", "allow_loopback_bootstrap"): (
        "Allow local bootstrap token minting for trusted loopback clients.",
    ),
    ("security", "https_redirect_enabled"): (
        "Redirect HTTP to HTTPS when LAN TLS is enabled.",
    ),
    ("security", "secret_store_service_name"): (
        "OS credential store namespace used by the agent.",
    ),
    ("security", "local_tokens", "ttl_seconds"): (
        "Lifetime of locally issued bearer tokens in seconds.",
    ),
    ("security", "local_tokens", "audience"): (
        "Audience claim for locally issued tokens.",
    ),
    ("security", "local_tokens", "issuer"): (
        "Issuer claim for locally issued tokens.",
    ),
    ("security", "tls", "cert_path"): (
        "PEM certificate file presented by the local API.",
    ),
    ("security", "tls", "key_path"): ("Private key for the local API certificate.",),
    ("security", "tls", "ca_path"): (
        "Optional custom CA bundle trusted by the agent.",
    ),
    ("gateway", "base_url"): ("Base URL for the external controller.",),
    ("gateway", "enrollment_url"): (
        "Explicit enrollment endpoint. Leave commented to derive from `base_url` if your controller supports it.",
    ),
    ("gateway", "status_url"): (
        "Explicit status endpoint if the controller expects a fixed URL.",
    ),
    ("gateway", "events_url"): ("WebSocket control/event endpoint.",),
    ("gateway", "auth_mode"): ("How the agent authenticates to the controller.",),
    ("gateway", "certificate_mode"): (
        "How the agent obtains and refreshes client certificates.",
    ),
    ("gateway", "edge_provider"): (
        "Controller edge layout. `caddy` enables stricter profile validation.",
    ),
    ("gateway", "mutual_tls_mode"): (
        "Whether upstream client-certificate authentication is disabled, optional, or required.",
        "Recommended production posture: keep this `optional` for bootstrap, then let the connection harden to required once a managed client certificate has been issued.",
    ),
    ("gateway", "trust_client_ca"): (
        "Trust the managed CA bundle for outbound TLS validation.",
    ),
    ("gateway", "bootstrap", "enrollment_token"): (
        "Short-lived controller-issued bootstrap credential used as a Bearer token during enrollment.",
    ),
    ("gateway", "sync", "interval_seconds"): (
        "How often snapshots are pushed to the controller.",
    ),
    ("gateway", "sync", "reconnect_delay_seconds"): (
        "Base reconnect delay after controller disconnects.",
    ),
    ("gateway", "sync", "event_timeout_seconds"): (
        "Read timeout for upstream event streams.",
    ),
    ("gateway", "sync", "control_poll_interval_seconds"): (
        "Polling cadence for control-plane maintenance work.",
    ),
    ("gateway", "sync", "outbox_batch_size"): (
        "Maximum queued outbound messages sent in one batch.",
    ),
    ("gateway", "sync", "backoff_base_seconds"): (
        "Starting backoff value for repeated upstream failures.",
    ),
    ("gateway", "sync", "backoff_max_seconds"): (
        "Maximum backoff value for repeated upstream failures.",
    ),
    ("gateway", "sync", "token_refresh_skew_seconds"): (
        "How early to refresh upstream tokens before expiry.",
    ),
    ("gateway", "zitadel", "base_url"): ("Base URL for your ZITADEL instance.",),
    ("gateway", "zitadel", "token_url"): (
        "Explicit token endpoint if it differs from the default instance URL.",
    ),
    ("gateway", "zitadel", "audience"): ("Audience requested for controller tokens.",),
    ("gateway", "zitadel", "service_account_key_path"): (
        "Path to the ZITADEL service-account JSON file.",
    ),
    ("gateway", "zitadel", "service_user_id"): (
        "Service user id when configuring the assertion components manually.",
    ),
    ("gateway", "zitadel", "key_id"): (
        "Key identifier for the service-account private key.",
    ),
    ("gateway", "zitadel", "private_key_path"): (
        "Path to the service-account private key PEM.",
    ),
    ("gateway", "zitadel", "assertion_algorithm"): (
        "Signing algorithm used for the private-key JWT assertion.",
    ),
    ("gateway", "zitadel", "requested_scopes"): ("Scopes requested from ZITADEL.",),
    ("gateway", "zitadel", "token_refresh_skew_seconds"): (
        "How early to refresh ZITADEL access tokens.",
    ),
    ("gateway", "step_ca", "url"): (
        "Base URL for the private step-ca instance when you want to override the controller-provided CA URL.",
    ),
    ("gateway", "step_ca", "sign_url"): (
        "Explicit sign endpoint override when it cannot be derived from `url` or the controller enrollment response.",
    ),
    ("gateway", "step_ca", "renew_url"): (
        "Explicit renew endpoint override when it cannot be derived from `url` or the controller enrollment response.",
    ),
    ("gateway", "step_ca", "root_fingerprint"): (
        "Expected fingerprint of the step-ca root certificate. The controller normally provides this during enrollment.",
    ),
    ("gateway", "step_ca", "requested_sans"): (
        "Additional SANs requested for the managed client certificate beyond the agent's default identity.",
    ),
    ("gateway", "step_ca", "certificate_renewal_skew_seconds"): (
        "How early to renew managed certificates before expiry after the initial controller-mediated bootstrap.",
    ),
    ("gateway", "step_ca", "lifecycle_poll_interval_seconds"): (
        "How often the dedicated managed-certificate lifecycle loop inspects, renews, and repairs certificate state.",
    ),
}

_FIELD_EXAMPLES: dict[tuple[str, ...], Any] = {
    ("logging", "directory"): "./logs",
    ("paths", "data_dir"): "./data",
    ("paths", "temp_dir"): "./tmp",
    ("paths", "runtime_database"): "./data/iot-agent.sqlite3",
    ("paths", "security_state_dir"): "./data/security",
    ("printing", "default_printer_name"): "Kitchen Printer",
    ("security", "local_tokens", "issuer"): "iot-agent.local",
    ("security", "tls", "cert_path"): "./certs/agent.crt",
    ("security", "tls", "key_path"): "./certs/agent.key",
    ("security", "tls", "ca_path"): "./certs/ca.crt",
    ("gateway", "base_url"): "https://controller.example.com",
    (
        "gateway",
        "enrollment_url",
    ): "https://bootstrap.controller.example.com/api/iot-agent/enroll",
    (
        "gateway",
        "status_url",
    ): "https://controller.example.com/api/iot-agent/agents/agt_123/status",
    (
        "gateway",
        "events_url",
    ): "wss://controller.example.com/api/iot-agent/agents/agt_123/events",
    ("gateway", "bootstrap", "enrollment_token"): "enrollment-token",
    ("gateway", "zitadel", "base_url"): "https://zitadel.example.com",
    ("gateway", "zitadel", "token_url"): "https://zitadel.example.com/oauth/v2/token",
    ("gateway", "zitadel", "audience"): "https://controller.example.com",
    (
        "gateway",
        "zitadel",
        "service_account_key_path",
    ): "./secrets/zitadel-service-account.json",
    ("gateway", "zitadel", "service_user_id"): "123456789012345678",
    ("gateway", "zitadel", "key_id"): "key-id",
    ("gateway", "zitadel", "private_key_path"): "./secrets/zitadel-private-key.pem",
    ("gateway", "step_ca", "url"): "https://ca.example.com",
    ("gateway", "step_ca", "sign_url"): "https://ca.example.com/1.0/sign",
    ("gateway", "step_ca", "renew_url"): "https://ca.example.com/1.0/renew",
    (
        "gateway",
        "step_ca",
        "root_fingerprint",
    ): "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
}

_ARRAY_TABLE_EXAMPLES: dict[tuple[str, ...], tuple[dict[str, Any], ...]] = {
    ("printing", "network_printers"): (
        {
            "name": "Kitchen LAN Printer",
            "host": "192.168.1.40",
            "port": 9100,
            "is_default": False,
            "preferred_transport": "raw",
            "cash_drawer": True,
            "text_enabled": False,
            "document_enabled": False,
            "encoding": "utf-8",
        },
    ),
}

_ARRAY_TABLE_COMMENTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("printing", "network_printers"): (
        "Repeat this block for each raw TCP or receipt printer that should be managed directly by the agent.",
    ),
}

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]
PrinterMode = Literal["auto", "raw", "text", "document"]

_NESTED_MODEL_CONFIG = ConfigDict(extra="forbid", str_strip_whitespace=True)
_SETTINGS_MODEL_CONFIG = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ServerConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    host: str = "127.0.0.1"
    port: int = 7310
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "testserver"]
    )


class CorsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:8069",
            "http://localhost:8069",
        ]
    )


class LoggingConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    level: LogLevel = "INFO"
    directory: Path | None = None


class PathsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    profile: PathProfile = "auto"
    data_dir: Path | None = None
    temp_dir: Path | None = None
    runtime_database: Path | None = None
    security_state_dir: Path | None = None


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


class PrintingConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    default_printer_name: str | None = None
    default_transport: PrinterMode = "auto"
    html_enabled: bool = True
    network_printers: list[NetworkPrinterConfig] = Field(default_factory=list)


class RuntimeDiscoveryConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    poll_interval_seconds: float = 3.0


class RuntimeSchedulerConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    poll_interval_seconds: float = 0.5
    batch_size: int = 32


class RuntimeJobsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    max_attempts: int = 3
    retry_base_delay_seconds: int = 2
    retry_max_delay_seconds: int = 30
    dispatch_lease_seconds: int = 15
    execution_lease_seconds: int = 30
    heartbeat_interval_seconds: float = 5.0
    execution_timeout_seconds: float = 60.0
    lease_recovery_interval_seconds: float = 5.0


class RuntimeConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    discovery: RuntimeDiscoveryConfig = Field(default_factory=RuntimeDiscoveryConfig)
    scheduler: RuntimeSchedulerConfig = Field(default_factory=RuntimeSchedulerConfig)
    jobs: RuntimeJobsConfig = Field(default_factory=RuntimeJobsConfig)


class SecurityLocalTokenConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    ttl_seconds: int = 3600
    audience: str = "iot-agent.local"
    issuer: str | None = None


class SecurityTlsConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    cert_path: Path | None = None
    key_path: Path | None = None
    ca_path: Path | None = None


class SecurityConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    gateway_mode: GatewayMode = GatewayMode.STANDALONE
    gateway_exposure: GatewayExposure = GatewayExposure.LOOPBACK
    allow_loopback_bootstrap: bool = True
    https_redirect_enabled: bool = True
    secret_store_service_name: str = "iot-agent"
    local_tokens: SecurityLocalTokenConfig = Field(
        default_factory=SecurityLocalTokenConfig
    )
    tls: SecurityTlsConfig = Field(default_factory=SecurityTlsConfig)


class GatewaySyncConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    interval_seconds: float = 30.0
    reconnect_delay_seconds: float = 5.0
    event_timeout_seconds: float = 30.0
    control_poll_interval_seconds: float = 0.5
    outbox_batch_size: int = 128
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    token_refresh_skew_seconds: int = 300


class GatewayBootstrapConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    enrollment_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "enrollment_token", "bootstrap_token", "enrollment_code"
        ),
    )


class GatewayZitadelConfig(BaseModel):
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
    token_refresh_skew_seconds: int = 120


class GatewayStepCaConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    url: str | None = None
    sign_url: str | None = None
    renew_url: str | None = None
    root_fingerprint: str | None = None
    requested_sans: list[str] = Field(default_factory=list)
    certificate_renewal_skew_seconds: int = 3600
    lifecycle_poll_interval_seconds: float = 60.0


class GatewayConfig(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    base_url: str | None = None
    enrollment_url: str | None = None
    status_url: str | None = None
    events_url: str | None = None
    auth_mode: UpstreamAuthMode = UpstreamAuthMode.CONTROLLER
    certificate_mode: UpstreamCertificateMode = UpstreamCertificateMode.CONTROLLER
    edge_provider: UpstreamEdgeProvider = UpstreamEdgeProvider.DIRECT
    mutual_tls_mode: MutualTlsMode = MutualTlsMode.OPTIONAL
    trust_client_ca: bool = True
    bootstrap: GatewayBootstrapConfig = Field(default_factory=GatewayBootstrapConfig)
    sync: GatewaySyncConfig = Field(default_factory=GatewaySyncConfig)
    zitadel: GatewayZitadelConfig = Field(default_factory=GatewayZitadelConfig)
    step_ca: GatewayStepCaConfig = Field(default_factory=GatewayStepCaConfig)


class AgentConfigFile(BaseModel):
    model_config = _NESTED_MODEL_CONFIG

    config_version: int = 1
    server: ServerConfig = Field(default_factory=ServerConfig)
    cors: CorsConfig = Field(default_factory=CorsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    printing: PrintingConfig = Field(default_factory=PrintingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    def to_settings_payload(
        self, *, base_dir: Path, path_defaults: PlatformPathBundle
    ) -> dict[str, object]:
        data_dir = (
            _resolve_relative_path(self.paths.data_dir, base_dir)
            or path_defaults.data_dir
        )
        runtime_database_path = _resolve_relative_path(
            self.paths.runtime_database, base_dir
        ) or (data_dir / "iot-agent.sqlite3")
        security_state_dir = _resolve_relative_path(
            self.paths.security_state_dir, base_dir
        ) or (data_dir / "security")
        return {
            "host": self.server.host,
            "port": self.server.port,
            "path_profile": path_defaults.profile,
            "trusted_hosts": list(self.server.trusted_hosts),
            "allowed_origins": list(self.cors.allowed_origins),
            "log_level": self.logging.level,
            "data_dir": data_dir,
            "log_dir": _resolve_relative_path(self.logging.directory, base_dir)
            or path_defaults.log_dir,
            "temp_dir": _resolve_relative_path(self.paths.temp_dir, base_dir)
            or path_defaults.temp_dir,
            "runtime_database_path": runtime_database_path,
            "security_state_dir": security_state_dir,
            "default_printer_name": self.printing.default_printer_name,
            "default_printer_mode": self.printing.default_transport,
            "html_print_enabled": self.printing.html_enabled,
            "network_printers": [
                printer.model_dump(mode="python")
                for printer in self.printing.network_printers
            ],
            "discovery_poll_interval_seconds": self.runtime.discovery.poll_interval_seconds,
            "scheduler_poll_interval_seconds": self.runtime.scheduler.poll_interval_seconds,
            "scheduler_batch_size": self.runtime.scheduler.batch_size,
            "job_max_attempts": self.runtime.jobs.max_attempts,
            "job_retry_base_delay_seconds": self.runtime.jobs.retry_base_delay_seconds,
            "job_retry_max_delay_seconds": self.runtime.jobs.retry_max_delay_seconds,
            "job_dispatch_lease_seconds": self.runtime.jobs.dispatch_lease_seconds,
            "job_execution_lease_seconds": self.runtime.jobs.execution_lease_seconds,
            "job_heartbeat_interval_seconds": self.runtime.jobs.heartbeat_interval_seconds,
            "job_execution_timeout_seconds": self.runtime.jobs.execution_timeout_seconds,
            "job_lease_recovery_interval_seconds": self.runtime.jobs.lease_recovery_interval_seconds,
            "gateway_mode": self.security.gateway_mode,
            "gateway_exposure": self.security.gateway_exposure,
            "allow_loopback_bootstrap": self.security.allow_loopback_bootstrap,
            "https_redirect_enabled": self.security.https_redirect_enabled,
            "secret_store_service_name": self.security.secret_store_service_name,
            "local_token_ttl_seconds": self.security.local_tokens.ttl_seconds,
            "token_audience": self.security.local_tokens.audience,
            "token_issuer": self.security.local_tokens.issuer,
            "tls_cert_path": _resolve_relative_path(
                self.security.tls.cert_path, base_dir
            ),
            "tls_key_path": _resolve_relative_path(
                self.security.tls.key_path, base_dir
            ),
            "tls_ca_path": _resolve_relative_path(self.security.tls.ca_path, base_dir),
            "upstream_base_url": self.gateway.base_url,
            "upstream_enrollment_url": self.gateway.enrollment_url,
            "upstream_status_url": self.gateway.status_url,
            "upstream_events_url": self.gateway.events_url,
            "upstream_auth_mode": self.gateway.auth_mode,
            "upstream_certificate_mode": self.gateway.certificate_mode,
            "upstream_edge_provider": self.gateway.edge_provider,
            "upstream_mutual_tls_mode": self.gateway.mutual_tls_mode,
            "upstream_trust_client_ca": self.gateway.trust_client_ca,
            "upstream_enrollment_token": self.gateway.bootstrap.enrollment_token,
            "gateway_sync_interval_seconds": self.gateway.sync.interval_seconds,
            "gateway_reconnect_delay_seconds": self.gateway.sync.reconnect_delay_seconds,
            "gateway_event_timeout_seconds": self.gateway.sync.event_timeout_seconds,
            "gateway_control_poll_interval_seconds": self.gateway.sync.control_poll_interval_seconds,
            "gateway_outbox_batch_size": self.gateway.sync.outbox_batch_size,
            "gateway_backoff_base_seconds": self.gateway.sync.backoff_base_seconds,
            "gateway_backoff_max_seconds": self.gateway.sync.backoff_max_seconds,
            "gateway_token_refresh_skew_seconds": self.gateway.sync.token_refresh_skew_seconds,
            "zitadel_base_url": self.gateway.zitadel.base_url,
            "zitadel_token_url": self.gateway.zitadel.token_url,
            "zitadel_audience": self.gateway.zitadel.audience,
            "zitadel_service_account_key_path": _resolve_relative_path(
                self.gateway.zitadel.service_account_key_path,
                base_dir,
            ),
            "zitadel_service_user_id": self.gateway.zitadel.service_user_id,
            "zitadel_key_id": self.gateway.zitadel.key_id,
            "zitadel_private_key_path": _resolve_relative_path(
                self.gateway.zitadel.private_key_path,
                base_dir,
            ),
            "zitadel_assertion_algorithm": self.gateway.zitadel.assertion_algorithm,
            "zitadel_requested_scopes": list(self.gateway.zitadel.requested_scopes),
            "zitadel_token_refresh_skew_seconds": self.gateway.zitadel.token_refresh_skew_seconds,
            "step_ca_url": self.gateway.step_ca.url,
            "step_ca_sign_url": self.gateway.step_ca.sign_url,
            "step_ca_renew_url": self.gateway.step_ca.renew_url,
            "step_ca_root_fingerprint": self.gateway.step_ca.root_fingerprint,
            "step_ca_requested_sans": list(self.gateway.step_ca.requested_sans),
            "step_ca_certificate_renewal_skew_seconds": self.gateway.step_ca.certificate_renewal_skew_seconds,
            "step_ca_lifecycle_poll_interval_seconds": self.gateway.step_ca.lifecycle_poll_interval_seconds,
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
    upstream_enrollment_token: str | None = None
    upstream_trust_client_ca: bool = True
    zitadel_base_url: str | None = None
    zitadel_token_url: str | None = None
    zitadel_audience: str | None = None
    zitadel_service_account_key_path: Path | None = None
    zitadel_service_user_id: str | None = None
    zitadel_key_id: str | None = None
    zitadel_private_key_path: Path | None = None
    zitadel_assertion_algorithm: str = "RS256"
    zitadel_requested_scopes: list[str] = Field(default_factory=lambda: ["openid"])
    zitadel_token_refresh_skew_seconds: int = 120
    step_ca_url: str | None = None
    step_ca_sign_url: str | None = None
    step_ca_renew_url: str | None = None
    step_ca_root_fingerprint: str | None = None
    step_ca_requested_sans: list[str] = Field(default_factory=list)
    step_ca_certificate_renewal_skew_seconds: int = 3600
    step_ca_lifecycle_poll_interval_seconds: float = 60.0
    gateway_sync_interval_seconds: float = 30.0
    gateway_reconnect_delay_seconds: float = 5.0
    gateway_event_timeout_seconds: float = 30.0
    gateway_control_poll_interval_seconds: float = 0.5
    gateway_outbox_batch_size: int = 128
    gateway_backoff_base_seconds: float = 1.0
    gateway_backoff_max_seconds: float = 60.0
    gateway_token_refresh_skew_seconds: int = 300

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
        "upstream_status_url",
        "upstream_events_url",
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
        "zitadel_requested_scopes", "step_ca_requested_sans", mode="before"
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
        self.data_dir = self.data_dir or defaults.data_dir
        self.log_dir = self.log_dir or defaults.log_dir
        self.temp_dir = self.temp_dir or defaults.temp_dir
        self.runtime_database_path = self.runtime_database_path or (
            self.data_dir / "iot-agent.sqlite3"
        )
        self.security_state_dir = self.security_state_dir or (
            self.data_dir / "security"
        )
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
    requested_path_profile = (
        env_payload.get("path_profile") or file_config.paths.profile
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
    schema = AgentConfigFile.model_json_schema()
    converted = _convert_schema_for_taplo(schema)
    converted["$schema"] = "http://json-schema.org/draft-04/schema#"
    converted.setdefault("title", "IoT Agent Config")
    converted.setdefault(
        "description", "Schema for the IoT Agent TOML configuration file."
    )
    return converted


def render_example_toml(
    *,
    schema_path: str | None = "./schemas/iot-agent-config.schema.json",
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
    schema_reference: str = "./schemas/iot-agent-config.schema.json",
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
    document.paths.profile = profile
    path.write_text(
        render_example_toml(
            schema_path=schema_path,
            config=document,
            active_fields={("paths", "profile")},
        ),
        encoding="utf-8",
    )
    return path


def generate_schema_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the IoT Agent TOML schema and example config."
    )
    parser.add_argument(
        "--schema-output",
        type=Path,
        default=Path("schemas") / "iot-agent-config.schema.json",
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
        default="./schemas/iot-agent-config.schema.json",
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
    if "upstream_enrollment_token" not in payload:
        for legacy_name in (
            f"{ENV_PREFIX}UPSTREAM_BOOTSTRAP_TOKEN",
            f"{ENV_PREFIX}UPSTREAM_ENROLLMENT_CODE",
        ):
            if legacy_name in combined:
                payload["upstream_enrollment_token"] = combined[legacy_name]
                break
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
            converted[normalized_key] = _convert_schema_for_taplo(item)
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
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value


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
    if field_path == ("config_version",):
        lines.append(f"{field_path[-1]} = {_toml_literal(example_value)}")
        return
    if field_path in active_fields:
        lines.append(f"{field_path[-1]} = {_toml_literal(example_value)}")
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
    document.paths.profile = "auto"
    document.server.trusted_hosts = [
        host for host in document.server.trusted_hosts if host != "testserver"
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
