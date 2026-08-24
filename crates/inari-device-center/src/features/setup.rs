//! First-run setup: connect this computer to a controller.
//!
//! This is the one screen in the app that asks the operator to trust something
//! external, so the review card is deliberately plain and complete. It names
//! the organization, the controller URL, the security posture, and the expiry
//! before the connect action appears — an invitation link is a credential, and
//! a link someone was sent in a chat message deserves to be read before it is
//! used.

use std::collections::HashSet;

use chrono::Local;
use gpui::{
    Entity, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce,
    StatefulInteractiveElement as _, Styled, WeakEntity, div, prelude::FluentBuilder as _, px, svg,
};
use gpui_component::{
    Disableable as _, IconName, StyledExt as _,
    button::{Button, ButtonVariants as _},
    checkbox::Checkbox,
    input::{Input, InputState},
};
use inari_agent_client::{DeviceId, EnrollmentPreview, SetupAccess, SetupSnapshot, SetupStage};

use crate::{
    app::{
        BeginSetup, ConfirmDevices, ContinueWithoutDevices, DeviceCenter, PreviewInvitation,
        RetryConnection, StartOver,
    },
    ui::{
        banner::Banner,
        content::{Field, Section, Typography as _},
        status::Tone,
        surface::card,
        theme::{ActiveTheme as _, Theme},
    },
};

/// The stages an operator passes through, in order. `Failed` and `Complete`
/// are outcomes rather than steps, so they are not on the track.
const TRACK: [SetupStage; 4] =
    [SetupStage::Invitation, SetupStage::Securing, SetupStage::Connecting, SetupStage::Devices];

#[derive(IntoElement)]
pub struct SetupView {
    snapshot: SetupSnapshot,
    invitation_input: Entity<InputState>,
    preview: Option<EnrollmentPreview>,
    error: Option<String>,
    working: bool,
    selected_devices: HashSet<DeviceId>,
    center: WeakEntity<DeviceCenter>,
}

impl SetupView {
    pub fn new(
        snapshot: SetupSnapshot,
        invitation_input: Entity<InputState>,
        preview: Option<EnrollmentPreview>,
        error: Option<String>,
        working: bool,
        selected_devices: HashSet<DeviceId>,
        center: WeakEntity<DeviceCenter>,
    ) -> Self {
        Self { snapshot, invitation_input, preview, error, working, selected_devices, center }
    }
}

impl RenderOnce for SetupView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let (eyebrow, title, detail) = copy_for(&self.snapshot);
        let stage = self.snapshot.stage;
        let access = self.snapshot.access;
        let working = self.working;
        let has_preview = self.preview.is_some();
        let selected_count = self.selected_devices.len();

        div()
            .id("setup")
            .size_full()
            .overflow_y_scroll()
            .child(
                div()
                    .v_flex()
                    .w_full()
                    .max_w(px(600.0))
                    .mx_auto()
                    .gap(px(Theme::SPACE_XL))
                    .px(px(Theme::SPACE_2XL))
                    .pt(px(Theme::SPACE_2XL))
                    .pb(px(Theme::SPACE_2XL))
                    .child(
                        div()
                            .v_flex()
                            .gap(px(Theme::SPACE_MD))
                            .child(
                                svg()
                                    .path("inari-mark-torii-ui.svg")
                                    .size(px(34.0))
                                    .flex_none()
                                    .text_color(theme.accent),
                            )
                            .child(
                                div()
                                    .v_flex()
                                    .gap(px(Theme::SPACE_XS))
                                    .child(
                                        div()
                                            .text_caption()
                                            .font_weight(gpui::FontWeight::MEDIUM)
                                            .text_color(theme.accent)
                                            .child(eyebrow),
                                    )
                                    .child(div().text_display().child(title))
                                    .child(
                                        div()
                                            .text_body()
                                            .text_color(theme.text_secondary)
                                            .child(detail),
                                    ),
                            ),
                    )
                    .when(access == SetupAccess::Required, |view| {
                        view.child(stage_track(stage, theme))
                    })
                    .when_some(self.snapshot.guidance, |view, guidance| {
                        let (title, tone) = if access == SetupAccess::Unknown {
                            ("Device Center cannot reach the agent", Tone::Caution)
                        } else {
                            ("Connection required", Tone::Busy)
                        };
                        view.child(Banner::new("setup-status", tone, title, guidance))
                    })
                    .when_some(self.error, |view, error| {
                        view.child(Banner::new(
                            "setup-error",
                            Tone::Critical,
                            "Setup could not continue",
                            error,
                        ))
                    })
                    .when(
                        access == SetupAccess::Required && stage == SetupStage::Invitation,
                        |view| {
                            view.child(
                                Section::new("Invitation link")
                                    .child(
                                        Input::new(&self.invitation_input)
                                            .cleanable(true)
                                            .disabled(working),
                                    )
                                    .when_some(self.preview, |form, preview| {
                                        form.child(trust_review(preview, theme))
                                    })
                                    .child(
                                        div()
                                            .h_flex()
                                            .flex_wrap()
                                            .items_center()
                                            .gap(px(Theme::SPACE_SM))
                                            .child(if has_preview {
                                                action_button(
                                                    "begin-setup",
                                                    if working {
                                                        "Connecting"
                                                    } else {
                                                        "Connect this computer"
                                                    },
                                                    IconName::ArrowRight,
                                                    working,
                                                    BeginSetup,
                                                    true,
                                                )
                                            } else {
                                                action_button(
                                                    "review-invitation",
                                                    if working {
                                                        "Checking invitation"
                                                    } else {
                                                        "Review invitation"
                                                    },
                                                    IconName::Search,
                                                    working,
                                                    PreviewInvitation,
                                                    true,
                                                )
                                            }),
                                    ),
                            )
                        },
                    )
                    .when(stage == SetupStage::Devices, |view| {
                        let center = self.center.clone();
                        let selected_devices = self.selected_devices.clone();
                        let device_count = self.snapshot.devices.len();
                        view.child(
                            Section::new("Devices to share")
                                .aside(
                                    div()
                                        .text_caption()
                                        .text_color(theme.text_secondary)
                                        .child(format!(
                                            "{selected_count} of {device_count} selected"
                                        )),
                                )
                                .child(
                                    card(theme)
                                        .gap(px(Theme::SPACE_MD))
                                        .w_full()
                                        .p(px(Theme::SPACE_LG))
                                        .when(device_count == 0, |list| {
                                            list.child(
                                                div()
                                                    .text_body()
                                                    .text_color(theme.text_secondary)
                                                    .child(
                                                        "The agent found no devices. You can \
                                                         finish setup and add them later.",
                                                    ),
                                            )
                                        })
                                        .children(self.snapshot.devices.into_iter().map(
                                            move |device| {
                                                let id = device.id.clone();
                                                let center = center.clone();
                                                Checkbox::new(gpui::SharedString::from(format!(
                                                    "setup-device-{}",
                                                    device.id
                                                )))
                                                .checked(selected_devices.contains(&device.id))
                                                .disabled(working)
                                                .label(device.name)
                                                .on_click(move |checked, _, cx| {
                                                    center
                                                        .update(cx, |center, cx| {
                                                            center.set_setup_device_selected(
                                                                id.clone(),
                                                                *checked,
                                                                cx,
                                                            );
                                                        })
                                                        .ok();
                                                })
                                            },
                                        )),
                                )
                                .child(
                                    div()
                                        .h_flex()
                                        .flex_wrap()
                                        .items_center()
                                        .gap(px(Theme::SPACE_SM))
                                        .child(action_button(
                                            "confirm-devices",
                                            if device_count == 0 {
                                                "Finish setup"
                                            } else {
                                                "Share selected devices"
                                            },
                                            IconName::Check,
                                            working,
                                            ConfirmDevices,
                                            true,
                                        ))
                                        .when(device_count > 0, |actions| {
                                            actions.child(action_button(
                                                "continue-without-devices",
                                                "Continue without devices",
                                                IconName::ArrowRight,
                                                working,
                                                ContinueWithoutDevices,
                                                false,
                                            ))
                                        }),
                                ),
                        )
                    })
                    .when(stage == SetupStage::Failed, |view| {
                        view.child(div().child(action_button(
                            "start-over",
                            if working { "Resetting" } else { "Start over" },
                            IconName::Redo2,
                            working,
                            StartOver,
                            true,
                        )))
                    })
                    .when(access == SetupAccess::Unknown, |view| {
                        view.child(div().child(action_button(
                            "retry-agent",
                            if working { "Checking" } else { "Try again" },
                            IconName::Redo2,
                            working,
                            RetryConnection,
                            true,
                        )))
                    }),
            )
    }
}

/// A segmented track showing how far setup has come.
///
/// Segments rather than numbered steps: the operator does not choose these
/// stages or move between them, so a numbered list would imply a control they
/// do not have.
fn stage_track(stage: SetupStage, theme: &Theme) -> impl IntoElement {
    let reached = TRACK
        .iter()
        .position(|candidate| *candidate == stage);
    div()
        .v_flex()
        .gap(px(Theme::SPACE_SM))
        .w_full()
        .child(
            div()
                .h_flex()
                .gap(px(Theme::SPACE_XS))
                .w_full()
                .children(
                    TRACK
                        .iter()
                        .enumerate()
                        .map(|(index, _)| {
                            let filled = reached.is_some_and(|current| index <= current);
                            div()
                                .h(px(3.0))
                                .flex_1()
                                .rounded_full()
                                .bg(if filled { theme.accent } else { theme.hairline_strong })
                        }),
                ),
        )
        .child(
            div()
                .text_caption()
                .text_color(theme.text_tertiary)
                .child(match reached {
                    Some(index) => format!("Step {} of {}", index + 1, TRACK.len()),
                    None => "Finishing".to_string(),
                }),
        )
}

fn action_button(
    id: &'static str,
    label: &'static str,
    icon: IconName,
    disabled: bool,
    action: impl gpui::Action,
    primary: bool,
) -> Button {
    let action = Box::new(action);
    Button::new(id)
        .when(primary, |button| button.primary())
        .when(!primary, |button| button.ghost())
        .icon(icon)
        .label(label)
        .disabled(disabled)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}

/// What the operator is agreeing to. Rendered before the connect action, never
/// beside it.
fn trust_review(preview: EnrollmentPreview, theme: &Theme) -> impl IntoElement {
    let controller = preview
        .controller_name
        .unwrap_or_else(|| {
            preview
                .controller_url
                .host_str()
                .unwrap_or("Controller")
                .to_owned()
        });
    card(theme)
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .p(px(Theme::SPACE_LG))
        // A stronger edge than a normal card: this is the one surface in the
        // app that carries a decision, and it should read as a distinct object
        // rather than as more page.
        .border_color(theme.hairline_strong)
        .child(
            div()
                .text_heading()
                .child("Review this connection"),
        )
        .child(Field::new("Organization", controller))
        .child(Field::new("Controller", preview.controller_url.to_string()).technical())
        .child(Field::new(
            "Connection security",
            if preview.requires_mutual_tls {
                "Mutual TLS after enrollment"
            } else {
                "Controller managed"
            },
        ))
        .child(Field::new(
            "Link expires",
            preview
                .expires_at
                .with_timezone(&Local)
                .format("%Y-%m-%d %H:%M %Z")
                .to_string(),
        ))
}

fn copy_for(snapshot: &SetupSnapshot) -> (&'static str, &'static str, &'static str) {
    match snapshot.access {
        SetupAccess::Unknown => (
            "Checking this computer",
            "Connecting to the Inari agent",
            "Device Center is checking the agent service on this computer.",
        ),
        SetupAccess::Required => match snapshot.stage {
            SetupStage::Invitation => (
                "Set up Inari",
                "Connect this computer",
                "Confirm the organization and controller before you connect this computer.",
            ),
            SetupStage::Securing => (
                "Securing the connection",
                "Protecting this computer",
                "Setup continues when the agent is ready.",
            ),
            SetupStage::Connecting => (
                "Connecting to Inari",
                "Contacting the controller",
                "The agent is connecting to your controller.",
            ),
            SetupStage::Devices => (
                "Select devices",
                "Choose what this computer shares",
                "All found devices are selected. Clear a device to keep it local.",
            ),
            SetupStage::Failed => (
                "Setup needs attention",
                "Setup did not finish",
                "No new access was granted. Start again when you are ready.",
            ),
            SetupStage::Complete => (
                "Setup complete",
                "This computer is connected",
                "Device Center is preparing the overview.",
            ),
        },
        SetupAccess::Complete => (
            "Setup complete",
            "This computer is connected",
            "Device Center is preparing the overview.",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_track_stage_is_distinct_and_ordered_from_the_invitation() {
        assert_eq!(TRACK[0], SetupStage::Invitation);
        assert_eq!(TRACK.len(), 4);
        for (index, stage) in TRACK.iter().enumerate() {
            for other in &TRACK[index + 1..] {
                assert_ne!(stage, other);
            }
        }
    }

    #[test]
    fn outcome_stages_stay_off_the_progress_track() {
        assert!(!TRACK.contains(&SetupStage::Failed));
        assert!(!TRACK.contains(&SetupStage::Complete));
    }
}
