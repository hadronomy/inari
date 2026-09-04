use chrono::{DateTime, Utc};
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, ConnectionTrait, DatabaseTransaction,
    DbBackend, EntityTrait, FromQueryResult, QueryFilter, QuerySelect, Statement, TransactionTrait,
};
use sha2::{Digest, Sha256};

use super::entity::value::{
    CommandState, ManagedDocumentOperationValue, ManagedWorkStateValue, StoredCommand,
    StoredManagedWorkPreflightRequest, StoredReportBindingClaim, StoredSealedManagedDispatch,
};
use super::entity::{agent, command, device, managed_work, managed_work_preflight};
use super::{
    GatewayRepository, ManagedWorkTargetRecord, NewManagedWorkPreflight, PersistedManagedWork,
    PersistedManagedWorkDispatch, stored_time, utc_time,
};
use crate::protocol::{
    ControllerCommand, DispatchEncryptionKey, ManagedDocumentOperation, ManagedWorkId,
    ManagedWorkPreflightRequest, ManagedWorkState, ManagedWorkSubmission, SealedManagedDispatch,
};
use crate::{GatewayError, GatewayResult};

const AGENT_PENDING_WORK_LIMIT: i64 = 32;
const AGENT_PENDING_BYTE_LIMIT: i64 = 64 * 1024 * 1024;
const ORGANIZATION_PENDING_WORK_LIMIT: i64 = 256;
const ORGANIZATION_PENDING_BYTE_LIMIT: i64 = 512 * 1024 * 1024;

#[derive(Debug, FromQueryResult)]
struct PendingUsage {
    work_count: i64,
    payload_bytes: i64,
}

impl GatewayRepository {
    pub async fn create_managed_work_preflight(
        &self,
        preflight: NewManagedWorkPreflight,
    ) -> GatewayResult<()> {
        managed_work_preflight::ActiveModel {
            preflight_id: Set(preflight.preflight_id.into()),
            request: Set(StoredManagedWorkPreflightRequest(preflight.request)),
            dispatch_key_id: Set(preflight.dispatch_key_id),
            capability_digest: Set(preflight.capability_digest),
            work_expires_at: Set(stored_time(preflight.work_expires_at)),
            idempotency_expires_at: Set(stored_time(preflight.idempotency_expires_at)),
            submit_before: Set(stored_time(preflight.submit_before)),
            created_at: Set(stored_time(preflight.created_at)),
        }
        .insert(&self.database)
        .await?;
        Ok(())
    }

    pub async fn managed_work_target(
        &self,
        organization_id: &str,
        site_id: &str,
        agent_id: &str,
        device_id: &str,
    ) -> GatewayResult<ManagedWorkTargetRecord> {
        let agent = agent::Entity::find_by_id(agent_id)
            .one(&self.database)
            .await?
            .ok_or_else(|| GatewayError::NotFound("managed Agent was not found".into()))?;
        if agent.organization_id != organization_id || agent.site_id != site_id {
            return Err(GatewayError::Forbidden(
                "managed Agent scope does not match the request".into(),
            ));
        }
        let dispatch_key = agent.dispatch_key.ok_or_else(|| {
            GatewayError::Conflict("managed Agent must re-enroll with a dispatch key".into())
        })?;
        let device = device::Entity::find_by_id(device_id)
            .one(&self.database)
            .await?
            .ok_or_else(|| GatewayError::NotFound("managed Device was not found".into()))?;
        if device.agent_id != agent_id || device.site_id != site_id {
            return Err(GatewayError::Forbidden(
                "managed Device scope does not match the request".into(),
            ));
        }
        Ok(ManagedWorkTargetRecord {
            organization_id: agent.organization_id.parse()?,
            site_id: agent.site_id.parse()?,
            agent_id: agent.agent_id.parse()?,
            device_id: device.device_id.parse()?,
            device_state: device.state.into(),
            capabilities: device.capabilities.0,
            dispatch_key: dispatch_key.0,
        })
    }

    pub async fn admit_managed_work<F>(
        &self,
        managed_work_id: &ManagedWorkId,
        submission: &ManagedWorkSubmission,
        request_fingerprint: &[u8; 32],
        payload_fingerprint: &[u8; 32],
        payload_bytes: i64,
        now: DateTime<Utc>,
        build_command: F,
    ) -> GatewayResult<PersistedManagedWorkDispatch>
    where
        F: FnOnce(
            &ManagedWorkId,
            u64,
            &str,
            &str,
            DateTime<Utc>,
            &DispatchEncryptionKey,
            DateTime<Utc>,
        ) -> GatewayResult<ControllerCommand>,
    {
        let transaction = self.database.begin().await?;
        transaction
            .execute_raw(Statement::from_sql_and_values(
                DbBackend::Postgres,
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0)), pg_advisory_xact_lock(hashtextextended($2, 0))",
                [
                    submission.work.scope.organization_id.as_str().to_owned().into(),
                    submission.work.scope.agent_id.as_str().to_owned().into(),
                ],
            ))
            .await?;

        if let Some(existing) = managed_work::Entity::find_by_id(managed_work_id.as_str())
            .one(&transaction)
            .await?
        {
            if existing.request_fingerprint.as_slice() != request_fingerprint {
                return Err(GatewayError::Conflict(
                    "the idempotency key was already used for another Managed Work request".into(),
                ));
            }
            return existing_dispatch(transaction, existing).await;
        }
        if let Some(existing) = managed_work::Entity::find()
            .filter(
                managed_work::Column::PrintIntentId.eq(submission.work.print_intent_id.as_str()),
            )
            .one(&transaction)
            .await?
        {
            if existing.request_fingerprint.as_slice() != request_fingerprint {
                return Err(GatewayError::Conflict(
                    "the Print Intent was already used for another Managed Work request".into(),
                ));
            }
            return existing_dispatch(transaction, existing).await;
        }

        let preflight =
            managed_work_preflight::Entity::find_by_id(submission.preflight_id.as_str())
                .lock_exclusive()
                .one(&transaction)
                .await?
                .ok_or_else(|| {
                    GatewayError::NotFound("Managed Work preflight was not found".into())
                })?;
        let expected_request = ManagedWorkPreflightRequest {
            contract_major: submission.contract_major,
            scope: submission.work.scope.clone(),
            device_id: submission.work.device_id.clone(),
            operation: submission.work.document.operation(),
            binding: submission.work.origin.binding.clone(),
        };
        if preflight.request.0 != expected_request {
            return Err(GatewayError::Conflict(
                "Managed Work submission does not match its preflight".into(),
            ));
        }
        if utc_time(preflight.submit_before) <= now {
            return Err(GatewayError::Conflict("Managed Work preflight has expired".into()));
        }

        let target = managed_work_target_in(
            &transaction,
            submission
                .work
                .scope
                .organization_id
                .as_str(),
            submission.work.scope.site_id.as_str(),
            submission.work.scope.agent_id.as_str(),
            submission.work.device_id.as_str(),
        )
        .await?;
        if target.0.key_id != preflight.dispatch_key_id {
            return Err(GatewayError::Conflict(
                "the Managed Work preflight does not use the current Agent dispatch key".into(),
            ));
        }

        let agent_usage =
            pending_usage(&transaction, "agent_id", submission.work.scope.agent_id.as_str())
                .await?;
        enforce_quota(
            agent_usage,
            payload_bytes,
            AGENT_PENDING_WORK_LIMIT,
            AGENT_PENDING_BYTE_LIMIT,
            "Agent",
        )?;
        let organization_usage = pending_usage(
            &transaction,
            "organization_id",
            submission
                .work
                .scope
                .organization_id
                .as_str(),
        )
        .await?;
        enforce_quota(
            organization_usage,
            payload_bytes,
            ORGANIZATION_PENDING_WORK_LIMIT,
            ORGANIZATION_PENDING_BYTE_LIMIT,
            "Organization",
        )?;

        let work_expires_at = utc_time(preflight.work_expires_at);
        let idempotency_expires_at = utc_time(preflight.idempotency_expires_at);
        let (command, sealed_dispatch) = insert_dispatch_command(
            &transaction,
            managed_work_id,
            submission.work.scope.agent_id.as_str(),
            request_fingerprint,
            now,
            &target.0,
            work_expires_at,
            build_command,
        )
        .await?;
        let model = managed_work::ActiveModel {
            managed_work_id: Set(managed_work_id.as_str().to_owned()),
            preflight_id: Set(submission
                .preflight_id
                .as_str()
                .to_owned()),
            organization_id: Set(submission
                .work
                .scope
                .organization_id
                .as_str()
                .to_owned()),
            database_name: Set(submission.work.scope.database.clone()),
            company_id: Set(submission.work.scope.company_id.clone()),
            site_id: Set(submission
                .work
                .scope
                .site_id
                .as_str()
                .to_owned()),
            agent_id: Set(submission
                .work
                .scope
                .agent_id
                .as_str()
                .to_owned()),
            device_id: Set(submission
                .work
                .device_id
                .as_str()
                .to_owned()),
            print_intent_id: Set(submission
                .work
                .print_intent_id
                .as_str()
                .to_owned()),
            operation: Set(submission
                .work
                .document
                .operation()
                .into()),
            media_type: Set(submission
                .work
                .document
                .operation()
                .media_type()
                .to_owned()),
            state: Set(ManagedWorkStateValue::Dispatching),
            binding_claim: Set(StoredReportBindingClaim(submission.work.origin.binding.clone())),
            payload_fingerprint: Set(payload_fingerprint.to_vec()),
            request_fingerprint: Set(request_fingerprint.to_vec()),
            sealed_document: Set(StoredSealedManagedDispatch(sealed_dispatch)),
            payload_bytes: Set(payload_bytes),
            print_job_id: Set(None),
            error_code: Set(None),
            message_key: Set("managed_work.dispatching".into()),
            expires_at: Set(stored_time(work_expires_at)),
            idempotency_expires_at: Set(stored_time(idempotency_expires_at)),
            admitted_at: Set(stored_time(now)),
            updated_at: Set(stored_time(now)),
        }
        .insert(&transaction)
        .await?;
        transaction.commit().await?;
        Ok(PersistedManagedWorkDispatch {
            managed_work: persisted_managed_work(model)?,
            command: Some(command),
        })
    }

    pub async fn managed_work(
        &self,
        managed_work_id: &ManagedWorkId,
    ) -> GatewayResult<PersistedManagedWork> {
        managed_work::Entity::find_by_id(managed_work_id.as_str())
            .one(&self.database)
            .await?
            .ok_or_else(|| GatewayError::NotFound("Managed Work was not found".into()))
            .and_then(persisted_managed_work)
    }
}

async fn existing_dispatch(
    transaction: DatabaseTransaction,
    existing: managed_work::Model,
) -> GatewayResult<PersistedManagedWorkDispatch> {
    let command = if existing.state == ManagedWorkStateValue::Dispatching {
        let command_id = dispatch_command_id(&existing.managed_work_id);
        let model = command::Entity::find_by_id(&command_id)
            .one(&transaction)
            .await?
            .ok_or_else(|| {
                GatewayError::CorruptState(
                    "dispatching Managed Work has no durable Controller command".into(),
                )
            })?;
        let namespace = agent::Entity::find_by_id(&existing.agent_id)
            .one(&transaction)
            .await?
            .ok_or_else(|| GatewayError::CorruptState("Managed Work Agent is missing".into()))?
            .namespace;
        Some(super::commands::persisted_command(model, namespace)?)
    } else {
        None
    };
    transaction.commit().await?;
    Ok(PersistedManagedWorkDispatch { managed_work: persisted_managed_work(existing)?, command })
}

async fn insert_dispatch_command<C, F>(
    transaction: &C,
    managed_work_id: &ManagedWorkId,
    agent_id: &str,
    request_fingerprint: &[u8; 32],
    issued_at: DateTime<Utc>,
    recipient_key: &DispatchEncryptionKey,
    work_expires_at: DateTime<Utc>,
    build_command: F,
) -> GatewayResult<(super::PersistedCommand, SealedManagedDispatch)>
where
    C: ConnectionTrait,
    F: FnOnce(
        &ManagedWorkId,
        u64,
        &str,
        &str,
        DateTime<Utc>,
        &DispatchEncryptionKey,
        DateTime<Utc>,
    ) -> GatewayResult<ControllerCommand>,
{
    let managed_agent = agent::Entity::find_by_id(agent_id)
        .one(transaction)
        .await?
        .ok_or_else(|| GatewayError::CorruptState("Managed Work Agent is missing".into()))?;
    let next = super::commands::next_command_sequence(transaction, agent_id).await?;
    let sequence = u64::try_from(next)
        .map_err(|_| GatewayError::Conflict("command sequence is out of range".into()))?;
    let command_id = dispatch_command_id(managed_work_id.as_str());
    let message_id = format!("msg_{command_id}");
    let command_message = build_command(
        managed_work_id,
        sequence,
        &command_id,
        &message_id,
        issued_at,
        recipient_key,
        work_expires_at,
    )?;
    let ControllerCommand::DispatchDeviceWork { payload, .. } = &command_message else {
        return Err(GatewayError::CorruptState(
            "Managed Work dispatch builder returned another command type".into(),
        ));
    };
    let sealed_dispatch = payload.sealed_envelope.clone();
    let model = command::ActiveModel {
        command_id: Set(command_id),
        agent_id: Set(agent_id.to_owned()),
        message_id: Set(message_id),
        sequence: Set(next),
        state: Set(CommandState::Queued),
        command: Set(StoredCommand(command_message)),
        request_fingerprint: Set(request_fingerprint.to_vec()),
        issued_at: Set(stored_time(issued_at)),
        published_at: Set(None),
        updated_at: Set(stored_time(issued_at)),
    }
    .insert(transaction)
    .await?;
    Ok((super::commands::persisted_command(model, managed_agent.namespace)?, sealed_dispatch))
}

fn dispatch_command_id(managed_work_id: &str) -> String {
    let digest = Sha256::digest(managed_work_id.as_bytes());
    format!("job_dispatch_{}", hex::encode(&digest[..16]))
}

async fn managed_work_target_in<C>(
    database: &C,
    organization_id: &str,
    site_id: &str,
    agent_id: &str,
    device_id: &str,
) -> GatewayResult<super::entity::value::StoredDispatchEncryptionKey>
where
    C: ConnectionTrait,
{
    let agent = agent::Entity::find_by_id(agent_id)
        .one(database)
        .await?
        .ok_or_else(|| GatewayError::NotFound("managed Agent was not found".into()))?;
    if agent.organization_id != organization_id || agent.site_id != site_id {
        return Err(GatewayError::Forbidden(
            "managed Agent scope does not match the request".into(),
        ));
    }
    let device = device::Entity::find_by_id(device_id)
        .one(database)
        .await?
        .ok_or_else(|| GatewayError::NotFound("managed Device was not found".into()))?;
    if device.agent_id != agent_id || device.site_id != site_id {
        return Err(GatewayError::Forbidden(
            "managed Device scope does not match the request".into(),
        ));
    }
    agent.dispatch_key.ok_or_else(|| {
        GatewayError::Conflict("managed Agent must re-enroll with a dispatch key".into())
    })
}

async fn pending_usage<C>(
    database: &C,
    scope_column: &str,
    scope_value: &str,
) -> GatewayResult<PendingUsage>
where
    C: ConnectionTrait,
{
    let statement = format!(
        "SELECT COUNT(*)::BIGINT AS work_count, COALESCE(SUM(payload_bytes), 0)::BIGINT AS payload_bytes FROM managed_work WHERE {scope_column} = $1 AND state IN ('pending_agent', 'dispatching', 'recovery_uncertain')"
    );
    PendingUsage::find_by_statement(Statement::from_sql_and_values(
        DbBackend::Postgres,
        statement,
        [scope_value.to_owned().into()],
    ))
    .one(database)
    .await?
    .ok_or_else(|| GatewayError::CorruptState("Managed Work usage query returned no row".into()))
}

fn enforce_quota(
    usage: PendingUsage,
    payload_bytes: i64,
    work_limit: i64,
    byte_limit: i64,
    scope_name: &str,
) -> GatewayResult<()> {
    let next_bytes = usage
        .payload_bytes
        .checked_add(payload_bytes)
        .ok_or_else(|| GatewayError::Conflict("Managed Work byte use is out of range".into()))?;
    if usage.work_count >= work_limit || next_bytes > byte_limit {
        return Err(GatewayError::Capacity(format!("{scope_name} Managed Work queue is full")));
    }
    Ok(())
}

fn persisted_managed_work(model: managed_work::Model) -> GatewayResult<PersistedManagedWork> {
    Ok(PersistedManagedWork {
        managed_work_id: model.managed_work_id.parse()?,
        print_intent_id: model.print_intent_id.parse()?,
        scope: crate::protocol::ManagedWorkScope {
            database: model.database_name,
            company_id: model.company_id,
            organization_id: model.organization_id.parse()?,
            site_id: model.site_id.parse()?,
            agent_id: model.agent_id.parse()?,
        },
        device_id: model.device_id.parse()?,
        operation: model.operation.into(),
        state: model.state.into(),
        print_job_id: model.print_job_id,
        error_code: model.error_code,
        message_key: model.message_key,
        admitted_at: utc_time(model.admitted_at),
        updated_at: utc_time(model.updated_at),
        expires_at: utc_time(model.expires_at),
    })
}

impl From<ManagedDocumentOperation> for ManagedDocumentOperationValue {
    fn from(operation: ManagedDocumentOperation) -> Self {
        match operation {
            ManagedDocumentOperation::ReportPdf => Self::ReportPdf,
            ManagedDocumentOperation::LabelDocument => Self::LabelDocument,
        }
    }
}

impl From<ManagedDocumentOperationValue> for ManagedDocumentOperation {
    fn from(operation: ManagedDocumentOperationValue) -> Self {
        match operation {
            ManagedDocumentOperationValue::ReportPdf => Self::ReportPdf,
            ManagedDocumentOperationValue::LabelDocument => Self::LabelDocument,
        }
    }
}

impl From<ManagedWorkStateValue> for ManagedWorkState {
    fn from(state: ManagedWorkStateValue) -> Self {
        match state {
            ManagedWorkStateValue::PendingAgent => Self::PendingAgent,
            ManagedWorkStateValue::Dispatching => Self::Dispatching,
            ManagedWorkStateValue::Accepted => Self::Accepted,
            ManagedWorkStateValue::Rejected => Self::Rejected,
            ManagedWorkStateValue::Canceled => Self::Canceled,
            ManagedWorkStateValue::Expired => Self::Expired,
            ManagedWorkStateValue::RecoveryUncertain => Self::RecoveryUncertain,
        }
    }
}
