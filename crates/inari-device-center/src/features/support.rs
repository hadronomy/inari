//! Support: recover the agent, then hand an administrator the facts.
//!
//! The recovery control offered is the one that matches the current state, and
//! only that one. A row of Start / Restart / Check buttons where two are
//! always wrong makes an operator guess during the exact moment they are least
//! able to.

use gpui::{
    IntoElement, ParentElement as _, RenderOnce, SharedString, Styled, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::{
    Disableable as _, IconName, StyledExt as _,
    button::{Button, ButtonVariants as _},
    switch::Switch,
};
use inari_agent_client::{AgentClientOptions, ServiceState};

use crate::{
    app::{
        OpenApiReference, OpenLogs, RefreshAgentService, RestartAgentService, StartAgentService,
        ToggleReducedMotion, ToggleTranslucency,
    },
    ui::{
        banner::Banner,
        content::{Field, PageTitle, Section, Typography as _, page},
        material, motion,
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
                Section::new("Technical details")
                    .child(
                        card(theme)
                            .gap(px(Theme::SPACE_MD))
                            .w_full()
                            .p(px(Theme::SPACE_LG))
                            .child(
                                Field::new("Device Center version", env!("CARGO_PKG_VERSION"))
                                    .technical(),
                            )
                            .child(Field::new("Agent API", endpoint).technical())
                            .child(Field::new("Latest error", breakable(diagnostic)).technical()),
                    )
                    .child(
                        div()
                            .h_flex()
                            .flex_wrap()
                            .items_center()
                            .gap(px(Theme::SPACE_SM))
                            .child(action_button(
                                "open-logs",
                                "Open local logs",
                                IconName::FolderOpen,
                                OpenLogs,
                            ))
                            .child(action_button(
                                "open-api-reference",
                                "Open API reference",
                                IconName::ExternalLink,
                                OpenApiReference,
                            )),
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
            Self::Start => primary_button(
                "start-agent-service",
                "Start service",
                IconName::ArrowRight,
                StartAgentService,
            ),
            Self::Restart => primary_button(
                "restart-agent-service",
                "Restart service",
                IconName::Redo2,
                RestartAgentService,
            ),
            Self::Check => primary_button(
                "refresh-agent-service",
                "Check again",
                IconName::Redo2,
                RefreshAgentService,
            ),
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

fn primary_button(
    id: &'static str,
    label: &'static str,
    icon: IconName,
    action: impl gpui::Action,
) -> Button {
    let action = Box::new(action);
    Button::new(id)
        .primary()
        .icon(icon)
        .label(label)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}

fn action_button(
    id: &'static str,
    label: &'static str,
    icon: IconName,
    action: impl gpui::Action,
) -> Button {
    let action = Box::new(action);
    Button::new(id)
        .outline()
        .icon(icon)
        .label(label)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}

/// Insert zero-width spaces after URL and path punctuation so a long
/// diagnostic wraps inside its card instead of forcing the card wider than the
/// panel.
fn breakable(message: String) -> SharedString {
    let mut wrapped = String::with_capacity(message.len());
    for character in message.chars() {
        wrapped.push(character);
        if matches!(character, '/' | ':' | '?' | '&' | '=' | ')' | ']') {
            wrapped.push('\u{200b}');
        }
    }
    wrapped.into()
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
}
