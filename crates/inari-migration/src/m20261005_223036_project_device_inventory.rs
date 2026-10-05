use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct Migration;

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        // Device rows are a derived Projection. Existing fingerprints cannot
        // prove the versioned identity digest that the Agent publishes.
        manager
            .get_connection()
            .execute_unprepared(
                "ALTER TABLE managed_work DROP CONSTRAINT managed_work_device_fk;
             DELETE FROM devices;
             ALTER TABLE devices DROP CONSTRAINT devices_pkey;
             ALTER TABLE devices ADD PRIMARY KEY (agent_id, device_id);
             ALTER TABLE devices RENAME COLUMN hardware_fingerprint TO identity_digest;
             ALTER TABLE devices DROP CONSTRAINT devices_agent_hardware_key;
             ALTER TABLE devices ADD CONSTRAINT devices_agent_identity_key
                 UNIQUE (agent_id, kind, identity_digest);
             ALTER TABLE devices ADD CONSTRAINT devices_identity_digest_check
                 CHECK (identity_digest ~ '^[0-9a-f]{64}$');
             ALTER TABLE devices DROP CONSTRAINT devices_kind_check;
             ALTER TABLE devices ADD CONSTRAINT devices_kind_check
                 CHECK (kind IN ('printer', 'scale', 'scanner', 'display'));
             ALTER TABLE devices ADD COLUMN device_class TEXT NOT NULL
                 CHECK (device_class IN ('physical', 'virtual'));",
            )
            .await?;
        Ok(())
    }
}
