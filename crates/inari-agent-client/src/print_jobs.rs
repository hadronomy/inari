use chrono::{DateTime, Utc};
use serde::Deserialize;

use crate::{AgentClientError, DeviceId, JobId};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrintOriginKind {
    Pos,
    Preparation,
    Report,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputEvidence {
    Device,
    Spooler,
    Transport,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PrintJobState {
    Accepted,
    InProgress,
    OutputConfirmed(OutputEvidence),
    Failed,
    OutcomeUnknown,
    Expired,
    Canceled,
}

/// A content-free snapshot from the authenticated native monitor.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PrintJob {
    pub id: JobId,
    pub intent_id: String,
    pub device_id: DeviceId,
    pub origin_kind: PrintOriginKind,
    pub database: String,
    pub document_kind: Option<String>,
    pub pos_configuration_id: Option<String>,
    pub state: PrintJobState,
    pub state_version: u64,
    pub occurred_at: DateTime<Utc>,
}

#[derive(Deserialize)]
pub(crate) struct WirePrintJob {
    print_job_id: String,
    print_intent_id: String,
    device_id: String,
    origin_kind: PrintOriginKind,
    database: String,
    document_kind: Option<String>,
    pos_configuration_id: Option<String>,
    state: WirePrintJobState,
    state_version: u64,
    accepted_at: DateTime<Utc>,
    started_at: Option<DateTime<Utc>>,
    terminal_at: Option<DateTime<Utc>>,
    confirmation_evidence: Option<OutputEvidence>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum WirePrintJobState {
    Accepted,
    InProgress,
    OutputConfirmed,
    Failed,
    OutcomeUnknown,
    Expired,
    Canceled,
}

impl TryFrom<WirePrintJob> for PrintJob {
    type Error = AgentClientError;

    fn try_from(job: WirePrintJob) -> Result<Self, Self::Error> {
        let malformed = || {
            AgentClientError::invalid_response(std::io::Error::other(
                "The native monitor returned an invalid Print Job snapshot.",
            ))
        };
        if job.state_version == 0
            || !valid_identifier(&job.print_intent_id)
            || !valid_identifier(&job.database)
            || job
                .document_kind
                .as_ref()
                .is_some_and(|kind| kind.is_empty() || kind.chars().count() > 64)
            || job
                .pos_configuration_id
                .as_ref()
                .is_some_and(|id| !valid_identifier(id))
            || job
                .started_at
                .is_some_and(|at| at < job.accepted_at)
            || job.terminal_at.is_some_and(|at| {
                at < job
                    .started_at
                    .unwrap_or(job.accepted_at)
            })
            || (job.origin_kind == PrintOriginKind::Report) != job.pos_configuration_id.is_none()
            || (job.origin_kind == PrintOriginKind::Report) != job.document_kind.is_none()
        {
            return Err(malformed());
        }
        let state = match (job.state, job.started_at, job.terminal_at, job.confirmation_evidence) {
            (WirePrintJobState::Accepted, None, None, None) => PrintJobState::Accepted,
            (WirePrintJobState::InProgress, Some(_), None, None) => PrintJobState::InProgress,
            (WirePrintJobState::OutputConfirmed, Some(_), Some(_), Some(evidence)) => {
                PrintJobState::OutputConfirmed(evidence)
            },
            (WirePrintJobState::Failed, _, Some(_), None) => PrintJobState::Failed,
            (WirePrintJobState::OutcomeUnknown, Some(_), Some(_), None) => {
                PrintJobState::OutcomeUnknown
            },
            (WirePrintJobState::Expired, None, Some(_), None) => PrintJobState::Expired,
            (WirePrintJobState::Canceled, None, Some(_), None) => PrintJobState::Canceled,
            _ => return Err(malformed()),
        };
        Ok(Self {
            id: JobId::parse(job.print_job_id).map_err(AgentClientError::invalid_response)?,
            intent_id: job.print_intent_id,
            device_id: DeviceId::parse(job.device_id)
                .map_err(AgentClientError::invalid_response)?,
            origin_kind: job.origin_kind,
            database: job.database,
            document_kind: job.document_kind,
            pos_configuration_id: job.pos_configuration_id,
            state,
            state_version: job.state_version,
            occurred_at: job
                .terminal_at
                .or(job.started_at)
                .unwrap_or(job.accepted_at),
        })
    }
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'/' | b'-')
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../contracts/local-agent.print-job.json")).unwrap()
    }

    #[test]
    fn receipt_snapshot_preserves_identity_evidence_and_terminal_time() {
        let wire: WirePrintJob = serde_json::from_value(fixture()).unwrap();
        let job = PrintJob::try_from(wire).unwrap();
        assert_eq!(job.id.as_str(), "job_receipt");
        assert_eq!(job.intent_id, "pi_v1_receipt");
        assert_eq!(job.pos_configuration_id.as_deref(), Some("4"));
        assert_eq!(job.state, PrintJobState::OutputConfirmed(OutputEvidence::Spooler));
        assert_eq!(job.state_version, 3);
        assert_eq!(job.occurred_at.to_rfc3339(), "2026-10-08T01:03:07+00:00");
    }

    #[test]
    fn incomplete_or_inconsistent_evidence_never_means_output_confirmed() {
        for (field, replacement) in [
            ("confirmation_evidence", serde_json::Value::Null),
            ("started_at", serde_json::Value::Null),
            ("terminal_at", serde_json::Value::Null),
            ("state_version", serde_json::json!(0)),
            ("started_at", serde_json::json!("2026-10-08T00:00:00Z")),
            ("terminal_at", serde_json::json!("2026-10-08T01:03:05Z")),
            ("pos_configuration_id", serde_json::Value::Null),
            ("print_intent_id", serde_json::json!("bad\nintent")),
        ] {
            let mut value = fixture();
            value[field] = replacement;
            let wire: WirePrintJob = serde_json::from_value(value).unwrap();
            assert!(PrintJob::try_from(wire).is_err(), "{field}");
        }
    }

    #[test]
    fn outcome_unknown_is_preserved_without_completion_evidence() {
        let mut value = fixture();
        value["state"] = serde_json::json!("outcome_unknown");
        value["confirmation_evidence"] = serde_json::Value::Null;
        let wire: WirePrintJob = serde_json::from_value(value).unwrap();
        assert_eq!(PrintJob::try_from(wire).unwrap().state, PrintJobState::OutcomeUnknown);
    }

    #[test]
    fn document_kind_preserves_the_submission_contracts_text() {
        let mut value = fixture();
        value["document_kind"] = serde_json::json!("Recibo de MIZONA");
        let wire: WirePrintJob = serde_json::from_value(value).unwrap();
        assert_eq!(
            PrintJob::try_from(wire)
                .unwrap()
                .document_kind
                .as_deref(),
            Some("Recibo de MIZONA")
        );
    }
}
