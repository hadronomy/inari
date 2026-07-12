use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::{AgentId, JobId, ProtocolVersion, StructuredFields};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ControllerCommand {
    #[serde(rename = "controller.command.submit_print_job")]
    SubmitPrintJob {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        payload: SubmitPrintJob,
    },
    #[serde(rename = "controller.command.execute_device_command")]
    ExecuteDeviceCommand {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        payload: ExecuteDeviceCommand,
    },
    #[serde(rename = "controller.command.cancel_job")]
    CancelJob {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        job_id: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmitPrintJob {
    pub content: PrintContent,
    #[serde(default)]
    pub target: CommandTarget,
    #[serde(default)]
    pub options: PrintOptions,
    #[serde(default)]
    pub metadata: StructuredFields,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PrintContent {
    StructuredReceipt { data: StructuredFields, document_name: String },
    ReceiptImage { binary: BinaryContent, document_name: String },
    Text { text: String, document_name: String },
    Html { html: String, document_name: String },
    Pdf { binary: BinaryContent, document_name: String },
    Raw { binary: BinaryContent, data_type: String, document_name: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BinaryContent {
    pub base64: String,
    pub declared_mime_type: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CommandTarget {
    pub device_id: Option<String>,
    pub printer_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrintOptions {
    pub transport: String,
    pub open_cash_drawer: bool,
}

impl Default for PrintOptions {
    fn default() -> Self {
        Self { transport: "auto".into(), open_cash_drawer: false }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecuteDeviceCommand {
    #[serde(default)]
    pub target: CommandTarget,
    pub command: DeviceCommand,
    #[serde(default)]
    pub metadata: StructuredFields,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DeviceCommand {
    OpenCashDrawer,
    PrintTestPage { transport: String },
    FeedLines { count: u8 },
    FeedDots { count: u8 },
    CutPaper { mode: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRequest {
    #[serde(flatten)]
    pub command: JobKind,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum JobKind {
    #[serde(rename = "controller.command.submit_print_job")]
    SubmitPrintJob { payload: SubmitPrintJob },
    #[serde(rename = "controller.command.execute_device_command")]
    ExecuteDeviceCommand { payload: ExecuteDeviceCommand },
    #[serde(rename = "controller.command.cancel_job")]
    CancelJob { job_id: String },
}

impl JobKind {
    #[must_use]
    pub const fn required_action(&self) -> &'static str {
        match self {
            Self::SubmitPrintJob { .. } => "jobs:create",
            Self::ExecuteDeviceCommand { .. } => "commands:execute",
            Self::CancelJob { .. } => "jobs:cancel",
        }
    }

    #[must_use]
    pub fn into_message(
        self,
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
    ) -> ControllerCommand {
        match self {
            Self::SubmitPrintJob { payload } => ControllerCommand::SubmitPrintJob {
                message_id,
                command_id,
                sequence,
                issued_at,
                payload,
            },
            Self::ExecuteDeviceCommand { payload } => ControllerCommand::ExecuteDeviceCommand {
                message_id,
                command_id,
                sequence,
                issued_at,
                payload,
            },
            Self::CancelJob { job_id } => {
                ControllerCommand::CancelJob { message_id, command_id, sequence, issued_at, job_id }
            },
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Published,
    Accepted,
    Rejected,
    Completed,
    Failed,
    Superseded,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobReceipt {
    pub job_id: JobId,
    pub state: JobState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandHistory {
    pub selected_protocol_version: ProtocolVersion,
    pub commands: Vec<ControllerCommand>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRecord {
    pub job_id: JobId,
    pub agent_id: AgentId,
    pub state: JobState,
    pub issued_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobList {
    pub jobs: Vec<JobRecord>,
}
