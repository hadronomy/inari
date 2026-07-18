use gpui::{Div, IntoElement, ParentElement as _, RenderOnce, Styled, div, rems};
use gpui_component::StyledExt as _;
use inari_agent_client::{AgentConnection, Device, DeviceState, Job, JobState, ServiceState};

use crate::ui::{MetricCard, PageHeader, SectionCard, palette};

#[derive(IntoElement)]
pub struct OverviewView {
    devices: Vec<Device>,
    jobs: Vec<Job>,
    connection: AgentConnection,
    service: ServiceState,
}

impl OverviewView {
    pub fn new(
        devices: &[Device],
        jobs: &[Job],
        connection: AgentConnection,
        service: ServiceState,
    ) -> Self {
        Self { devices: devices.to_vec(), jobs: jobs.to_vec(), connection, service }
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
        let attention = self
            .jobs
            .iter()
            .filter(|job| job.state == JobState::Failed)
            .count();
        let queued = self
            .jobs
            .iter()
            .filter(|job| matches!(job.state, JobState::Queued | JobState::Running))
            .count();
        let (agent, agent_detail, agent_color) =
            service_summary(self.service, self.connection, colors);

        page()
            .child(PageHeader::new(
                "Overview",
                "A clear view of this computer and the devices connected through it.",
            ))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(rems(0.85))
                    .child(responsive_card(MetricCard::new(
                        "Agent service",
                        agent,
                        agent_detail,
                        agent_color,
                    )))
                    .child(responsive_card(MetricCard::new(
                        "Devices online",
                        online.to_string(),
                        format!("{} discovered", self.devices.len()),
                        colors.green,
                    )))
                    .child(responsive_card(MetricCard::new(
                        "Work in progress",
                        queued.to_string(),
                        "Queued or currently running",
                        if queued == 0 { colors.green } else { colors.blue },
                    ))),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap(rems(0.85))
                    .child(responsive_card(SectionCard::new(
                        "What needs attention",
                        if attention == 0 {
                            "Nothing needs your attention."
                        } else {
                            "One or more jobs need review."
                        },
                        "Failures and recovery steps appear here, without hiding healthy work.",
                    )))
                    .child(responsive_card(SectionCard::new(
                        "Recent activity",
                        if self.jobs.is_empty() {
                            "No device work yet."
                        } else {
                            "Recent jobs are available in Activity."
                        },
                        "Completed work remains available after restarts.",
                    ))),
            )
    }
}

fn service_summary(
    service: ServiceState,
    connection: AgentConnection,
    colors: palette::Palette,
) -> (&'static str, &'static str, gpui::Hsla) {
    match service {
        ServiceState::Checking => {
            ("Checking", "Reading the operating-system service state", colors.blue)
        },
        ServiceState::Starting => {
            ("Starting", "Waiting for the agent service to become ready", colors.blue)
        },
        ServiceState::Running if connection == AgentConnection::Connected => {
            ("Running", "Live local updates are active", colors.green)
        },
        ServiceState::Running => ("Running", "The local connection is being restored", colors.blue),
        ServiceState::Stopped => {
            ("Stopped", "Start the agent service to reconnect", colors.vermilion)
        },
        ServiceState::NotInstalled => (
            "Not installed",
            "Repair the Inari installation to restore the service",
            colors.vermilion,
        ),
        ServiceState::Unavailable => {
            ("Unavailable", "Service state could not be read on this computer", colors.vermilion)
        },
    }
}

fn responsive_card(card: impl IntoElement) -> Div {
    div()
        .min_w(rems(15.))
        .flex_1()
        .child(card)
}

fn page() -> Div {
    div()
        .v_flex()
        .gap(rems(1.35))
        .max_w(rems(78.))
        .mx_auto()
        .px(rems(2.5))
        .py(rems(2.25))
}
