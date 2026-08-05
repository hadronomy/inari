use gpui::{
    IntoElement, ParentElement as _, RenderOnce, Styled, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{
    IconName, StyledExt as _,
    button::{Button, ButtonVariants as _},
};
use inari_agent_client::{AgentClientOptions, ServiceState};

use crate::{
    app::{
        OpenApiReference, OpenLogs, RefreshAgentService, RestartAgentService, StartAgentService,
    },
    ui::{Message, MessageTone, PageHeader, page, palette},
};

#[derive(IntoElement)]
pub struct SupportView {
    service: ServiceState,
    service_error: Option<String>,
    agent_error: Option<String>,
}

impl SupportView {
    pub fn new(
        service: ServiceState,
        service_error: Option<String>,
        agent_error: Option<String>,
    ) -> Self {
        Self { service, service_error, agent_error }
    }
}

impl RenderOnce for SupportView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        let needs_restart = self.agent_error.is_some();
        let (title, detail, tone) = if self.service == ServiceState::Running && needs_restart {
            (
                "The agent needs attention",
                "Restart the agent service, then try again.",
                MessageTone::Danger,
            )
        } else {
            service_copy(self.service)
        };
        let agent_endpoint = AgentClientOptions::default()
            .endpoint
            .to_string();
        let diagnostic = break_diagnostic(
            self.agent_error
                .or(self.service_error)
                .unwrap_or_else(|| "No error details are available.".into()),
        );

        page()
            .child(PageHeader::new(
                "Support",
                "Check the agent service. Use technical details when an administrator asks for them.",
            ))
            .child(
                div()
                    .v_flex()
                    .gap(rems(0.75))
                    .child(section_heading("Service health"))
                    .child(Message::new("service-health", tone, title, detail))
                    .when(self.service == ServiceState::Stopped, |section| {
                        section.child(recovery_button(
                            "start-agent-service",
                            "Start agent service",
                            IconName::ArrowRight,
                            StartAgentService,
                        ))
                    })
                    .when(self.service == ServiceState::Running && needs_restart, |section| {
                        section.child(recovery_button(
                            "restart-agent-service",
                            "Restart agent service",
                            IconName::Redo2,
                            RestartAgentService,
                        ))
                    })
                    .when(self.service == ServiceState::Unavailable, |section| {
                        section.child(recovery_button(
                            "refresh-agent-service",
                            "Check service again",
                            IconName::Redo2,
                            RefreshAgentService,
                        ))
                    }),
            )
            .child(
                div()
                    .v_flex()
                    .gap(rems(0.75))
                    .child(section_heading("Technical details"))
                    .child(
                        div()
                            .v_flex()
                            .gap(rems(0.75))
                            .p(rems(1.))
                            .rounded(rems(0.5))
                            .bg(colors.surface)
                            .child(detail_row(
                                "Device Center version",
                                env!("CARGO_PKG_VERSION"),
                                colors,
                            ))
                            .child(detail_row("Agent API", agent_endpoint, colors))
                            .child(detail_row("Latest error", diagnostic, colors)),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_wrap()
                            .items_center()
                            .gap(rems(0.75))
                            .child(quiet_button(
                                "open-logs",
                                "Open local logs",
                                IconName::FolderOpen,
                                OpenLogs,
                            ))
                            .child(quiet_button(
                                "open-api-reference",
                                "Open API reference",
                                IconName::ExternalLink,
                                OpenApiReference,
                            )),
                    ),
            )
    }
}

fn service_copy(state: ServiceState) -> (&'static str, &'static str, MessageTone) {
    match state {
        ServiceState::Checking => (
            "Checking the agent service",
            "Device Center is reading the service state.",
            MessageTone::Info,
        ),
        ServiceState::Starting => (
            "Starting the agent service",
            "Wait for the service request to finish.",
            MessageTone::Info,
        ),
        ServiceState::Running => (
            "Agent service is running",
            "Restart the service only when the agent response is invalid.",
            MessageTone::Success,
        ),
        ServiceState::Stopped => (
            "Agent service is stopped",
            "Start the service to restore device operations.",
            MessageTone::Warning,
        ),
        ServiceState::NotInstalled => (
            "Agent service is not installed",
            "Repair the Inari installation to restore the service.",
            MessageTone::Danger,
        ),
        ServiceState::Unavailable => (
            "Service status is unavailable",
            "Check the service again. If the problem continues, open the local logs.",
            MessageTone::Danger,
        ),
    }
}

fn detail_row(
    label: &'static str,
    value: impl Into<gpui::SharedString>,
    colors: palette::Palette,
) -> impl IntoElement {
    div()
        .v_flex()
        .gap(rems(0.25))
        .child(
            div()
                .text_size(rems(0.75))
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .text_color(colors.text_muted)
                .child(label),
        )
        .child(
            div()
                .text_size(rems(0.8125))
                .line_height(rems(1.125))
                .child(value.into()),
        )
}

fn break_diagnostic(message: String) -> String {
    let mut wrapped = String::with_capacity(message.len());
    for character in message.chars() {
        wrapped.push(character);
        if matches!(character, '/' | ':' | '?' | '&' | '=' | ')' | ']') {
            wrapped.push('\u{200b}');
        }
    }
    wrapped
}

fn section_heading(title: &'static str) -> impl IntoElement {
    div()
        .font_weight(gpui::FontWeight::SEMIBOLD)
        .child(title)
}

fn recovery_button(
    id: &'static str,
    label: &'static str,
    icon: IconName,
    action: impl gpui::Action,
) -> impl IntoElement {
    let action = Box::new(action);
    Button::new(id)
        .primary()
        .h(rems(2.))
        .icon(icon)
        .label(label)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}

fn quiet_button(
    id: &'static str,
    label: &'static str,
    icon: IconName,
    action: impl gpui::Action,
) -> impl IntoElement {
    let action = Box::new(action);
    Button::new(id)
        .ghost()
        .h(rems(2.))
        .icon(icon)
        .label(label)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}
