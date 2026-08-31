from __future__ import annotations

import os
from urllib.parse import urlsplit


def _is_https_origin(value):
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def pos_binding_projection(env, config, binding):
    """Return the exact browser projection for one active local Device Binding."""

    revision = binding.active_revision_id
    if not revision or revision.state != "active":
        return False
    agent = revision.device_id.agent_id
    browser_origin = config.get_base_url().rstrip("/")
    endpoint = (
        env["inari.agent.endpoint"]
        .sudo()
        .search(
            [
                ("company_id", "=", config.company_id.id),
                ("agent_id", "=", agent.id),
                ("origin", "=", browser_origin),
                ("state", "=", "active"),
            ],
            limit=1,
        )
    )
    endpoint_url = (
        endpoint.endpoint_url if _is_https_origin(endpoint.endpoint_url) else False
    )
    return {
        "authoritative": True,
        "state": "ready" if endpoint_url else "agent_endpoint_required",
        "database": env.cr.dbname,
        "company_id": str(config.company_id.id),
        "organization_id": binding.site_id.organization_id.controller_uuid,
        "site_id": binding.site_id.controller_uuid,
        "pos_configuration_id": str(config.id),
        "binding_revision_id": revision.revision_id,
        "device_id": revision.device_id.device_id,
        "agent_id": agent.agent_id,
        "browser_origin": browser_origin,
        "agent_endpoint": endpoint_url,
        "audience": os.environ.get("INARI_AGENT_TOKEN_AUDIENCE", "inari-agent"),
        "requested_permissions": ["receipt_image"],
        "authorization_digest": revision.authorization_digest,
        "capability_id": revision.capability_id.controller_uuid,
        "contract_major": revision.capability_id.contract_major,
        "driver_profile_digest": revision.driver_profile_digest,
    }


__all__ = ["pos_binding_projection"]
