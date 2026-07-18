use gpui::{
    IntoElement, ParentElement as _, RenderOnce, Styled, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{
    StyledExt as _,
    button::{Button, ButtonVariants as _},
};
use inari_agent_client::ServiceState;

use crate::{
    app::{
        OpenApiReference, OpenLogs, RefreshAgentService, RestartAgentService, StartAgentService,
    },
    ui::{PageHeader, SectionCard},
};

#[derive(IntoElement)]
pub struct SupportView {
    service: ServiceState,
    service_error: Option<String>,
}

impl SupportView {
    pub fn new(service: ServiceState, service_error: Option<String>) -> Self {
        Self { service, service_error }
    }
}

impl RenderOnce for SupportView {
    fn render(self, _: &mut gpui::Window, _: &mut gpui::App) -> impl IntoElement {
        let (service_summary, service_detail) = service_copy(self.service, self.service_error);
        div()
            .v_flex()
            .gap(rems(1.35))
            .max_w(rems(78.))
            .mx_auto()
            .px(rems(2.5))
            .py(rems(2.25))
            .child(PageHeader::new(
                "Support",
                "Diagnostics and recovery without exposing protected credentials.",
            ))
            .child(SectionCard::new(
                "Inari Device Center",
                env!("CARGO_PKG_VERSION"),
                "Include this version when asking an administrator for help.",
            ))
            .child(SectionCard::new("Agent service", service_summary, service_detail))
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .items_center()
                    .gap(rems(0.65))
                    .when(self.service == ServiceState::Stopped, |actions| {
                        actions.child(action_button(
                            "start-agent-service",
                            "Start agent service",
                            StartAgentService,
                        ))
                    })
                    .when(self.service == ServiceState::Running, |actions| {
                        actions.child(action_button(
                            "restart-agent-service",
                            "Restart agent service",
                            RestartAgentService,
                        ))
                    })
                    .when(self.service == ServiceState::Unavailable, |actions| {
                        actions.child(action_button(
                            "refresh-agent-service",
                            "Check service again",
                            RefreshAgentService,
                        ))
                    })
                    .child(action_button("open-logs", "Open local logs", OpenLogs))
                    .child(action_button(
                        "open-api-reference",
                        "Open local API reference",
                        OpenApiReference,
                    )),
            )
    }
}

fn service_copy(state: ServiceState, error: Option<String>) -> (&'static str, String) {
    let (summary, guidance) = match state {
        ServiceState::Checking => {
            ("Checking", "Device Center is reading the system service state.")
        },
        ServiceState::Starting => ("Starting", "The request is still in progress."),
        ServiceState::Running => {
            ("Running", "The background agent remains active when Device Center closes.")
        },
        ServiceState::Stopped => {
            ("Stopped", "Start the service to reconnect devices and resume work.")
        },
        ServiceState::NotInstalled => {
            ("Not installed", "Repair the Inari installation to restore the background agent.")
        },
        ServiceState::Unavailable => (
            "Status unavailable",
            "Device Center could not read the operating-system service state.",
        ),
    };
    (summary, error.unwrap_or_else(|| guidance.into()))
}

fn action_button(
    id: &'static str,
    label: &'static str,
    action: impl gpui::Action,
) -> impl IntoElement {
    let action = Box::new(action);
    Button::new(id)
        .ghost()
        .label(label)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}
