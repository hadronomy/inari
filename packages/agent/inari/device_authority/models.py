from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


class ScopeKind(StrEnum):
    SITE = "site"
    POS_CONFIGURATION = "pos_configuration"


class OutputEvidence(StrEnum):
    TRANSPORT = "transport"
    SPOOLER = "spooler"
    DEVICE = "device"


class DeviceTestResult(StrEnum):
    PASSED = "passed"
    FAILED_ENVIRONMENT = "failed_environment"
    FAILED_CONTRACT = "failed_contract"


class SignerPurpose(StrEnum):
    AUTHORITY_REVISION = "authority_revision"
    DRIVER_PROFILE = "driver_profile"
    CERTIFICATION_MATRIX = "certification_matrix"
    BINDING_REVISION = "binding_revision"
    DEVICE_TEST_EVIDENCE = "device_test_evidence"
    DEVICE_OBSERVATION = "device_observation"


class SignerState(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class RevocationSubjectKind(StrEnum):
    AUTHORITY_REVISION = "authority_revision"
    DRIVER_PROFILE = "driver_profile"
    CERTIFICATION_MATRIX_ROW = "certification_matrix_row"
    BINDING_REVISION = "binding_revision"
    DEVICE_TEST_EVIDENCE = "device_test_evidence"


class AuthorityStatus(StrEnum):
    READY = "ready"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class AuthorityRevision:
    revision_id: str
    revision_number: int
    manifest_digest: str
    effective_at: datetime
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _require_text("revision_id", self.revision_id)
        if isinstance(self.revision_number, bool) or self.revision_number < 1:
            raise ValueError("revision_number must be a positive integer")
        _require_digest("manifest_digest", self.manifest_digest)
        _require_utc("effective_at", self.effective_at)
        if self.expires_at is not None:
            _require_utc("expires_at", self.expires_at)
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must follow effective_at")


@dataclass(frozen=True, slots=True)
class SignedAuthorityRevision:
    revision: AuthorityRevision
    digest: str
    signer_key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        _require_digest("digest", self.digest)
        _require_text("signer_key_id", self.signer_key_id)
        if len(self.signature) != 64:
            raise ValueError("an Ed25519 signature must contain 64 bytes")


@dataclass(frozen=True, slots=True)
class AuthorityState:
    status: AuthorityStatus
    current_revision: SignedAuthorityRevision

    def __post_init__(self) -> None:
        if not isinstance(self.status, AuthorityStatus):
            raise ValueError("status must be an AuthorityStatus")


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    """The exact tenant and Odoo scope in which a Binding Revision applies."""

    database: str
    organization_id: str
    site_id: str
    kind: ScopeKind
    pos_configuration_id: str | None

    def __post_init__(self) -> None:
        _require_text("database", self.database)
        _require_text("organization_id", self.organization_id)
        _require_text("site_id", self.site_id)
        if not isinstance(self.kind, ScopeKind):
            raise ValueError("kind must be a ScopeKind")
        if self.kind is ScopeKind.POS_CONFIGURATION:
            _require_text("pos_configuration_id", self.pos_configuration_id)
        elif self.pos_configuration_id is not None:
            raise ValueError("site scope cannot contain a POS configuration")


@dataclass(frozen=True, slots=True)
class DeviceCapability:
    """One Driver declaration for one class of Device Work."""

    capability_id: str
    operation: str
    media_type: str
    contract_major: int
    output_evidence: OutputEvidence
    max_payload_bytes: int
    max_copies: int
    options_digest: str

    def __post_init__(self) -> None:
        _require_text("capability_id", self.capability_id)
        _require_text("operation", self.operation)
        _require_text("media_type", self.media_type)
        _require_digest("options_digest", self.options_digest)
        if not isinstance(self.output_evidence, OutputEvidence):
            raise ValueError("output_evidence must be an OutputEvidence")
        if isinstance(self.contract_major, bool) or self.contract_major < 1:
            raise ValueError("contract_major must be a positive integer")
        if isinstance(self.max_payload_bytes, bool) or self.max_payload_bytes < 1:
            raise ValueError("max_payload_bytes must be a positive integer")
        if isinstance(self.max_copies, bool) or self.max_copies < 1:
            raise ValueError("max_copies must be a positive integer")


@dataclass(frozen=True, slots=True)
class DriverProfile:
    """The immutable, signed contract shipped with one Driver."""

    profile_id: str
    version: str
    driver_id: str
    min_agent_version: str
    capabilities: tuple[DeviceCapability, ...]
    effective_at: datetime
    expires_at: datetime | None

    def __post_init__(self) -> None:
        _require_text("profile_id", self.profile_id)
        _require_text("version", self.version)
        _require_text("driver_id", self.driver_id)
        _require_text("min_agent_version", self.min_agent_version)
        if not isinstance(self.capabilities, tuple) or not self.capabilities:
            raise ValueError("a Driver Profile needs one Device Capability")
        ids = [capability.capability_id for capability in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("a Driver Profile cannot repeat a capability")
        _require_utc("effective_at", self.effective_at)
        if self.expires_at is not None:
            _require_utc("expires_at", self.expires_at)
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must follow effective_at")


@dataclass(frozen=True, slots=True)
class SignedDriverProfile:
    profile: DriverProfile
    digest: str
    signer_key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        _require_digest("digest", self.digest)
        _require_text("signer_key_id", self.signer_key_id)
        if len(self.signature) != 64:
            raise ValueError("an Ed25519 signature must contain 64 bytes")


@dataclass(frozen=True, slots=True)
class HardwareCertificationMatrixRow:
    """One exact physical Device, host, Driver, and media qualification."""

    row_id: str
    version: int
    device_id: str
    device_identity_digest: str
    manufacturer: str
    model: str
    firmware_version: str
    firmware_build: str
    driver_id: str
    driver_profile_digest: str
    capability_id: str
    platform_backend_id: str
    connection: str
    media_profile: str
    operating_system: str
    release_set_id: str
    effective_at: datetime
    expires_at: datetime | None

    def __post_init__(self) -> None:
        for name, value in (
            ("row_id", self.row_id),
            ("device_id", self.device_id),
            ("manufacturer", self.manufacturer),
            ("model", self.model),
            ("firmware_version", self.firmware_version),
            ("firmware_build", self.firmware_build),
            ("driver_id", self.driver_id),
            ("capability_id", self.capability_id),
            ("platform_backend_id", self.platform_backend_id),
            ("connection", self.connection),
            ("media_profile", self.media_profile),
            ("operating_system", self.operating_system),
            ("release_set_id", self.release_set_id),
        ):
            _require_text(name, value)
        _require_digest("device_identity_digest", self.device_identity_digest)
        _require_digest("driver_profile_digest", self.driver_profile_digest)
        if isinstance(self.version, bool) or self.version < 1:
            raise ValueError("matrix version must be positive")
        _require_utc("effective_at", self.effective_at)
        if self.expires_at is not None:
            _require_utc("expires_at", self.expires_at)
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must follow effective_at")


@dataclass(frozen=True, slots=True)
class SignedHardwareCertificationMatrixRow:
    row: HardwareCertificationMatrixRow
    digest: str
    signer_key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        _require_digest("digest", self.digest)
        _require_text("signer_key_id", self.signer_key_id)
        if len(self.signature) != 64:
            raise ValueError("an Ed25519 signature must contain 64 bytes")


@dataclass(frozen=True, slots=True)
class BindingRevision:
    """An immutable assignment of a Device Capability to an Odoo scope."""

    binding_id: str
    revision_id: str
    revision_number: int
    scope: AuthorityScope
    purpose: str
    device_id: str
    device_identity_digest: str
    capability_id: str
    driver_profile_digest: str
    matrix_row_id: str
    options_digest: str

    def __post_init__(self) -> None:
        for name, value in (
            ("binding_id", self.binding_id),
            ("revision_id", self.revision_id),
            ("purpose", self.purpose),
            ("device_id", self.device_id),
            ("capability_id", self.capability_id),
            ("matrix_row_id", self.matrix_row_id),
        ):
            _require_text(name, value)
        _require_digest("device_identity_digest", self.device_identity_digest)
        _require_digest("driver_profile_digest", self.driver_profile_digest)
        _require_digest("options_digest", self.options_digest)
        if isinstance(self.revision_number, bool) or self.revision_number < 1:
            raise ValueError("revision_number must be positive")


@dataclass(frozen=True, slots=True)
class SignedBindingRevision:
    revision: BindingRevision
    digest: str
    signer_key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        _require_digest("digest", self.digest)
        _require_text("signer_key_id", self.signer_key_id)
        if len(self.signature) != 64:
            raise ValueError("an Ed25519 signature must contain 64 bytes")


@dataclass(frozen=True, slots=True)
class DeviceTestEvidence:
    """The signed result of a physical Device Test for one exact graph."""

    evidence_id: str
    revision_id: str
    device_id: str
    device_identity_digest: str
    capability_id: str
    driver_profile_digest: str
    matrix_row_id: str
    output_evidence: OutputEvidence
    result: DeviceTestResult
    test_pattern_digest: str
    tested_at: datetime
    valid_until: datetime | None

    def __post_init__(self) -> None:
        for name, value in (
            ("evidence_id", self.evidence_id),
            ("revision_id", self.revision_id),
            ("device_id", self.device_id),
            ("capability_id", self.capability_id),
            ("matrix_row_id", self.matrix_row_id),
        ):
            _require_text(name, value)
        _require_digest("device_identity_digest", self.device_identity_digest)
        _require_digest("driver_profile_digest", self.driver_profile_digest)
        _require_digest("test_pattern_digest", self.test_pattern_digest)
        if not isinstance(self.output_evidence, OutputEvidence):
            raise ValueError("output_evidence must be an OutputEvidence")
        if not isinstance(self.result, DeviceTestResult):
            raise ValueError("result must be a DeviceTestResult")
        _require_utc("tested_at", self.tested_at)
        if self.valid_until is not None:
            _require_utc("valid_until", self.valid_until)
            if self.valid_until <= self.tested_at:
                raise ValueError("valid_until must follow tested_at")


@dataclass(frozen=True, slots=True)
class SignedDeviceTestEvidence:
    evidence: DeviceTestEvidence
    digest: str
    signer_key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        _require_digest("digest", self.digest)
        _require_text("signer_key_id", self.signer_key_id)
        if len(self.signature) != 64:
            raise ValueError("an Ed25519 signature must contain 64 bytes")


@dataclass(frozen=True, slots=True)
class DeviceObservation:
    """The current trusted Device identity and readiness observation."""

    observation_id: str
    device_id: str
    device_identity_digest: str
    driver_id: str
    driver_profile_digest: str
    platform_backend_id: str
    connection: str
    media_profile: str
    firmware_version: str
    firmware_build: str
    operating_system: str
    ready: bool
    state: str
    reason: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("observation_id", self.observation_id),
            ("device_id", self.device_id),
            ("driver_id", self.driver_id),
            ("platform_backend_id", self.platform_backend_id),
            ("connection", self.connection),
            ("media_profile", self.media_profile),
            ("firmware_version", self.firmware_version),
            ("firmware_build", self.firmware_build),
            ("operating_system", self.operating_system),
            ("state", self.state),
        ):
            _require_text(name, value)
        _require_digest("device_identity_digest", self.device_identity_digest)
        _require_digest("driver_profile_digest", self.driver_profile_digest)
        _require_utc("observed_at", self.observed_at)
        if not isinstance(self.ready, bool):
            raise ValueError("ready must be a boolean")


@dataclass(frozen=True, slots=True)
class SignedDeviceObservation:
    observation: DeviceObservation
    digest: str
    signer_key_id: str
    signature: bytes

    def __post_init__(self) -> None:
        _require_digest("digest", self.digest)
        _require_text("signer_key_id", self.signer_key_id)
        if len(self.signature) != 64:
            raise ValueError("an Ed25519 signature must contain 64 bytes")


@dataclass(frozen=True, slots=True)
class SignerRecord:
    key_id: str
    purpose: SignerPurpose
    public_key: bytes
    state: SignerState
    not_before: datetime
    not_after: datetime | None
    retired_at: datetime | None

    def __post_init__(self) -> None:
        _require_text("key_id", self.key_id)
        if len(self.public_key) != 32:
            raise ValueError("an Ed25519 public key must contain 32 bytes")
        if not isinstance(self.purpose, SignerPurpose):
            raise ValueError("purpose must be a SignerPurpose")
        if not isinstance(self.state, SignerState):
            raise ValueError("state must be a SignerState")
        _require_utc("not_before", self.not_before)
        if self.not_after is not None:
            _require_utc("not_after", self.not_after)
            if self.not_after <= self.not_before:
                raise ValueError("not_after must follow not_before")
        if self.retired_at is not None:
            _require_utc("retired_at", self.retired_at)
            if self.retired_at < self.not_before:
                raise ValueError("retired_at cannot precede not_before")
        if (self.state is SignerState.ACTIVE) != (self.retired_at is None):
            raise ValueError("signer state and retirement time must agree")


@dataclass(frozen=True, slots=True)
class CapabilityAdmissionTarget:
    """The requested Device Work whose signed graph the authority derives."""

    scope: AuthorityScope
    purpose: str
    device_id: str
    binding_revision_id: str
    operation: str
    media_type: str
    contract_major: int
    options_digest: str
    requested_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("purpose", self.purpose),
            ("device_id", self.device_id),
            ("binding_revision_id", self.binding_revision_id),
            ("operation", self.operation),
            ("media_type", self.media_type),
        ):
            _require_text(name, value)
        _require_digest("options_digest", self.options_digest)
        if isinstance(self.contract_major, bool) or self.contract_major < 1:
            raise ValueError("contract_major must be a positive integer")
        if self.requested_expires_at is not None:
            _require_utc("requested_expires_at", self.requested_expires_at)


@dataclass(frozen=True, slots=True)
class CapabilityStreamTarget:
    """A typed input stream whose signed capability graph the Agent derives."""

    scope: AuthorityScope
    purpose: str
    device_id: str
    binding_revision_id: str
    operation: str
    contract_major: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, AuthorityScope):
            raise TypeError("scope must be an AuthorityScope")
        for name, value in (
            ("purpose", self.purpose),
            ("device_id", self.device_id),
            ("binding_revision_id", self.binding_revision_id),
            ("operation", self.operation),
        ):
            _require_text(name, value)
        if isinstance(self.contract_major, bool) or self.contract_major < 1:
            raise ValueError("contract_major must be a positive integer")


@dataclass(frozen=True, slots=True)
class AuthorityProof:
    """The public, content-free evidence attached to an Admission Permit."""

    proof_id: str
    authority_revision_id: str
    authority_revision_number: int
    authority_revision_digest: str
    snapshot_digest: str
    graph_digest: str
    scope_digest: str
    observation_digest: str
    binding_revision_id: str
    binding_revision_digest: str
    driver_profile_id: str
    driver_profile_digest: str
    matrix_row_id: str
    matrix_row_digest: str
    test_evidence_id: str
    test_evidence_digest: str
    device_id: str
    device_identity_digest: str
    capability_id: str
    purpose: str
    operation: str
    media_type: str
    contract_major: int
    options_digest: str
    issued_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("proof_id", self.proof_id),
            ("authority_revision_id", self.authority_revision_id),
            ("binding_revision_id", self.binding_revision_id),
            ("driver_profile_id", self.driver_profile_id),
            ("matrix_row_id", self.matrix_row_id),
            ("test_evidence_id", self.test_evidence_id),
            ("device_id", self.device_id),
            ("capability_id", self.capability_id),
            ("purpose", self.purpose),
            ("operation", self.operation),
            ("media_type", self.media_type),
        ):
            _require_text(name, value)
        for name, value in (
            ("snapshot_digest", self.snapshot_digest),
            ("authority_revision_digest", self.authority_revision_digest),
            ("graph_digest", self.graph_digest),
            ("scope_digest", self.scope_digest),
            ("observation_digest", self.observation_digest),
            ("binding_revision_digest", self.binding_revision_digest),
            ("driver_profile_digest", self.driver_profile_digest),
            ("matrix_row_digest", self.matrix_row_digest),
            ("test_evidence_digest", self.test_evidence_digest),
            ("device_identity_digest", self.device_identity_digest),
            ("options_digest", self.options_digest),
        ):
            _require_digest(name, value)
        _require_utc("issued_at", self.issued_at)
        if (
            isinstance(self.authority_revision_number, bool)
            or self.authority_revision_number < 1
        ):
            raise ValueError("authority_revision_number must be positive")
        if isinstance(self.contract_major, bool) or self.contract_major < 1:
            raise ValueError("contract_major must be positive")
        _require_utc("valid_until", self.valid_until)
        if self.valid_until <= self.issued_at:
            raise ValueError("valid_until must follow issued_at")


class AdmissionPermit:
    """Opaque immutable proof that the authority accepted one Device Work graph."""

    __slots__ = ("__proof", "__token")

    def __init__(
        self, token: object, proof: AuthorityProof, permit_token: bytes
    ) -> None:
        if token is not _PERMIT_TOKEN:
            raise TypeError("AdmissionPermit values are issued by the authority")
        if len(permit_token) != 32:
            raise ValueError("permit_token must contain 32 bytes")
        object.__setattr__(self, "_AdmissionPermit__proof", proof)
        object.__setattr__(self, "_AdmissionPermit__token", permit_token)

    @classmethod
    def _issue(cls, proof: AuthorityProof, permit_token: bytes) -> AdmissionPermit:
        return cls(_PERMIT_TOKEN, proof, permit_token)

    @property
    def authority_proof(self) -> AuthorityProof:
        return self.__proof

    def _token_for_authority_check(self) -> bytes:
        return self.__token

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("AdmissionPermit is immutable")

    def __repr__(self) -> str:
        return "AdmissionPermit(<opaque>)"


_PERMIT_TOKEN = object()
JsonValue: TypeAlias = None | bool | int | float | str | list[Any] | dict[str, Any]


def _require_text(name: str, value: str | None) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty string")


def _require_digest(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_utc(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset() is None or value.astimezone(UTC) != value:
        raise ValueError(f"{name} must use UTC")


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy a JSON mapping before it crosses an immutable domain seam."""

    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("immutable JSON objects must use string keys")
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        if isinstance(item, tuple):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(value)
