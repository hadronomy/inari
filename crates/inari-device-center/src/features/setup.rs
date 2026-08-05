use std::collections::HashSet;

use chrono::Local;
use gpui::{
    Entity, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce, SharedString,
    StatefulInteractiveElement as _, Styled, WeakEntity, div, prelude::FluentBuilder as _, rems,
    svg,
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
    ui::{Message, MessageTone, palette},
};

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
        let colors = palette::Palette::current(cx);
        let (label, title, detail) = copy_for(&self.snapshot);
        let stage = self.snapshot.stage;
        let access = self.snapshot.access;
        let working = self.working;
        let has_preview = self.preview.is_some();
        let selected_count = self.selected_devices.len();
        let (guidance_title, guidance_tone) = if access == SetupAccess::Unknown {
            ("Agent connection unavailable", MessageTone::Warning)
        } else {
            ("Connection required", MessageTone::Info)
        };

        div()
            .id("setup")
            .size_full()
            .overflow_y_scroll()
            .bg(colors.canvas)
            .child(
                div()
                    .w_full()
                    .max_w(rems(48.))
                    .mx_auto()
                    .v_flex()
                    .gap(rems(1.5))
                    .px(rems(1.5))
                    .py(rems(1.5))
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .gap(rems(0.75))
                            .child(
                                svg()
                                    .path("inari-mark-torii-ui.svg")
                                    .size(rems(2.))
                                    .text_color(colors.vermilion)
                                    .flex_shrink_0(),
                            )
                            .child(
                                div()
                                    .v_flex()
                                    .gap(rems(0.25))
                                    .child(
                                        div()
                                            .text_size(rems(0.75))
                                            .font_weight(gpui::FontWeight::SEMIBOLD)
                                            .text_color(colors.vermilion)
                                            .child(label),
                                    )
                                    .child(
                                        div()
                                            .text_size(rems(1.75))
                                            .line_height(rems(2.))
                                            .font_weight(gpui::FontWeight::SEMIBOLD)
                                            .child(title),
                                    ),
                            ),
                    )
                    .child(
                        div()
                            .max_w(rems(40.))
                            .text_size(rems(0.875))
                            .line_height(rems(1.25))
                            .text_color(colors.text_muted)
                            .child(detail),
                    )
                    .when_some(self.snapshot.guidance, |view, guidance| {
                        view.child(Message::new(
                            "setup-status",
                            guidance_tone,
                            guidance_title,
                            guidance,
                        ))
                    })
                    .when_some(self.error, |view, error| {
                        view.child(Message::new(
                            "setup-error",
                            MessageTone::Danger,
                            "Setup could not continue",
                            error,
                        ))
                    })
                    .when(
                        access == SetupAccess::Required && stage == SetupStage::Invitation,
                        |view| {
                            view.child(
                                div()
                                    .v_flex()
                                    .gap(rems(0.75))
                                    .child(field_label("Invitation link"))
                                    .child(
                                        Input::new(&self.invitation_input)
                                            .cleanable(true)
                                            .disabled(working),
                                    )
                                    .when_some(self.preview, |form, preview| {
                                        form.child(trust_review(preview, colors))
                                    })
                                    .child(
                                        div()
                                            .flex()
                                            .flex_wrap()
                                            .items_center()
                                            .gap(rems(0.75))
                                            .when(!has_preview, |actions| {
                                                actions.child(action_button(
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
                                                ))
                                            })
                                            .when(has_preview, |actions| {
                                                actions.child(action_button(
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
                                                ))
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
                            div()
                                .v_flex()
                                .gap(rems(0.75))
                                .child(field_label("Devices to share"))
                                .children(
                                    self.snapshot
                                        .devices
                                        .into_iter()
                                        .enumerate()
                                        .map(move |(index, device)| {
                                            let id = device.id.clone();
                                            let center = center.clone();
                                            Checkbox::new(("setup-device", index))
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
                                        }),
                                )
                                .child(
                                    div()
                                        .text_size(rems(0.75))
                                        .text_color(colors.text_muted)
                                        .child(format!(
                                            "{selected_count} of {device_count} selected"
                                        )),
                                )
                                .child(
                                    div()
                                        .flex()
                                        .flex_wrap()
                                        .items_center()
                                        .gap(rems(0.75))
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
                        view.child(action_button(
                            "start-over",
                            if working { "Resetting" } else { "Start over" },
                            IconName::Redo2,
                            working,
                            StartOver,
                            true,
                        ))
                    })
                    .when(access == SetupAccess::Unknown, |view| {
                        view.child(action_button(
                            "retry-agent",
                            if working { "Checking" } else { "Try again" },
                            IconName::Redo2,
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
    icon: IconName,
    disabled: bool,
    action: impl gpui::Action,
    primary: bool,
) -> impl IntoElement {
    let action = Box::new(action);
    Button::new(id)
        .h(rems(2.))
        .when(primary, |button| button.primary())
        .when(!primary, |button| button.ghost())
        .icon(icon)
        .label(label)
        .disabled(disabled)
        .on_click(move |_, window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
}

fn trust_review(preview: EnrollmentPreview, colors: palette::Palette) -> impl IntoElement {
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
        .gap(rems(0.75))
        .p(rems(1.))
        .rounded(rems(0.5))
        .bg(colors.info_wash)
        .child(
            div()
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .text_color(colors.info)
                .child("Review this connection"),
        )
        .child(trust_row("Organization", controller, colors))
        .child(trust_row("Controller", preview.controller_url.to_string(), colors))
        .child(trust_row(
            "Connection security",
            if preview.requires_mutual_tls {
                "Mutual TLS after enrollment"
            } else {
                "Controller managed"
            },
            colors,
        ))
        .child(trust_row(
            "Link expires",
            preview
                .expires_at
                .with_timezone(&Local)
                .format("%Y-%m-%d %H:%M %Z")
                .to_string(),
            colors,
        ))
}

fn trust_row(
    label: &'static str,
    value: impl Into<SharedString>,
    colors: palette::Palette,
) -> impl IntoElement {
    div()
        .flex()
        .flex_wrap()
        .items_start()
        .gap(rems(0.5))
        .child(
            div()
                .w(rems(8.))
                .flex_shrink_0()
                .text_size(rems(0.75))
                .text_color(colors.info)
                .child(label),
        )
        .child(
            div()
                .min_w(rems(12.))
                .flex_1()
                .text_size(rems(0.8125))
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .text_color(colors.info)
                .child(value.into()),
        )
}

fn field_label(label: &'static str) -> impl IntoElement {
    div()
        .text_size(rems(0.8125))
        .font_weight(gpui::FontWeight::SEMIBOLD)
        .child(label)
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
