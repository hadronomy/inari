use gpui::{
    Div, IntoElement, ParentElement as _, RenderOnce, Styled, div, prelude::FluentBuilder as _,
    rems,
};
use gpui_component::StyledExt as _;
use inari_agent_client::{AgentConnection, Device, DeviceState, Job, JobState, ServiceState};

use crate::ui::{Message, MessageTone, MetricCard, PageHeader, page, palette};

#[derive(IntoElement)]
pub struct OverviewView {
    devices: Vec<Device>,
    jobs: Vec<Job>,
    connection: AgentConnection,
    service: ServiceState,
    agent_guidance: Option<String>,
}

impl OverviewView {
    pub fn new(
        devices: &[Device],
        jobs: &[Job],
        connection: AgentConnection,
        service: ServiceState,
        agent_guidance: Option<String>,
    ) -> Self {
        Self { devices: devices.to_vec(), jobs: jobs.to_vec(), connection, service, agent_guidance }
    }
}

impl RenderOnce for OverviewView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        let online = self
            .devices
            .iter()
            .filter(|device| device.state == DeviceState::Online)
            .count();
        let device_issues = self
            .devices
            .iter()
            .filter(|device| {
                matches!(
                    device.state,
                    DeviceState::Offline | DeviceState::Degraded | DeviceState::Blocked
                )
            })
            .count();
        let failed = self
            .jobs
            .iter()
            .filter(|job| job.state == JobState::Failed)
            .count();
        let active = self
            .jobs
            .iter()
            .filter(|job| matches!(job.state, JobState::Queued | JobState::Running))
            .count();
        let issue_count = device_issues + failed;
        let has_agent_guidance = self.agent_guidance.is_some();
        let (agent, agent_detail, agent_color) =
            service_summary(self.service, self.connection, colors);

        page()
            .child(PageHeader::new(
                "Overview",
                "Check this computer first. Then review devices and work that need attention.",
            ))
            .when_some(self.agent_guidance, |view, guidance| {
                view.child(Message::new(
                    "agent-connection-alert",
                    MessageTone::Danger,
                    "Agent connection unavailable",
                    guidance,
                ))
            })
            .when(
                !has_agent_guidance
                    && matches!(
                        self.service,
                        ServiceState::Stopped
                            | ServiceState::NotInstalled
                            | ServiceState::Unavailable
                    ),
                |view| {
                    let (_, detail, _) = service_summary(self.service, self.connection, colors);
                    view.child(Message::new(
                        "service-alert",
                        MessageTone::Warning,
                        "Agent service needs attention",
                        detail,
                    ))
                },
            )
            .child(
                div()
                    .v_flex()
                    .gap(rems(0.75))
                    .child(section_heading("Needs attention"))
                    .child(if has_agent_guidance {
                        Message::new(
                            "attention-summary",
                            MessageTone::Info,
                            "Device and job state unavailable",
                            "Restore the agent connection before you review device and job health.",
                        )
                        .into_any_element()
                    } else if issue_count == 0 {
                        Message::new(
                            "attention-summary",
                            MessageTone::Success,
                            "No device or job issues",
                            "Device Center has not found a failed job or a device issue.",
                        )
                        .into_any_element()
                    } else {
                        Message::new(
                            "attention-summary",
                            MessageTone::Warning,
                            format!("{issue_count} items need review"),
                            format!("{device_issues} device issues. {failed} failed jobs."),
                        )
                        .into_any_element()
                    }),
            )
            .child(
                div()
                    .v_flex()
                    .gap(rems(0.75))
                    .child(section_heading("Current state"))
                    .child(
                        div()
                            .flex()
                            .flex_wrap()
                            .gap(rems(0.75))
                            .child(responsive_metric(MetricCard::new(
                                "Agent service",
                                agent,
                                agent_detail,
                                agent_color,
                            )))
                            .child(responsive_metric(MetricCard::new(
                                "Devices online",
                                online.to_string(),
                                format!("{} found", self.devices.len()),
                                if device_issues == 0 { colors.success } else { colors.warning },
                            )))
                            .child(responsive_metric(MetricCard::new(
                                "Active work",
                                active.to_string(),
                                "Queued or running",
                                if active == 0 { colors.text_muted } else { colors.info },
                            ))),
                    ),
            )
    }
}

fn service_summary(
    service: ServiceState,
    connection: AgentConnection,
    colors: palette::Palette,
) -> (&'static str, &'static str, gpui::Hsla) {
    match service {
        ServiceState::Checking => ("Checking", "Reading the service state", colors.info),
        ServiceState::Starting => ("Starting", "Waiting for the service", colors.info),
        ServiceState::Running if connection == AgentConnection::Connected => {
            ("Running", "Local updates are active", colors.success)
        },
        ServiceState::Running => ("Running", "Restoring the local connection", colors.warning),
        ServiceState::Stopped => ("Stopped", "Open Support to start the service", colors.danger),
        ServiceState::NotInstalled => {
            ("Not installed", "Repair the Inari installation", colors.danger)
        },
        ServiceState::Unavailable => {
            ("Unavailable", "Open Support and check the service", colors.danger)
        },
    }
}

fn responsive_metric(card: impl IntoElement) -> Div {
    div()
        .min_w(rems(13.))
        .flex_1()
        .child(card)
}

fn section_heading(title: &'static str) -> Div {
    div()
        .text_size(rems(1.))
        .font_weight(gpui::FontWeight::SEMIBOLD)
        .child(title)
}
