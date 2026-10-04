use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct Migration;

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.get_connection().execute_unprepared(
            "CREATE TABLE router_policies (
                organization_id TEXT PRIMARY KEY REFERENCES organizations(organization_id) ON DELETE CASCADE,
                authority_revision BIGINT NOT NULL DEFAULT 0 CHECK (authority_revision >= 0),
                policy_revision BIGINT,
                generation BIGINT NOT NULL DEFAULT 0 CHECK (generation BETWEEN 0 AND 9007199254740991),
                configuration_digest BYTEA CHECK (octet_length(configuration_digest) = 32),
                policy_digest BYTEA CHECK (octet_length(policy_digest) = 32),
                signed_policy JSONB,
                expires_at TIMESTAMPTZ,
                acknowledged_at TIMESTAMPTZ,
                CHECK ((generation = 0 AND policy_revision IS NULL AND signed_policy IS NULL
                        AND policy_digest IS NULL AND configuration_digest IS NULL AND expires_at IS NULL)
                    OR (generation > 0 AND policy_revision IS NOT NULL AND signed_policy IS NOT NULL
                        AND policy_digest IS NOT NULL AND configuration_digest IS NOT NULL AND expires_at IS NOT NULL))
             );
             INSERT INTO router_policies (organization_id) SELECT organization_id FROM organizations;
             CREATE FUNCTION initialize_router_policy() RETURNS TRIGGER LANGUAGE plpgsql AS $$
             BEGIN
                 INSERT INTO router_policies (organization_id) VALUES (NEW.organization_id);
                 RETURN NEW;
             END $$;
             CREATE TRIGGER organizations_router_policy AFTER INSERT ON organizations
                 FOR EACH ROW EXECUTE FUNCTION initialize_router_policy();
             CREATE FUNCTION invalidate_router_policy() RETURNS TRIGGER LANGUAGE plpgsql AS $$
             DECLARE organization TEXT;
             BEGIN
                 IF TG_TABLE_NAME = 'agents' THEN
                     IF TG_OP <> 'INSERT' THEN
                         UPDATE router_policies SET authority_revision = authority_revision + 1,
                             acknowledged_at = NULL WHERE organization_id = OLD.organization_id;
                     END IF;
                     IF TG_OP <> 'DELETE' THEN
                         UPDATE router_policies SET authority_revision = authority_revision + 1,
                             acknowledged_at = NULL WHERE organization_id = NEW.organization_id;
                     END IF;
                 ELSE
                     SELECT organization_id INTO organization FROM agents
                         WHERE agent_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.agent_id ELSE NEW.agent_id END;
                     UPDATE router_policies SET authority_revision = authority_revision + 1,
                         acknowledged_at = NULL WHERE organization_id = organization;
                 END IF;
                 RETURN NULL;
             END $$;
             CREATE TRIGGER agents_router_policy AFTER INSERT OR UPDATE OR DELETE ON agents
                 FOR EACH ROW EXECUTE FUNCTION invalidate_router_policy();
             CREATE TRIGGER agent_keys_router_policy AFTER INSERT OR UPDATE OR DELETE ON agent_verification_keys
                 FOR EACH ROW EXECUTE FUNCTION invalidate_router_policy();"
        ).await?;
        Ok(())
    }
}
