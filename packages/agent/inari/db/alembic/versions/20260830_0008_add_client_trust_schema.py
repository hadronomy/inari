"""Create the content-free Client Trust persistence schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260830_0008"
down_revision = "20260828_0007"
branch_labels = None
depends_on = None

_PERMISSIONS = (
    "'device_work:receipt_image', 'device_work:drawer', 'device_read:scale', "
    "'device_read:scanner', 'device_test:run', 'events:read', 'jobs:read', 'jobs:submit'"
)


def upgrade() -> None:
    _create_pairing_requests()
    _create_pairings()
    _create_grants()
    _create_replays()
    _create_nonces()
    _create_permission_guards()
    _create_immutability_guards()
    _create_grant_scope_guard()
    _create_lifecycle_guards()


def downgrade() -> None:
    for name in (
        "trg_client_trust_grants_lifecycle",
        "trg_client_trust_pairings_lifecycle",
        "trg_client_trust_pairing_requests_state",
        "trg_client_trust_grants_immutable",
        "trg_client_trust_pairings_immutable",
        "trg_client_trust_pairing_requests_immutable",
        "trg_client_trust_grants_pairing_scope",
        "trg_client_trust_grants_pairing_scope_update",
        "trg_client_trust_grants_permissions_insert",
        "trg_client_trust_grants_permissions_update",
        "trg_client_trust_pairings_permissions_insert",
        "trg_client_trust_pairings_permissions_update",
        "trg_client_trust_pairing_requests_permissions_insert",
        "trg_client_trust_pairing_requests_permissions_update",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))
    op.drop_index("idx_client_trust_nonces_expiry", table_name="client_trust_nonces")
    op.drop_table("client_trust_nonces")
    op.drop_index("idx_client_trust_replays_expiry", table_name="client_trust_replays")
    op.drop_table("client_trust_replays")
    op.drop_index(
        "idx_client_trust_grants_pairing_id", table_name="client_trust_grants"
    )
    op.drop_table("client_trust_grants")
    op.drop_index("idx_client_trust_pairings_scope", table_name="client_trust_pairings")
    op.drop_table("client_trust_pairings")
    op.drop_index(
        "idx_client_trust_pairing_requests_state_expires_at",
        table_name="client_trust_pairing_requests",
    )
    op.drop_table("client_trust_pairing_requests")


def _create_pairing_requests() -> None:
    op.create_table(
        "client_trust_pairing_requests",
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("browser_origin", sa.String(), nullable=False),
        sa.Column("agent_endpoint", sa.String(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String()),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("browser_jwk_thumbprint", sa.String(), nullable=False),
        sa.Column("requested_permissions", sa.Text(), nullable=False),
        sa.Column("session_nonce", sa.String(), nullable=False),
        sa.Column("phrase", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(request_id) BETWEEN 1 AND 256 AND length(agent_id) BETWEEN 1 AND 256 AND length(database) BETWEEN 1 AND 256 AND length(company_id) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND length(audience) BETWEEN 1 AND 256 AND length(browser_jwk_thumbprint) BETWEEN 8 AND 512 AND length(session_nonce) BETWEEN 1 AND 512 AND length(phrase) BETWEEN 5 AND 256",
            name="ck_client_trust_pairing_requests_identity",
        ),
        sa.CheckConstraint(
            "length(browser_origin) > 8 AND browser_origin LIKE 'https://%' AND length(browser_origin) - length(replace(browser_origin, '/', '')) = 2 AND browser_origin NOT LIKE '%?%' AND browser_origin NOT LIKE '%#%' AND browser_origin NOT LIKE '%@%' AND browser_origin NOT LIKE '%*%' AND length(agent_endpoint) > 8 AND agent_endpoint LIKE 'https://%' AND length(agent_endpoint) - length(replace(agent_endpoint, '/', '')) = 2 AND agent_endpoint NOT LIKE '%?%' AND agent_endpoint NOT LIKE '%#%' AND agent_endpoint NOT LIKE '%@%' AND agent_endpoint NOT LIKE '%*%'",
            name="ck_client_trust_pairing_requests_origin",
        ),
        sa.CheckConstraint(
            "pos_configuration_id IS NULL OR length(pos_configuration_id) BETWEEN 1 AND 256",
            name="ck_client_trust_pairing_requests_scope",
        ),
        sa.CheckConstraint(
            "json_valid(requested_permissions) AND json_type(requested_permissions) = 'array' AND requested_permissions = json(requested_permissions)",
            name="ck_client_trust_pairing_requests_permissions",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_client_trust_pairing_requests_validity"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'denied', 'canceled', 'expired', 'completed')",
            name="ck_client_trust_pairing_requests_state",
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "idx_client_trust_pairing_requests_state_expires_at",
        "client_trust_pairing_requests",
        ["state", "expires_at"],
    )


def _create_pairings() -> None:
    op.create_table(
        "client_trust_pairings",
        sa.Column("pairing_id", sa.String(), nullable=False),
        sa.Column("pairing_request_id", sa.String(), nullable=False),
        sa.Column("jwk_thumbprint", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("browser_origin", sa.String(), nullable=False),
        sa.Column("agent_endpoint", sa.String(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String()),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("permissions", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String()),
        sa.Column("lifecycle", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String()),
        sa.CheckConstraint(
            "length(pairing_id) BETWEEN 1 AND 256 AND length(pairing_request_id) BETWEEN 1 AND 256 AND length(jwk_thumbprint) BETWEEN 8 AND 512 AND length(agent_id) BETWEEN 1 AND 256 AND length(database) BETWEEN 1 AND 256 AND length(company_id) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND length(audience) BETWEEN 1 AND 256 AND length(actor_id) BETWEEN 1 AND 256 AND length(role) BETWEEN 1 AND 256",
            name="ck_client_trust_pairings_identity",
        ),
        sa.CheckConstraint(
            "length(browser_origin) > 8 AND browser_origin LIKE 'https://%' AND length(browser_origin) - length(replace(browser_origin, '/', '')) = 2 AND browser_origin NOT LIKE '%?%' AND browser_origin NOT LIKE '%#%' AND browser_origin NOT LIKE '%@%' AND browser_origin NOT LIKE '%*%' AND length(agent_endpoint) > 8 AND agent_endpoint LIKE 'https://%' AND length(agent_endpoint) - length(replace(agent_endpoint, '/', '')) = 2 AND agent_endpoint NOT LIKE '%?%' AND agent_endpoint NOT LIKE '%#%' AND agent_endpoint NOT LIKE '%@%' AND agent_endpoint NOT LIKE '%*%'",
            name="ck_client_trust_pairings_origin",
        ),
        sa.CheckConstraint(
            "pos_configuration_id IS NULL OR length(pos_configuration_id) BETWEEN 1 AND 256",
            name="ck_client_trust_pairings_scope",
        ),
        sa.CheckConstraint(
            "json_valid(permissions) AND json_type(permissions) = 'array' AND permissions = json(permissions)",
            name="ck_client_trust_pairings_permissions",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_client_trust_pairings_validity",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('active', 'revoked', 'expired')",
            name="ck_client_trust_pairings_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["pairing_request_id"],
            ["client_trust_pairing_requests.request_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("pairing_id"),
    )
    op.create_index(
        "idx_client_trust_pairings_scope",
        "client_trust_pairings",
        ["agent_id", "database", "organization_id", "site_id", "pos_configuration_id"],
    )


def _create_grants() -> None:
    op.create_table(
        "client_trust_grants",
        sa.Column("grant_id", sa.String(), nullable=False),
        sa.Column("pairing_id", sa.String(), nullable=False),
        sa.Column("jwk_thumbprint", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("browser_origin", sa.String(), nullable=False),
        sa.Column("agent_endpoint", sa.String(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String()),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("permissions", sa.Text(), nullable=False),
        sa.Column("authorization_digest", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("offline_renewal_until", sa.String()),
        sa.Column("lifecycle", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String()),
        sa.CheckConstraint(
            "length(grant_id) BETWEEN 1 AND 256 AND length(pairing_id) BETWEEN 1 AND 256 AND length(jwk_thumbprint) BETWEEN 8 AND 512 AND length(agent_id) BETWEEN 1 AND 256 AND length(database) BETWEEN 1 AND 256 AND length(company_id) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND length(audience) BETWEEN 1 AND 256 AND length(actor_id) BETWEEN 1 AND 256 AND length(role) BETWEEN 1 AND 256 AND length(authorization_digest) BETWEEN 1 AND 256 AND generation >= 0",
            name="ck_client_trust_grants_identity",
        ),
        sa.CheckConstraint(
            "length(browser_origin) > 8 AND browser_origin LIKE 'https://%' AND length(browser_origin) - length(replace(browser_origin, '/', '')) = 2 AND browser_origin NOT LIKE '%?%' AND browser_origin NOT LIKE '%#%' AND browser_origin NOT LIKE '%@%' AND browser_origin NOT LIKE '%*%' AND length(agent_endpoint) > 8 AND agent_endpoint LIKE 'https://%' AND length(agent_endpoint) - length(replace(agent_endpoint, '/', '')) = 2 AND agent_endpoint NOT LIKE '%?%' AND agent_endpoint NOT LIKE '%#%' AND agent_endpoint NOT LIKE '%@%' AND agent_endpoint NOT LIKE '%*%'",
            name="ck_client_trust_grants_origin",
        ),
        sa.CheckConstraint(
            "pos_configuration_id IS NULL OR length(pos_configuration_id) BETWEEN 1 AND 256",
            name="ck_client_trust_grants_scope",
        ),
        sa.CheckConstraint(
            "json_valid(permissions) AND json_type(permissions) = 'array' AND permissions = json(permissions)",
            name="ck_client_trust_grants_permissions",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND (offline_renewal_until IS NULL OR offline_renewal_until > expires_at)",
            name="ck_client_trust_grants_validity",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('active', 'revoked', 'expired')",
            name="ck_client_trust_grants_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["pairing_id"], ["client_trust_pairings.pairing_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("grant_id"),
    )
    op.create_index(
        "idx_client_trust_grants_pairing_id",
        "client_trust_grants",
        ["pairing_id", "expires_at"],
    )


def _create_replays() -> None:
    op.create_table(
        "client_trust_replays",
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String()),
        sa.CheckConstraint(
            "kind IN ('pairing_assertion', 'dpop') AND length(jti) BETWEEN 1 AND 512",
            name="ck_client_trust_replays_identity",
        ),
        sa.CheckConstraint(
            "(kind = 'pairing_assertion' AND expires_at IS NULL) OR (kind = 'dpop' AND expires_at IS NOT NULL AND expires_at > consumed_at)",
            name="ck_client_trust_replays_kind_shape",
        ),
        sa.PrimaryKeyConstraint("kind", "jti"),
    )
    op.create_index(
        "idx_client_trust_replays_expiry", "client_trust_replays", ["expires_at"]
    )


def _create_nonces() -> None:
    op.create_table(
        "client_trust_nonces",
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("issued_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.String()),
        sa.CheckConstraint(
            "length(nonce) BETWEEN 8 AND 512",
            name="ck_client_trust_nonces_identity",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at AND (consumed_at IS NULL OR consumed_at >= issued_at)",
            name="ck_client_trust_nonces_validity",
        ),
        sa.PrimaryKeyConstraint("nonce"),
    )
    op.create_index(
        "idx_client_trust_nonces_expiry", "client_trust_nonces", ["expires_at"]
    )


def _create_permission_guards() -> None:
    for table, column in (
        ("client_trust_pairing_requests", "requested_permissions"),
        ("client_trust_pairings", "permissions"),
        ("client_trust_grants", "permissions"),
    ):
        trigger_base = table.replace("-", "_")
        for operation in ("INSERT", "UPDATE"):
            op.execute(
                sa.text(
                    f"""
                    CREATE TRIGGER trg_{trigger_base}_permissions_{operation.lower()}
                    BEFORE {operation} ON {table}
                    WHEN EXISTS (
                        SELECT 1 FROM json_each(NEW.{column})
                        WHERE type != 'text' OR value NOT IN ({_PERMISSIONS})
                    ) OR json_array_length(NEW.{column}) != (
                        SELECT COUNT(DISTINCT value) FROM json_each(NEW.{column})
                    )
                    BEGIN
                        SELECT RAISE(ABORT, 'Client Trust permissions are not closed or unique');
                    END
                    """
                )
            )


def _create_immutability_guards() -> None:
    _immutable_trigger(
        "client_trust_pairing_requests",
        "request_id, agent_id, browser_origin, agent_endpoint, database, company_id, organization_id, site_id, pos_configuration_id, audience, browser_jwk_thumbprint, requested_permissions, session_nonce, phrase, created_at, expires_at",
    )
    _immutable_trigger(
        "client_trust_pairings",
        "pairing_id, pairing_request_id, jwk_thumbprint, agent_id, browser_origin, agent_endpoint, database, company_id, organization_id, site_id, pos_configuration_id, audience, actor_id, role, permissions, created_at, expires_at",
    )
    _immutable_trigger(
        "client_trust_grants",
        "grant_id, pairing_id, jwk_thumbprint, agent_id, browser_origin, agent_endpoint, database, company_id, organization_id, site_id, pos_configuration_id, audience, actor_id, role, permissions, authorization_digest, offline_renewal_until",
    )


def _immutable_trigger(table: str, immutable: str) -> None:
    old = [part.strip() for part in immutable.split(",")]
    predicate = " OR ".join(f"OLD.{name} IS NOT NEW.{name}" for name in old)
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE ON {table}
            WHEN {predicate}
            BEGIN
                SELECT RAISE(ABORT, 'Client Trust identity is immutable');
            END
            """
        )
    )


def _create_lifecycle_guards() -> None:
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_client_trust_pairing_requests_state
            BEFORE UPDATE OF state ON client_trust_pairing_requests
            WHEN NOT (
                OLD.state = NEW.state OR
                (OLD.state = 'pending' AND NEW.state IN ('approved', 'denied', 'canceled', 'expired', 'completed')) OR
                (OLD.state = 'approved' AND NEW.state IN ('completed', 'canceled', 'expired'))
            )
            BEGIN
                SELECT RAISE(ABORT, 'Pairing Request state transition is invalid');
            END
            """
        )
    )
    for table, column in (
        ("client_trust_pairings", "lifecycle"),
        ("client_trust_grants", "lifecycle"),
    ):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_lifecycle
                BEFORE UPDATE OF {column} ON {table}
                WHEN NOT (
                    OLD.{column} = NEW.{column} OR
                    (OLD.{column} = 'active' AND NEW.{column} IN ('revoked', 'expired'))
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Client Trust lifecycle transition is invalid');
                END
                """
            )
        )


def _create_grant_scope_guard() -> None:
    for operation in (
        "INSERT",
        "UPDATE OF pairing_id, jwk_thumbprint, agent_id, browser_origin, agent_endpoint, database, company_id, organization_id, site_id, pos_configuration_id, audience, actor_id, role",
    ):
        suffix = "update" if operation.startswith("UPDATE") else ""
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_client_trust_grants_pairing_scope{("_" + suffix) if suffix else ""}
                BEFORE {operation} ON client_trust_grants
                WHEN NOT EXISTS (
                    SELECT 1 FROM client_trust_pairings AS pairing
                    WHERE pairing.pairing_id = NEW.pairing_id
                      AND pairing.jwk_thumbprint = NEW.jwk_thumbprint
                      AND pairing.agent_id = NEW.agent_id
                      AND pairing.browser_origin = NEW.browser_origin
                      AND pairing.agent_endpoint = NEW.agent_endpoint
                      AND pairing.database = NEW.database
                      AND pairing.company_id = NEW.company_id
                      AND pairing.organization_id = NEW.organization_id
                      AND pairing.site_id = NEW.site_id
                      AND pairing.pos_configuration_id IS NEW.pos_configuration_id
                      AND pairing.audience = NEW.audience
                      AND pairing.actor_id = NEW.actor_id
                      AND pairing.role = NEW.role
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Client Grant scope does not match its Client Pairing');
                END
                """
            )
        )
