use gpui::{
    Entity, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce, SharedString,
    Styled, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{
    Disableable as _, StyledExt as _,
    button::{Button, ButtonVariants as _},
    input::{Input, InputState},
};
use inari_agent_client::{EnrollmentPreview, SetupAccess, SetupSnapshot, SetupStage};

use crate::{
    app::{
        BeginSetup, ConfirmDevices, ContinueWithoutDevices, PreviewInvitation, RetryConnection,
        StartOver,
    },
    assets::image as brand_image,
    ui::palette,
};

#[derive(IntoElement)]
pub struct SetupView {
    snapshot: SetupSnapshot,
    invitation_input: Entity<InputState>,
    preview: Option<EnrollmentPreview>,
    error: Option<String>,
    working: bool,
}

impl SetupView {
    pub fn new(
        snapshot: SetupSnapshot,
        invitation_input: Entity<InputState>,
        preview: Option<EnrollmentPreview>,
        error: Option<String>,
        working: bool,
    ) -> Self {
        Self { snapshot, invitation_input, preview, error, working }
    }
}

impl RenderOnce for SetupView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        let (eyebrow, title, detail) = copy_for(&self.snapshot);
        let stage = self.snapshot.stage;
        let access = self.snapshot.access;
        let working = self.working;
        let has_preview = self.preview.is_some();

        div()
            .id("setup")
            .size_full()
            .flex()
            .items_center()
            .justify_center()
            .px(rems(2.))
            .py(rems(2.))
            .bg(colors.canvas)
            .child(
                div()
                    .w_full()
                    .max_w(rems(52.))
                    .v_flex()
                    .gap(rems(1.4))
                    .p(rems(2.5))
                    .rounded(rems(1.35))
                    .border_1()
                    .border_color(colors.border)
                    .bg(colors.surface)
                    .child(
                        brand_image("inari-icon-128.png")
                            .size(rems(3.25))
                            .rounded(rems(0.95))
                            .flex_shrink_0(),
                    )
                    .child(
                        div()
                            .text_size(rems(0.75))
                            .font_weight(gpui::FontWeight::SEMIBOLD)
                            .text_color(colors.vermilion)
                            .child(eyebrow.to_uppercase()),
                    )
                    .child(
                        div()
                            .text_size(rems(2.15))
                            .line_height(rems(2.45))
                            .font_weight(gpui::FontWeight::SEMIBOLD)
                            .child(title),
                    )
                    .child(
                        div()
                            .max_w(rems(40.))
                            .text_size(rems(0.98))
                            .line_height(rems(1.5))
                            .text_color(colors.text_muted)
                            .child(detail),
                    )
                    .when_some(self.snapshot.guidance, |card, guidance| {
                        card.child(information_message(guidance, colors))
                    })
                    .when_some(self.error, |card, error| card.child(error_message(error, colors)))
                    .when(
                        access == SetupAccess::Required && stage == SetupStage::Invitation,
                        |card| {
                            card.child(
                                div()
                                    .v_flex()
                                    .gap(rems(0.65))
                                    .pt(rems(0.35))
                                    .child(
                                        div()
                                            .text_size(rems(0.78))
                                            .font_weight(gpui::FontWeight::SEMIBOLD)
                                            .child("Invitation"),
                                    )
                                    .child(
                                        Input::new(&self.invitation_input)
                                            .cleanable(true)
                                            .disabled(working),
                                    )
                                    .when_some(self.preview, |form, preview| {
                                        form.child(preview_card(preview, colors))
                                    })
                                    .child(
                                        div()
                                            .flex()
                                            .items_center()
                                            .gap(rems(0.65))
                                            .when(!has_preview, |actions| {
                                                actions.child(action_button(
                                                    "review-invitation",
                                                    if working {
                                                        "Checking invitation…"
                                                    } else {
                                                        "Review invitation"
                                                    },
                                                    working,
                                                    PreviewInvitation,
                                                    true,
                                                ))
                                            })
                                            .when(has_preview, |actions| {
                                                actions.child(action_button(
                                                    "begin-setup",
                                                    if working {
                                                        "Connecting…"
                                                    } else {
                                                        "Connect this computer"
                                                    },
                                                    working,
                                                    BeginSetup,
                                                    true,
                                                ))
                                            }),
                                    ),
                            )
                        },
                    )
                    .when(stage == SetupStage::Devices, |card| {
                        let device_count = self.snapshot.devices.len();
                        card.child(
                            div()
                                .v_flex()
                                .gap(rems(0.85))
                                .children(
                                    self.snapshot
                                        .devices
                                        .into_iter()
                                        .map(|device| {
                                            div()
                                                .flex()
                                                .items_center()
                                                .justify_between()
                                                .px(rems(0.9))
                                                .py(rems(0.75))
                                                .rounded(rems(0.7))
                                                .border_1()
                                                .border_color(colors.border)
                                                .child(
                                                    div()
                                                        .font_weight(gpui::FontWeight::MEDIUM)
                                                        .child(device.name),
                                                )
                                                .child(
                                                    div()
                                                        .text_size(rems(0.76))
                                                        .text_color(colors.text_muted)
                                                        .child("Ready to share"),
                                                )
                                        }),
                                )
                                .child(
                                    div()
                                        .flex()
                                        .items_center()
                                        .gap(rems(0.65))
                                        .child(action_button(
                                            "confirm-devices",
                                            if device_count == 0 {
                                                "Finish setup"
                                            } else {
                                                "Share these devices"
                                            },
                                            working,
                                            ConfirmDevices,
                                            true,
                                        ))
                                        .when(device_count > 0, |actions| {
                                            actions.child(action_button(
                                                "continue-without-devices",
                                                "Continue without devices",
                                                working,
                                                ContinueWithoutDevices,
                                                false,
                                            ))
                                        }),
                                ),
                        )
                    })
                    .when(stage == SetupStage::Failed, |card| {
                        card.child(action_button(
                            "start-over",
                            if working { "Resetting…" } else { "Start over" },
                            working,
                            StartOver,
                            true,
                        ))
                    })
                    .when(access == SetupAccess::Unknown, |card| {
                        card.child(action_button(
                            "retry-agent",
                            if working { "Checking…" } else { "Try again" },
                            working,
                            RetryConnection,
                            true,
                        ))
                    }),
            )
    }
}

fn action_button(
    id: &'static str,
    label: &'static str,
    disabled: bool,
    action: impl gpui::Action,
    primary: bool,
) -> impl IntoElement {
    let action = Box::new(action);
    Button::new(id)
        .when(primary, |button| button.primary())
        .when(!primary, |button| button.ghost())
        .label(label)
        .disabled(disabled)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}

fn preview_card(preview: EnrollmentPreview, colors: palette::Palette) -> impl IntoElement {
    let controller = preview
        .controller_name
        .unwrap_or_else(|| {
            preview
                .controller_url
                .host_str()
                .unwrap_or("Controller")
                .to_owned()
        });
    div()
        .v_flex()
        .gap(rems(0.65))
        .p(rems(1.))
        .rounded(rems(0.75))
        .border_1()
        .border_color(colors.border)
        .bg(colors.blue_wash)
        .child(
            div()
                .text_size(rems(0.75))
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .text_color(colors.blue_text)
                .child("Review this connection"),
        )
        .child(trust_row("Controller", controller, colors))
        .child(trust_row("Address", preview.controller_url.to_string(), colors))
        .child(trust_row(
            "Certificate",
            if preview.requires_mutual_tls {
                "Mutual TLS after enrollment"
            } else {
                "Controller-managed"
            },
            colors,
        ))
        .child(trust_row("Expires", preview.expires_at.to_rfc3339(), colors))
}

fn trust_row(
    label: &'static str,
    value: impl Into<SharedString>,
    colors: palette::Palette,
) -> impl IntoElement {
    div()
        .flex()
        .items_start()
        .gap(rems(1.))
        .child(
            div()
                .w(rems(7.))
                .flex_shrink_0()
                .text_size(rems(0.76))
                .text_color(colors.text_muted)
                .child(label),
        )
        .child(
            div()
                .text_size(rems(0.8))
                .font_weight(gpui::FontWeight::MEDIUM)
                .child(value.into()),
        )
}

fn information_message(
    message: impl Into<SharedString>,
    colors: palette::Palette,
) -> impl IntoElement {
    div()
        .id("setup-status")
        .p(rems(1.))
        .rounded(rems(0.75))
        .bg(colors.blue_wash)
        .text_color(colors.blue_text)
        .child(message.into())
}

fn error_message(message: impl Into<SharedString>, colors: palette::Palette) -> impl IntoElement {
    div()
        .id("setup-error")
        .p(rems(1.))
        .rounded(rems(0.75))
        .border_1()
        .border_color(colors.vermilion)
        .text_color(colors.vermilion)
        .child(message.into())
}

fn copy_for(snapshot: &SetupSnapshot) -> (&'static str, &'static str, &'static str) {
    match snapshot.access {
        SetupAccess::Unknown => (
            "Checking this computer",
            "Connecting to the Inari agent",
            "Device Center is verifying the local service and protected identity.",
        ),
        SetupAccess::Required => match snapshot.stage {
            SetupStage::Invitation => (
                "Set up Inari",
                "Connect this computer",
                "Review the organization and controller before this computer joins Inari.",
            ),
            SetupStage::Securing => (
                "Securing the connection",
                "Protecting this computer",
                "Setup will continue from this checkpoint when the local agent is ready.",
            ),
            SetupStage::Connecting => (
                "Connecting to Inari",
                "Reaching your controller",
                "The local agent is establishing its managed connection.",
            ),
            SetupStage::Devices => (
                "Finding devices",
                "Choose what this computer shares",
                "Confirm the discovered hardware, or finish setup without sharing a device yet.",
            ),
            SetupStage::Failed => (
                "Setup needs attention",
                "We could not finish connecting",
                "The incomplete attempt has not granted access to Device Center.",
            ),
            SetupStage::Complete => (
                "Setup complete",
                "This computer is connected",
                "Device Center is preparing your operational overview.",
            ),
        },
        SetupAccess::Complete => (
            "Setup complete",
            "This computer is connected",
            "Device Center is preparing your operational overview.",
        ),
    }
}
