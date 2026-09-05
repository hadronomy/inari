use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::{AgentId, DispatchDeviceWork, JobId, ProtocolVersion, StructuredFields};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", deny_unknown_fields)]
pub enum ControllerCommand {
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
    #[serde(rename = "controller.command.dispatch_device_work")]
    DispatchDeviceWork {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        payload: Box<DispatchDeviceWork>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandTarget {
    pub device_id: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecuteDeviceCommand {
    pub target: CommandTarget,
    pub command: DeviceCommand,
    #[serde(default)]
    pub metadata: StructuredFields,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum DeviceCommand {
    OpenCashDrawer,
    PrintTestPage {},
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
#[serde(tag = "type", deny_unknown_fields)]
pub enum JobKind {
    #[serde(rename = "controller.command.execute_device_command")]
    ExecuteDeviceCommand { payload: ExecuteDeviceCommand },
    #[serde(rename = "controller.command.cancel_job")]
    CancelJob { job_id: String },
}

impl JobKind {
    #[must_use]
    pub const fn required_action(&self) -> &'static str {
        match self {
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{DeviceCommand, JobKind, JobRequest};

    #[test]
    fn device_command_requires_a_stable_device_id() {
        let request: JobRequest = serde_json::from_value(json!({
            "type": "controller.command.execute_device_command",
            "payload": {
                "target": {"device_id": "dev_receipt"},
                "command": {"kind": "print_test_page"},
                "metadata": {}
            }
        }))
        .unwrap();

        let JobKind::ExecuteDeviceCommand { payload } = request.command else {
            panic!("expected a device command");
        };
        assert_eq!(payload.target.device_id, "dev_receipt");
        assert_eq!(payload.command, DeviceCommand::PrintTestPage {});
    }

    #[test]
    fn obsolete_print_submission_is_not_part_of_the_command_union() {
        let result = serde_json::from_value::<JobRequest>(json!({
            "type": "controller.command.submit_print_job",
            "payload": {
                "content": {"kind": "text", "text": "obsolete"},
                "target": {"device_id": "dev_receipt"}
            }
        }));

        assert!(result.is_err());
    }

    #[test]
    fn command_target_rejects_printer_names_and_transport_selection() {
        let printer_name = serde_json::from_value::<JobRequest>(json!({
            "type": "controller.command.execute_device_command",
            "payload": {
                "target": {"device_id": "dev_receipt", "printer_name": "Receipt"},
                "command": {"kind": "print_test_page"},
                "metadata": {}
            }
        }));
        let transport = serde_json::from_value::<JobRequest>(json!({
            "type": "controller.command.execute_device_command",
            "payload": {
                "target": {"device_id": "dev_receipt"},
                "command": {"kind": "print_test_page", "transport": "raw"},
                "metadata": {}
            }
        }));

        assert!(printer_name.is_err());
        assert!(transport.is_err());
    }
}
