use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct Migration;

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        // Enrollment now consumes an invitation in the same transaction that
        // stores the Agent, so `claimed` cannot occur. A claimed row has no
        // enrollment fingerprint to bind a retry to, so it needs a new
        // invitation. Enrolled rows keep a NULL fingerprint and reject retries.
        manager
            .get_connection()
            .execute_unprepared(
                "UPDATE invitations
                 SET state = 'failed',
                     failed_at = COALESCE(failed_at, CURRENT_TIMESTAMP),
                     last_error = 'Enrollment did not finish. Create a new invitation.'
                 WHERE state = 'claimed';
                 ALTER TABLE invitations DROP CONSTRAINT invitations_state_check;
                 ALTER TABLE invitations ADD CONSTRAINT invitations_state_check CHECK (
                     state IN ('created', 'enrolled', 'online', 'expired', 'failed', 'revoked')
                 );
                 ALTER TABLE invitations DROP COLUMN claimed_at;
                 ALTER TABLE invitations ADD COLUMN enrollment_fingerprint BYTEA
                     CONSTRAINT invitations_enrollment_fingerprint_check
                     CHECK (octet_length(enrollment_fingerprint) = 32);",
            )
            .await?;
        Ok(())
    }
}
