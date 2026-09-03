use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{TimeDelta, Utc};
use inari_gateway::protocol::{
    DeviceCapability, DeviceState, MANAGED_WORK_CONTRACT_MAJOR, ManagedDocumentOperation,
    ManagedPreflightId, ManagedWorkId, ManagedWorkPreflightRequest, ManagedWorkPreflightResult,
    ManagedWorkPreflightState, ManagedWorkReceipt, ManagedWorkRecord, ManagedWorkSubmission,
};
use sha2::{Digest, Sha256};

use super::ManagedGatewayController;
use crate::error::{AppError, AppResult};

const PREFLIGHT_TTL: Duration = Duration::from_secs(2 * 60);
const WORK_TTL: Duration = Duration::from_secs(5 * 60);
const IDEMPOTENCY_TTL: Duration = Duration::from_secs(90 * 24 * 60 * 60);
const REPORT_PDF_LIMIT: usize = 10 * 1024 * 1024;
const LABEL_DOCUMENT_LIMIT: usize = 2 * 1024 * 1024;
const HPKE_AEAD_TAG_BYTES: usize = 16;

impl ManagedGatewayController {
    pub async fn preflight_managed_work(
        &self,
        request: ManagedWorkPreflightRequest,
    ) -> AppResult<ManagedWorkPreflightResult> {
        self.ensure_enabled()?;
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

        let now = Utc::now();
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
            dispatch_key: Some(target.dispatch_key),
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
        submission: ManagedWorkSubmission,
    ) -> AppResult<ManagedWorkReceipt> {
        self.ensure_enabled()?;
        validate_submission(&submission)?;
        if submission.scope.organization_id != self.inner.organization.id {
            return Err(AppError::forbidden(
                "Managed Work Organization does not match this Controller.",
            ));
        }
        let payload_fingerprint = decode_fingerprint(&submission.payload_fingerprint)?;
        let payload_bytes = decoded_ciphertext_size(&submission)?;
        let request_fingerprint: [u8; 32] = Sha256::digest(serde_json::to_vec(&submission)?).into();
        let persisted = self
            .inner
            .store
            .admit_managed_work(
                &managed_work_id,
                &submission,
                &request_fingerprint,
                &payload_fingerprint,
                i64::try_from(payload_bytes).map_err(|_| {
                    AppError::bad_request("Managed Work payload size is out of range.")
                })?,
                Utc::now(),
            )
            .await?;
        Ok(ManagedWorkReceipt {
            managed_work_id: persisted.managed_work_id,
            state: persisted.state,
        })
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
    if submission.expires_at <= Utc::now() {
        return Err(AppError::conflict("Managed Work has expired."));
    }
    if submission.idempotency_expires_at <= submission.expires_at {
        return Err(AppError::bad_request(
            "Managed Work idempotency expiry must follow the work expiry.",
        ));
    }
    validate_text("database", &submission.scope.database, 128)?;
    validate_text("company_id", &submission.scope.company_id, 64)?;
    validate_binding(&submission.binding)?;
    validate_text("dispatch key_id", &submission.sealed_document.key_id, 128)?;
    let encapsulated_key = URL_SAFE_NO_PAD
        .decode(
            &submission
                .sealed_document
                .encapsulated_key_base64url,
        )
        .map_err(|_| AppError::bad_request("HPKE encapsulated key is not valid base64url."))?;
    if encapsulated_key.len() != 32 {
        return Err(AppError::bad_request("HPKE encapsulated key must contain 32 bytes."));
    }
    Ok(())
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
        dispatch_key: None,
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

fn decoded_ciphertext_size(submission: &ManagedWorkSubmission) -> AppResult<usize> {
    let decoded = URL_SAFE_NO_PAD
        .decode(
            &submission
                .sealed_document
                .ciphertext_base64url,
        )
        .map_err(|_| AppError::bad_request("Managed Work ciphertext is not valid base64url."))?;
    let limit = match submission.operation {
        ManagedDocumentOperation::ReportPdf => REPORT_PDF_LIMIT,
        ManagedDocumentOperation::LabelDocument => LABEL_DOCUMENT_LIMIT,
    };
    if decoded.len() <= HPKE_AEAD_TAG_BYTES || decoded.len() > limit + HPKE_AEAD_TAG_BYTES {
        return Err(AppError::bad_request(format!(
            "Managed Work {} payload exceeds its {limit}-byte limit.",
            submission.operation.media_type()
        )));
    }
    Ok(decoded.len())
}

#[cfg(test)]
mod tests {
    use inari_gateway::protocol::{
        DispatchHpkeSuite, ManagedDocumentOperation, ManagedPreflightId, ManagedWorkScope,
        PrintIntentId, ReportBindingClaim, SealedManagedDocument,
    };

    use super::{decoded_ciphertext_size, validate_submission};

    fn submission() -> inari_gateway::protocol::ManagedWorkSubmission {
        let now = chrono::Utc::now();
        inari_gateway::protocol::ManagedWorkSubmission {
            contract_major: 1,
            preflight_id: "mpf_test"
                .parse::<ManagedPreflightId>()
                .unwrap(),
            scope: ManagedWorkScope {
                database: "production".into(),
                company_id: "7".into(),
                organization_id: "org_example".parse().unwrap(),
                site_id: "site_example".parse().unwrap(),
                agent_id: "agt_example".parse().unwrap(),
            },
            print_intent_id: "pi_v1_test"
                .parse::<PrintIntentId>()
                .unwrap(),
            device_id: "dev_printer".parse().unwrap(),
            operation: ManagedDocumentOperation::ReportPdf,
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
            payload_fingerprint: "c".repeat(64),
            sealed_document: SealedManagedDocument {
                key_id: "dispatch-key-1".into(),
                suite: DispatchHpkeSuite::DhkemX25519HkdfSha256HkdfSha256Aes256Gcm,
                encapsulated_key_base64url: base64::Engine::encode(
                    &base64::engine::general_purpose::URL_SAFE_NO_PAD,
                    [1_u8; 32],
                ),
                ciphertext_base64url: base64::Engine::encode(
                    &base64::engine::general_purpose::URL_SAFE_NO_PAD,
                    [1_u8; 17],
                ),
            },
            expires_at: now + chrono::TimeDelta::minutes(5),
            idempotency_expires_at: now + chrono::TimeDelta::days(90),
        }
    }

    #[test]
    fn submission_accepts_fixed_hpke_shape() {
        let submission = submission();
        assert!(validate_submission(&submission).is_ok());
        assert_eq!(decoded_ciphertext_size(&submission).unwrap(), 17);
    }

    #[test]
    fn submission_rejects_wrong_encapsulated_key_length() {
        let mut submission = submission();
        submission
            .sealed_document
            .encapsulated_key_base64url = "AQ".into();
        assert!(validate_submission(&submission).is_err());
    }
}
