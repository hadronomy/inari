from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.http_client import RemoteServiceError

from ..services import (
    PAIRING_PERMISSION_ORDER,
    build_pairing_assertion_signer,
    pos_pairing_permissions,
)


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_THUMBPRINT = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{8,512}\Z")
_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "agent_id",
        "browser_origin",
        "agent_endpoint",
        "database",
        "company_id",
        "organization_id",
        "site_id",
        "pos_configuration_id",
        "audience",
        "browser_jwk_thumbprint",
        "requested_permissions",
        "session_nonce",
        "expires_at",
        "state",
    }
)
_PAIRING_PERMISSIONS = frozenset(PAIRING_PERMISSION_ORDER)


def _identifier(values, name):
    value = values.get(name)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValidationError(_("The Pairing Request contains an invalid %s.", name))
    return value


def _exact_https_origin(value, name):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            _("The Pairing Request contains an invalid %s.", name)
        ) from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(_("The Pairing Request contains an invalid %s.", name))
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", authority, "", "", ""))


def _utc_datetime(value, name):
    if not isinstance(value, str) or len(value) > 64:
        raise ValidationError(_("The Pairing Request contains an invalid %s.", name))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            _("The Pairing Request contains an invalid %s.", name)
        ) from exc
    if parsed.tzinfo is None:
        raise ValidationError(_("The Pairing Request contains an invalid %s.", name))
    return parsed.astimezone(UTC)


def _validated_request(values):
    if not isinstance(values, dict) or set(values) != _REQUEST_FIELDS:
        raise ValidationError(_("The Pairing Request shape is invalid."))
    permissions = values.get("requested_permissions")
    if (
        not isinstance(permissions, list)
        or not permissions
        or len(permissions) != len(set(permissions))
        or any(permission not in _PAIRING_PERMISSIONS for permission in permissions)
        or permissions
        != [
            permission
            for permission in PAIRING_PERMISSION_ORDER
            if permission in permissions
        ]
    ):
        raise AccessError(_("This POS requested invalid Device permissions."))
    thumbprint = values.get("browser_jwk_thumbprint")
    session_nonce = values.get("session_nonce")
    if not isinstance(thumbprint, str) or not _THUMBPRINT.fullmatch(thumbprint):
        raise ValidationError(_("The browser key identity is invalid."))
    if not isinstance(session_nonce, str) or not _TOKEN.fullmatch(session_nonce):
        raise ValidationError(_("The Pairing Request session identity is invalid."))
    if values.get("state") != "approved":
        raise UserError(_("Approve the Pairing Request in Device Center first."))
    return {
        "request_id": _identifier(values, "request_id"),
        "agent_id": _identifier(values, "agent_id"),
        "browser_origin": _exact_https_origin(
            values.get("browser_origin"), "browser_origin"
        ),
        "agent_endpoint": _exact_https_origin(
            values.get("agent_endpoint"), "agent_endpoint"
        ),
        "database": _identifier(values, "database"),
        "company_id": _identifier(values, "company_id"),
        "organization_id": _identifier(values, "organization_id"),
        "site_id": _identifier(values, "site_id"),
        "pos_configuration_id": _identifier(values, "pos_configuration_id"),
        "audience": _identifier(values, "audience"),
        "browser_jwk_thumbprint": thumbprint,
        "requested_permissions": permissions,
        "session_nonce": session_nonce,
        "expires_at": _utc_datetime(values.get("expires_at"), "expires_at"),
        "state": "approved",
    }


class InariAgentEndpoint(models.Model):
    _name = "inari.agent.endpoint"
    _description = "Authenticated local Agent Endpoint"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        ondelete="restrict",
        index=True,
    )
    agent_id = fields.Many2one(
        "inari.agent", required=True, ondelete="restrict", index=True
    )
    origin = fields.Char(required=True)
    endpoint_url = fields.Char(required=True)
    certificate_fingerprint = fields.Char(required=True)
    state = fields.Selection(
        [("active", "Active"), ("revoked", "Revoked")],
        default="active",
        index=True,
    )
    last_seen_at = fields.Datetime()

    _endpoint_origin_uniq = models.Constraint(
        "UNIQUE(company_id, agent_id, origin)",
        "An Agent origin can have one Endpoint.",
    )


class InariPairingAssertion(models.Model):
    _name = "inari.pairing.assertion"
    _description = "Issued Inari Pairing Assertion"
    _order = "issued_at desc, id desc"

    pairing_request_id = fields.Char(
        required=True, readonly=True, copy=False, index=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, ondelete="restrict", index=True
    )
    organization_id = fields.Many2one(
        "inari.organization", required=True, readonly=True, ondelete="restrict"
    )
    site_id = fields.Many2one(
        "inari.site", required=True, readonly=True, ondelete="restrict"
    )
    agent_id = fields.Many2one(
        "inari.agent", required=True, readonly=True, ondelete="restrict"
    )
    pos_config_id = fields.Many2one(
        "pos.config", required=True, readonly=True, ondelete="restrict"
    )
    pos_session_id = fields.Many2one(
        "pos.session", required=True, readonly=True, ondelete="restrict"
    )
    browser_key_thumbprint = fields.Char(required=True, readonly=True, index=True)
    browser_origin = fields.Char(required=True, readonly=True)
    request_fingerprint = fields.Char(required=True, readonly=True)
    assertion_jti = fields.Char(required=True, readonly=True, copy=False, index=True)
    signer_key_id = fields.Char(required=True, readonly=True)
    assertion = fields.Text(required=True, readonly=True, copy=False)
    issued_by = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict"
    )
    issued_at = fields.Datetime(required=True, readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)

    _pairing_request_uniq = models.Constraint(
        "UNIQUE(pairing_request_id)",
        "A Pairing Request can have one Pairing Assertion.",
    )
    _assertion_jti_uniq = models.Constraint(
        "UNIQUE(assertion_jti)",
        "A Pairing Assertion identity must be unique.",
    )

    @api.model
    def issue_for_pos(self, request_values, pos_session_id):
        """Issue or replay one assertion for the exact active POS receipt scope."""

        if not self.env.is_superuser() and not self.env.user.has_group(
            "inari_devices.group_inari_operator"
        ):
            raise AccessError(_("A Device Operator is required to pair this browser."))
        pairing_request = _validated_request(request_values)
        now = datetime.now(UTC)
        if pairing_request["expires_at"] <= now:
            raise UserError(_("The Pairing Request expired. Create a new request."))
        if pairing_request["expires_at"] > now + timedelta(minutes=11):
            raise ValidationError(_("The Pairing Request lifetime is invalid."))

        session = self._active_pos_session(pos_session_id)
        scope = self._local_device_scope(session.config_id, pairing_request["agent_id"])
        self._check_request_scope(pairing_request, scope)
        fingerprint = self._request_fingerprint(
            pairing_request, session.id, self.env.uid
        )

        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [pairing_request["request_id"]],
        )
        existing = self.sudo().search(
            [("pairing_request_id", "=", pairing_request["request_id"])], limit=1
        )
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise AccessError(_("The Pairing Request scope changed."))
            if (
                fields.Datetime.to_datetime(existing.expires_at).replace(tzinfo=UTC)
                <= now
            ):
                raise UserError(_("The Pairing Request expired. Create a new request."))
            return existing._response()

        assertion_jti = uuid4().hex
        expires_at = min(pairing_request["expires_at"], now + timedelta(minutes=2))
        claims = {
            "iss": os.environ.get("INARI_PAIRING_ASSERTION_ISSUER", "odoo"),
            "sub": pairing_request["request_id"],
            "aud": pairing_request["audience"],
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": assertion_jti,
            "pairing_request_id": pairing_request["request_id"],
            "agent_id": pairing_request["agent_id"],
            "jwk_thumbprint": pairing_request["browser_jwk_thumbprint"],
            "database": pairing_request["database"],
            "company_id": pairing_request["company_id"],
            "organization_id": pairing_request["organization_id"],
            "site_id": pairing_request["site_id"],
            "pos_configuration_id": pairing_request["pos_configuration_id"],
            "actor_id": str(self.env.uid),
            "role": "manager"
            if self.env.is_superuser()
            or self.env.user.has_group("inari_devices.group_inari_manager")
            else "operator",
            "scopes": list(pairing_request["requested_permissions"]),
            "session_nonce": pairing_request["session_nonce"],
        }
        try:
            signed = build_pairing_assertion_signer(
                database=self.env.cr.dbname,
                company_id=session.company_id.id,
            ).sign(claims)
        except RemoteServiceError as exc:
            raise UserError(
                _(
                    "Odoo could not sign the Pairing Assertion. "
                    "Try again or contact an administrator."
                )
            ) from exc

        record = self.sudo().create(
            {
                "pairing_request_id": pairing_request["request_id"],
                "company_id": session.company_id.id,
                "organization_id": scope["organization"].id,
                "site_id": scope["site"].id,
                "agent_id": scope["agent"].id,
                "pos_config_id": session.config_id.id,
                "pos_session_id": session.id,
                "browser_key_thumbprint": pairing_request["browser_jwk_thumbprint"],
                "browser_origin": pairing_request["browser_origin"],
                "request_fingerprint": fingerprint,
                "assertion_jti": assertion_jti,
                "signer_key_id": signed.signer_key_id,
                "assertion": signed.compact_jws,
                "issued_by": self.env.uid,
                "issued_at": fields.Datetime.to_string(now.replace(tzinfo=None)),
                "expires_at": fields.Datetime.to_string(
                    expires_at.replace(tzinfo=None)
                ),
            }
        )
        return record._response()

    def _active_pos_session(self, pos_session_id):
        try:
            session_id = int(pos_session_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("The POS session identity is invalid.")) from exc
        session = self.env["pos.session"].sudo().browse(session_id).exists()
        if (
            not session
            or session.company_id not in self.env.companies
            or session.state == "closed"
        ):
            raise AccessError(_("The POS session is not active for this user."))
        return session

    def _local_device_scope(self, config, requested_agent_id):
        bindings = (
            self.env["inari.device.binding"]
            .sudo()
            .search(
                [
                    ("company_id", "=", config.company_id.id),
                    ("pos_config_id", "=", config.id),
                    (
                        "purpose",
                        "in",
                        [
                            "pos_receipt",
                            "pos_preparation",
                            "pos_cash_drawer",
                            "pos_scale",
                            "pos_scanner",
                        ],
                    ),
                    ("active", "=", True),
                    ("state", "=", "active"),
                ]
            )
        )
        bindings = bindings.filtered(
            lambda candidate: (
                candidate.active_revision_id.state == "active"
                and candidate.active_revision_id.device_id.agent_id.agent_id
                == requested_agent_id
            )
        )
        binding = bindings[:1]
        revision = binding.active_revision_id
        if not revision:
            raise UserError(
                _(
                    "This POS has no active local Device Binding Revision for the "
                    "requested Agent."
                )
            )
        agent = revision.device_id.agent_id
        if (
            agent.site_id != binding.site_id
            or agent.organization_id != binding.site_id.organization_id
        ):
            raise ValidationError(
                _("The local Device Binding Revision has an invalid Agent scope.")
            )
        return {
            "binding": binding,
            "revision": revision,
            "agent": agent,
            "site": binding.site_id,
            "organization": binding.site_id.organization_id,
            "permissions": pos_pairing_permissions(self.env, config, agent),
        }

    def _check_request_scope(self, request_values, scope):
        config = scope["binding"].pos_config_id
        expected_origin = _exact_https_origin(config.get_base_url(), "Odoo origin")
        endpoint = (
            self.env["inari.agent.endpoint"]
            .sudo()
            .search(
                [
                    ("company_id", "=", config.company_id.id),
                    ("agent_id", "=", scope["agent"].id),
                    ("origin", "=", expected_origin),
                    ("state", "=", "active"),
                ],
                limit=1,
            )
        )
        expected = {
            "agent_id": scope["agent"].agent_id,
            "browser_origin": expected_origin,
            "agent_endpoint": _exact_https_origin(
                endpoint.endpoint_url, "Agent Endpoint"
            )
            if endpoint
            else None,
            "database": self.env.cr.dbname,
            "company_id": str(config.company_id.id),
            "organization_id": scope["organization"].controller_uuid,
            "site_id": scope["site"].controller_uuid,
            "pos_configuration_id": str(config.id),
            "audience": os.environ.get("INARI_AGENT_TOKEN_AUDIENCE", "inari-agent"),
            "requested_permissions": scope["permissions"],
        }
        if any(request_values[name] != value for name, value in expected.items()):
            raise AccessError(
                _("The Pairing Request does not match this POS Device scope.")
            )

    @staticmethod
    def _request_fingerprint(request_values, pos_session_id, actor_id):
        values = {
            **request_values,
            "expires_at": request_values["expires_at"].isoformat(),
            "pos_session_id": pos_session_id,
            "actor_id": actor_id,
        }
        encoded = json.dumps(
            values, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    def _response(self):
        self.ensure_one()
        return {
            "assertion": self.assertion,
            "expires_at": fields.Datetime.to_datetime(self.expires_at)
            .replace(tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
