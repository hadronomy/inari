//! Support: recover the agent, then hand an administrator the facts.
//!
//! The screen answers two questions in the order they get asked. *Can I fix
//! this myself?* — one recovery control, the one that matches the current
//! state, and only that one. A row of Start / Restart / Check buttons where
//! two are always wrong makes an operator guess during the exact moment they
//! are least able to. Then: *what do I tell the person who can?* — a readout
//! where every fact is one press from the clipboard, so the answer arrives in
//! the ticket as text rather than as a photograph of a window.

use gpui::{
    IntoElement, ParentElement as _, RenderOnce, SharedString, Styled, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::{Disableable as _, IconName, StyledExt as _, switch::Switch};
use inari_agent_client::{AgentClientOptions, ServiceState};

use crate::{
    app::{
        OpenApiReference, OpenLogs, RefreshAgentService, RestartAgentService, StartAgentService,
        ToggleReducedMotion, ToggleTranslucency,
    },
    ui::{
        banner::Banner,
        button::Button,
        content::{PageTitle, Section, Typography as _, page},
        material, motion,
        readout::readout,
        status::{Status, Tone},
        surface::card,
        theme::{ActiveTheme as _, Theme},
    },
};

#[derive(IntoElement)]
pub struct SupportView {
    agent: Status,
    service: ServiceState,
    service_error: Option<String>,
    agent_error: Option<String>,
    identity_retry_available: bool,
}

impl SupportView {
    pub fn new(
        agent: Status,
        service: ServiceState,
        service_error: Option<String>,
        agent_error: Option<String>,
        identity_retry_available: bool,
    ) -> Self {
        Self { agent, service, service_error, agent_error, identity_retry_available }
    }
}

impl RenderOnce for SupportView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let recovery =
            Recovery::for_state(self.service, self.agent.tone, self.identity_retry_available);
        let endpoint = AgentClientOptions::default()
            .endpoint
            .to_string();
        let diagnostic = self
            .agent_error
            .or(self.service_error)
            .unwrap_or_else(|| "No error has been recorded.".into());
        let translucent = material::resolve().is_glass();
        let glass_available = material::platform_supports_glass();

        page("support")
            .child(PageTitle::new(
                "Support",
                "Restore the agent, and collect what an administrator will ask for.",
            ))
            .child(
                Section::new("Agent service").child(
                    Banner::new(
                        "service-health",
                        self.agent.tone,
                        self.agent.label.clone(),
                        self.agent.detail.clone(),
                    )
                    .when_some(recovery, |banner, recovery| banner.action(recovery.button())),
                ),
            )
            .child(
                Section::new("Technical details").child(
                    readout("technical-details")
                        .fact("Device Center", env!("CARGO_PKG_VERSION"))
                        .fact("Platform", platform())
                        .fact("Agent API", endpoint)
                        .fact("Log folder", log_folder())
                        .diagnostic("Latest error", diagnostic)
                        .action(
                            Button::new("open-logs")
                                .ghost()
                                .icon(IconName::FolderOpen)
                                .label("Open local logs")
                                .action(OpenLogs),
                        )
                        .action(
                            Button::new("open-api-reference")
                                .ghost()
                                .icon(IconName::ExternalLink)
                                .label("Open API reference")
                                .action(OpenApiReference),
                        ),
                ),
            )
            .child(
                Section::new("Display")
                    .child(
                        card(theme)
                            .w_full()
                            .child(preference_row(
                                "translucency",
                                "Translucent window",
                                if glass_available {
                                    "Blur the desktop behind the window chrome."
                                } else {
                                    "This platform does not blur behind windows."
                                },
                                translucent,
                                glass_available,
                                ToggleTranslucency,
                            ))
                            .child(
                                div()
                                    .h(px(1.0))
                                    .w_full()
                                    .bg(theme.hairline),
                            )
                            .child(preference_row(
                                "reduced-motion",
                                "Reduce motion",
                                "Stop the connection pulse and the navigation slide.",
                                motion::reduced(),
                                true,
                                ToggleReducedMotion,
                            )),
                    )
                    .child(
                        div()
                            .max_w(px(Theme::MEASURE))
                            .text_caption()
                            .text_color(theme.text_tertiary)
                            .child(
                                "These apply for this session. Set INARI_MATERIAL=opaque or \
                                 INARI_REDUCED_MOTION to start this way every time.",
                            ),
                    ),
            )
    }
}

/// This build's operating system and architecture.
///
/// Two facts the platform can always answer for itself, which is why they are
/// here and a marketing OS version is not: a wrong build number in a ticket
/// costs more than a missing one.
fn platform() -> String {
    format!("{} {}", std::env::consts::OS, std::env::consts::ARCH)
}

/// Where the logs are, in the same words the operating system would use.
///
/// Shown as well as opened. "Open local logs" is the faster route for the
/// person at the keyboard; the path is the only route for the person who is
/// not, and it is the thing a remote administrator asks for first.
fn log_folder() -> SharedString {
    crate::infrastructure::log_directory()
        .map(|directory| SharedString::from(directory.display().to_string()))
        .unwrap_or_else(|| "This system provides no data directory.".into())
}

/// The single recovery action that matches the current service state.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Recovery {
    Start,
    Restart,
    Check,
}

impl Recovery {
    fn for_state(
        service: ServiceState,
        tone: Tone,
        identity_retry_available: bool,
    ) -> Option<Self> {
        match service {
            ServiceState::Stopped => Some(Self::Start),
            ServiceState::Running if identity_retry_available => Some(Self::Check),
            // A service that is up but not answering is the case where a
            // restart is the fix; restarting a healthy one is not offered.
            ServiceState::Running if tone == Tone::Critical => Some(Self::Restart),
            ServiceState::Unavailable => Some(Self::Check),
            _ => None,
        }
    }

    fn button(self) -> Button {
        match self {
            Self::Start => Button::new("start-agent-service")
                .primary()
                .icon(IconName::ArrowRight)
                .label("Start service")
                .action(StartAgentService),
            Self::Restart => Button::new("restart-agent-service")
                .primary()
                .icon(IconName::Redo2)
                .label("Restart service")
                .action(RestartAgentService),
            Self::Check => Button::new("refresh-agent-service")
                .primary()
                .icon(IconName::Redo2)
                .label("Check again")
                .action(RefreshAgentService),
        }
    }
}

fn preference_row(
    id: &'static str,
    title: &'static str,
    detail: &'static str,
    checked: bool,
    enabled: bool,
    action: impl gpui::Action,
) -> impl IntoElement {
    let action = Box::new(action);
    div()
        .h_flex()
        .items_center()
        .justify_between()
        .gap(px(Theme::SPACE_LG))
        .w_full()
        .px(px(Theme::SPACE_LG))
        .py(px(Theme::SPACE_MD))
        .child(
            div()
                .v_flex()
                .gap(px(1.0))
                .child(div().text_body().child(title))
                .child(
                    div()
                        .text_caption()
                        .opacity(0.75)
                        .child(detail),
                ),
        )
        .child(
            Switch::new(id)
                .checked(checked)
                .disabled(!enabled)
                .on_click(move |_, window, cx| {
                    window.dispatch_action(action.boxed_clone(), cx);
                }),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_healthy_service_offers_no_recovery_action() {
        assert_eq!(Recovery::for_state(ServiceState::Running, Tone::Positive, false), None);
    }

    #[test]
    fn a_running_but_unresponsive_service_offers_a_restart() {
        assert_eq!(
            Recovery::for_state(ServiceState::Running, Tone::Critical, false),
            Some(Recovery::Restart)
        );
    }

    #[test]
    fn a_running_service_with_a_failed_identity_read_offers_a_retry() {
        for tone in [Tone::Critical, Tone::Caution] {
            assert_eq!(
                Recovery::for_state(ServiceState::Running, tone, true),
                Some(Recovery::Check)
            );
        }
    }

    #[test]
    fn a_stopped_service_offers_start_rather_than_restart() {
        assert_eq!(
            Recovery::for_state(ServiceState::Stopped, Tone::Critical, false),
            Some(Recovery::Start)
        );
    }

    #[test]
    fn transient_states_offer_nothing_to_press() {
        for state in [ServiceState::Checking, ServiceState::Starting] {
            assert_eq!(Recovery::for_state(state, Tone::Busy, false), None);
        }
    }

    #[test]
    fn the_platform_fact_names_both_halves_of_the_target() {
        // An architecture without an OS, or the reverse, sends somebody to
        // check the wrong build.
        let platform = platform();
        assert!(platform.contains(std::env::consts::OS), "{platform}");
        assert!(platform.contains(std::env::consts::ARCH), "{platform}");
    }
}
