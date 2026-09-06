use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use chrono::{SubsecRound, TimeDelta, Utc};
use inari_gateway::ManagedWorkAdmission;
use inari_gateway::protocol::{
    DeviceCapability, DeviceState, MANAGED_WORK_CONTRACT_MAJOR, ManagedDocumentOperation,
    ManagedPreflightId, ManagedWorkId, ManagedWorkPreflightRequest, ManagedWorkPreflightResult,
    ManagedWorkPreflightState, ManagedWorkReceipt, ManagedWorkRecord, ManagedWorkSubmission,
    managed_work_fingerprint,
};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;

use super::{ManagedGatewayController, StoredControllerCommand};
use crate::error::{AppError, AppResult};

const PREFLIGHT_TTL: Duration = Duration::from_secs(2 * 60);
const WORK_TTL: Duration = Duration::from_secs(5 * 60);
const IDEMPOTENCY_TTL: Duration = Duration::from_secs(90 * 24 * 60 * 60);
const REPORT_PDF_LIMIT: usize = 10 * 1024 * 1024;
const LABEL_DOCUMENT_LIMIT: usize = 2 * 1024 * 1024;

impl ManagedGatewayController {
    pub async fn preflight_managed_work(
        &self,
        request: ManagedWorkPreflightRequest,
    ) -> AppResult<ManagedWorkPreflightResult> {
        self.ensure_enabled()?;
        self.dispatch_signer()?;
        validate_preflight_request(&request)?;
        if request.scope.organization_id != self.inner.organization.id {
            return Err(AppError::forbidden(
                "Managed Work Organization does not match this Controller.",
            ));
        }
        let target = self
            .inner
            .store
            .managed_work_target(
                &request.scope.organization_id,
                &request.scope.site_id,
                &request.scope.agent_id,
                &request.device_id,
            )
            .await?;
        if target.device_state != DeviceState::Online {
            return Ok(blocked_preflight(&request, "device_offline"));
        }
        if !target
            .capabilities
            .contains(&DeviceCapability::Print)
        {
            return Ok(blocked_preflight(&request, "print_not_supported"));
        }

        let now = Utc::now().trunc_subsecs(6);
        let submit_before = now + duration_delta(PREFLIGHT_TTL)?;
        let expires_at = now + duration_delta(WORK_TTL)?;
        let idempotency_expires_at = now + duration_delta(IDEMPOTENCY_TTL)?;
        let preflight_id = random_preflight_id()?;
        let capability_digest = capability_digest(&request, &target)?;
        self.inner
            .store
            .create_managed_work_preflight(inari_gateway::NewManagedWorkPreflight {
                preflight_id: preflight_id.clone(),
                request: request.clone(),
                dispatch_key_id: target.dispatch_key.key_id.clone(),
                capability_digest: capability_digest.clone(),
                work_expires_at: expires_at,
                idempotency_expires_at,
                submit_before,
                created_at: now,
            })
            .await?;

        Ok(ManagedWorkPreflightResult {
            contract_major: MANAGED_WORK_CONTRACT_MAJOR,
            preflight_id: Some(preflight_id),
            state: ManagedWorkPreflightState::Ready,
            operation: request.operation,
            media_type: request.operation.media_type().into(),
            device_id: request.device_id,
            capability_digest: Some(capability_digest),
            expires_at: Some(expires_at),
            idempotency_expires_at: Some(idempotency_expires_at),
            submit_before: Some(submit_before),
            error_code: None,
            message_key: "managed_work.ready".into(),
        })
    }

    pub async fn submit_managed_work(
        &self,
        managed_work_id: ManagedWorkId,
        idempotency_key: String,
        submission: ManagedWorkSubmission,
    ) -> AppResult<ManagedWorkReceipt> {
        self.ensure_enabled()?;
        let security = self
            .inner
            .security
            .clone()
            .ok_or_else(|| {
                AppError::service_unavailable("Managed Work dispatch is not enabled.")
            })?;
        validate_submission(&submission)?;
        if submission.work.scope.organization_id != self.inner.organization.id {
            return Err(AppError::forbidden(
                "Managed Work Organization does not match this Controller.",
            ));
        }
        let payload_fingerprint = decode_fingerprint(&submission.payload_fingerprint)?;
        let payload = decoded_document(&submission)?;
        let payload_bytes = payload.len();
        let request_fingerprint: [u8; 32] = Sha256::digest(serde_json_canonicalizer::to_vec(&(
            &submission.work,
            &submission.payload_fingerprint,
        ))?)
        .into();
        if let Some((persisted, command)) = self
            .inner
            .store
            .replay_managed_work(
                &managed_work_id,
                &submission.work.scope.organization_id,
                &idempotency_key,
                submission.work.print_intent_id.as_str(),
                &request_fingerprint,
            )
            .await?
        {
            self.publish_managed_dispatch(command)
                .await?;
            return Ok(ManagedWorkReceipt {
                managed_work_id: persisted.managed_work_id,
                state: persisted.state,
            });
        }
        let protected_aad =
            serde_json_canonicalizer::to_vec(&inari_gateway::ManagedPayloadContext {
                organization_id: submission
                    .work
                    .scope
                    .organization_id
                    .to_string(),
                managed_work_id: managed_work_id.clone(),
                idempotency_key: idempotency_key.clone(),
                payload_fingerprint: hex::encode(payload_fingerprint),
                request_fingerprint: hex::encode(request_fingerprint),
            })?;
        let prepared_payload = self
            .payload_protector()?
            .prepare(protected_aad)
            .await?;
        let dispatch_submission = submission.clone();
        let dispatch_idempotency_key = idempotency_key.clone();
        let (persisted, command) = self
            .inner
            .store
            .admit_managed_work(
                ManagedWorkAdmission {
                    managed_work_id: &managed_work_id,
                    idempotency_key: &idempotency_key,
                    submission: &submission,
                    request_fingerprint: &request_fingerprint,
                    payload_fingerprint: &payload_fingerprint,
                    payload_bytes: i64::try_from(payload_bytes).map_err(|_| {
                        AppError::bad_request("Managed Work payload size is out of range.")
                    })?,
                    admitted_at: Utc::now(),
                },
                move |allocation| {
                    if managed_work_fingerprint(&dispatch_submission.work, allocation.work_expires_at)? != payload_fingerprint {
                        return Err(inari_gateway::GatewayError::InvalidInput(
                            "Managed Work Payload Fingerprint does not match its normalized envelope and deadline".into(),
                        ));
                    }
                    let command = security
                        .dispatch_signer
                        .command(allocation, dispatch_idempotency_key, dispatch_submission)
                        .map_err(|error| {
                            inari_gateway::GatewayError::Unavailable(error.to_string())
                        })?;
                    let plaintext =
                        Zeroizing::new(serde_json_canonicalizer::to_vec(&command).map_err(
                            |error| inari_gateway::GatewayError::Unavailable(error.to_string()),
                        )?);
                    prepared_payload
                        .seal(&plaintext)
                        .map_err(|error| {
                            inari_gateway::GatewayError::Unavailable(error.to_string())
                        })
                },
            )
            .await?;
        self.publish_managed_dispatch(command)
            .await?;
        Ok(ManagedWorkReceipt {
            managed_work_id: persisted.managed_work_id,
            state: persisted.state,
        })
    }

    async fn publish_managed_dispatch(
        &self,
        command: Option<StoredControllerCommand>,
    ) -> AppResult<()> {
        if let Some(command) = command {
            match self
                .publish_live_command(&command)
                .await
            {
                Ok(()) => {
                    self.inner
                        .store
                        .mark_command_published(
                            command.agent_id.as_str(),
                            command.command_id.as_str(),
                        )
                        .await?;
                },
                Err(error) => {
                    tracing::debug!(
                        error = %error,
                        command_id = %command.command_id,
                        agent_id = %command.agent_id,
                        "durable Managed Work dispatch could not be published live"
                    );
                },
            }
        }
        Ok(())
    }

    pub async fn managed_work(
        &self,
        managed_work_id: &ManagedWorkId,
    ) -> AppResult<ManagedWorkRecord> {
        self.ensure_enabled()?;
        let persisted = self
            .inner
            .store
            .managed_work(managed_work_id)
            .await?;
        Ok(ManagedWorkRecord {
            managed_work_id: persisted.managed_work_id,
            print_intent_id: persisted.print_intent_id,
            scope: persisted.scope,
            device_id: persisted.device_id,
            operation: persisted.operation,
            media_type: persisted.operation.media_type().into(),
            state: persisted.state,
            print_job_id: persisted.print_job_id,
            print_job_observation: persisted.print_job_observation,
            error_code: persisted.error_code,
            message_key: persisted.message_key,
            admitted_at: persisted.admitted_at,
            updated_at: persisted.updated_at,
            expires_at: persisted.expires_at,
        })
    }
}

fn validate_preflight_request(request: &ManagedWorkPreflightRequest) -> AppResult<()> {
    if request.contract_major != MANAGED_WORK_CONTRACT_MAJOR {
        return Err(AppError::bad_request("Managed Work contract major is not supported."));
    }
    validate_text("database", &request.scope.database, 128)?;
    validate_text("company_id", &request.scope.company_id, 64)?;
    validate_binding(&request.binding)
}

fn validate_submission(submission: &ManagedWorkSubmission) -> AppResult<()> {
    if submission.contract_major != MANAGED_WORK_CONTRACT_MAJOR {
        return Err(AppError::bad_request("Managed Work contract major is not supported."));
    }
    if submission.work.contract_major != submission.contract_major {
        return Err(AppError::bad_request("Managed Work contract versions do not match."));
    }
    validate_text("database", &submission.work.scope.database, 128)?;
    validate_text("company_id", &submission.work.scope.company_id, 64)?;
    validate_binding(&submission.work.origin.binding)
}

fn validate_binding(binding: &inari_gateway::protocol::ReportBindingClaim) -> AppResult<()> {
    validate_text("report_binding_id", &binding.report_binding_id, 128)?;
    validate_text("binding_revision_id", &binding.binding_revision_id, 128)?;
    validate_text("report_action_id", &binding.report_action_id, 256)?;
    validate_text("report_contract_digest", &binding.report_contract_digest, 128)?;
    validate_text("template_digest", &binding.template_digest, 128)?;
    for (name, value) in [
        ("command_profile_id", binding.command_profile_id.as_deref()),
        ("layout_profile_id", binding.layout_profile_id.as_deref()),
        (
            "hardware_matrix_digest",
            binding
                .hardware_matrix_digest
                .as_deref(),
        ),
    ] {
        if let Some(value) = value {
            validate_text(name, value, 128)?;
        }
    }
    Ok(())
}

fn validate_text(name: &str, value: &str, max_len: usize) -> AppResult<()> {
    if value.is_empty() || value.len() > max_len || value.chars().any(char::is_control) {
        return Err(AppError::bad_request(format!(
            "Managed Work {name} must contain 1 to {max_len} non-control characters."
        )));
    }
    Ok(())
}

fn blocked_preflight(
    request: &ManagedWorkPreflightRequest,
    error_code: &str,
) -> ManagedWorkPreflightResult {
    ManagedWorkPreflightResult {
        contract_major: MANAGED_WORK_CONTRACT_MAJOR,
        preflight_id: None,
        state: ManagedWorkPreflightState::Blocked,
        operation: request.operation,
        media_type: request.operation.media_type().into(),
        device_id: request.device_id.clone(),
        capability_digest: None,
        expires_at: None,
        idempotency_expires_at: None,
        submit_before: None,
        error_code: Some(error_code.into()),
        message_key: format!("managed_work.{error_code}"),
    }
}

fn random_preflight_id() -> AppResult<ManagedPreflightId> {
    let mut random = [0_u8; 16];
    getrandom::fill(&mut random).map_err(|source| {
        AppError::internal("randomness_unavailable", "A Managed Work ID could not be created.")
            .with_source(source)
    })?;
    format!("mpf_{}", hex::encode(random))
        .parse()
        .map_err(Into::into)
}

fn duration_delta(duration: Duration) -> AppResult<TimeDelta> {
    TimeDelta::from_std(duration).map_err(|source| {
        AppError::internal("managed_work_duration", "A Managed Work deadline is invalid.")
            .with_source(source)
    })
}

fn capability_digest(
    request: &ManagedWorkPreflightRequest,
    target: &inari_gateway::ManagedWorkTargetRecord,
) -> AppResult<String> {
    let mut capabilities = target
        .capabilities
        .iter()
        .map(|capability| format!("{capability:?}"))
        .collect::<Vec<_>>();
    capabilities.sort_unstable();
    let facts = (
        MANAGED_WORK_CONTRACT_MAJOR,
        &target.organization_id,
        &target.site_id,
        &target.agent_id,
        &target.device_id,
        request.operation,
        capabilities,
        &target.dispatch_key,
    );
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(&facts)?)))
}

fn decode_fingerprint(value: &str) -> AppResult<[u8; 32]> {
    let decoded = hex::decode(value)
        .map_err(|_| AppError::bad_request("Managed Work payload fingerprint is not valid hex."))?;
    decoded.try_into().map_err(|_| {
        AppError::bad_request("Managed Work payload fingerprint must contain 32 bytes.")
    })
}

fn decoded_document(submission: &ManagedWorkSubmission) -> AppResult<Vec<u8>> {
    let content_base64 = match &submission.work.document {
        inari_gateway::protocol::ManagedDocument::ReportPdf { content_base64 }
        | inari_gateway::protocol::ManagedDocument::LabelDocument { content_base64 } => {
            content_base64
        },
    };
    let decoded = STANDARD
        .decode(content_base64)
        .map_err(|_| AppError::bad_request("Managed Work document is not valid base64."))?;
    let operation = submission.work.document.operation();
    let limit = match operation {
        ManagedDocumentOperation::ReportPdf => REPORT_PDF_LIMIT,
        ManagedDocumentOperation::LabelDocument => LABEL_DOCUMENT_LIMIT,
    };
    if decoded.is_empty() || decoded.len() > limit {
        return Err(AppError::bad_request(format!(
            "Managed Work {} payload exceeds its {limit}-byte limit.",
            operation.media_type()
        )));
    }
    Ok(decoded)
}

#[cfg(test)]
mod persistence_tests;

#[cfg(test)]
mod tests {
    use inari_gateway::protocol::{
        ManagedDeviceWork, ManagedDocument, ManagedPreflightId, ManagedWorkScope,
        ManagedWorkSubmission, ReportBindingClaim, ReportPrintOrigin, ReportRoute, ReportSource,
    };

    use super::{decoded_document, validate_submission};

    pub(super) fn submission() -> ManagedWorkSubmission {
        ManagedWorkSubmission {
            contract_major: 1,
            preflight_id: "mpf_test"
                .parse::<ManagedPreflightId>()
                .unwrap(),
            payload_fingerprint: "c".repeat(64),
            work: ManagedDeviceWork {
                contract_major: 1,
                scope: ManagedWorkScope {
                    database: "production".into(),
                    company_id: "7".into(),
                    organization_id: "org_example".parse().unwrap(),
                    site_id: "site_example".parse().unwrap(),
                    agent_id: "agt_example".parse().unwrap(),
                },
                print_intent_id: "pi_v1_test".parse().unwrap(),
                device_id: "dev_printer".parse().unwrap(),
                origin: ReportPrintOrigin {
                    binding: ReportBindingClaim {
                        report_binding_id: "binding-1".into(),
                        binding_revision_id: "revision-1".into(),
                        report_action_id: "sale.action_report_saleorder".into(),
                        report_contract_digest: "a".repeat(64),
                        template_digest: "b".repeat(64),
                        command_profile_id: None,
                        layout_profile_id: None,
                        hardware_matrix_digest: None,
                    },
                    route: ReportRoute::Manual,
                    source: ReportSource::Records {
                        model: "sale.order".into(),
                        ordered_ids: vec![42],
                    },
                    rendered_document_index: 0,
                    copy_ordinal: 1,
                },
                document: ManagedDocument::ReportPdf { content_base64: "JVBERi0xLjQ=".into() },
                normalized_device_options: serde_json::from_value(serde_json::json!({"dpi":300}))
                    .unwrap(),
            },
        }
    }

    #[test]
    fn submission_accepts_a_plain_document_with_controller_owned_deadlines() {
        let submission = submission();
        assert!(validate_submission(&submission).is_ok());
        assert_eq!(decoded_document(&submission).unwrap(), b"%PDF-1.4");
    }

    #[test]
    fn submission_rejects_invalid_document_base64() {
        let mut submission = submission();
        let ManagedDocument::ReportPdf { content_base64 } = &mut submission.work.document else {
            panic!("expected a PDF document");
        };
        *content_base64 = "%%%".into();
        assert!(decoded_document(&submission).is_err());
    }
}
