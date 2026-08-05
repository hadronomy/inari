use std::sync::Arc;

use gpui::{
    AppContext as _, Context, Entity, InteractiveElement as _, IntoElement, ParentElement as _,
    Render, Styled, Subscription, Window, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{
    Icon, IconName, StyledExt as _,
    button::{Button, ButtonVariants as _},
    input::{Input, InputEvent, InputState},
};
use inari_agent_client::{Device, DeviceId, DeviceKind, DeviceState};

use crate::ui::{PageHeader, page, palette};

pub struct DeviceDirectory {
    devices: Arc<[Device]>,
    search: Entity<InputState>,
    selected: Option<DeviceId>,
    _search_subscription: Subscription,
}

impl DeviceDirectory {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let search = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("Search devices")
                .clean_on_escape()
        });
        let search_subscription = cx.subscribe_in(&search, window, |_, _, event, _, cx| {
            if matches!(event, InputEvent::Change) {
                cx.notify();
            }
        });
        Self {
            devices: Arc::default(),
            search,
            selected: None,
            _search_subscription: search_subscription,
        }
    }

    pub fn replace_devices(&mut self, devices: Vec<Device>, cx: &mut Context<Self>) {
        let selected_still_exists = self
            .selected
            .as_ref()
            .is_some_and(|selected| {
                devices
                    .iter()
                    .any(|device| &device.id == selected)
            });
        if !selected_still_exists {
            self.selected = devices
                .first()
                .map(|device| device.id.clone());
        }
        self.devices = devices.into();
        cx.notify();
    }

    fn select(&mut self, id: DeviceId, cx: &mut Context<Self>) {
        self.selected = Some(id);
        cx.notify();
    }
}

impl Render for DeviceDirectory {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        let query = self
            .search
            .read(cx)
            .value()
            .trim()
            .to_lowercase();
        let visible = self
            .devices
            .iter()
            .filter(|device| matches_search(device, &query))
            .cloned()
            .collect::<Vec<_>>();
        let selected = self
            .selected
            .as_ref()
            .and_then(|id| {
                self.devices
                    .iter()
                    .find(|device| &device.id == id)
            })
            .cloned();

        page()
            .child(PageHeader::new(
                "Devices",
                "Find connected hardware and check its current state.",
            ))
            .child(
                div()
                    .max_w(rems(24.))
                    .child(Input::new(&self.search).cleanable(true)),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .items_start()
                    .gap(rems(0.75))
                    .child(
                        div()
                            .id("device-directory")
                            .min_w(rems(20.))
                            .flex_1()
                            .v_flex()
                            .rounded(rems(0.5))
                            .bg(colors.surface)
                            .overflow_hidden()
                            .when(visible.is_empty(), |list| {
                                list.child(empty_directory(self.devices.is_empty(), colors))
                            })
                            .children(
                                visible
                                    .into_iter()
                                    .enumerate()
                                    .map(|(index, device)| {
                                        let selected = self.selected.as_ref() == Some(&device.id);
                                        let id = device.id.clone();
                                        device_row(device, index, selected, colors).on_click(
                                            cx.listener(move |directory, _, _, cx| {
                                                directory.select(id.clone(), cx);
                                            }),
                                        )
                                    }),
                            ),
                    )
                    .child(
                        div()
                            .min_w(rems(16.))
                            .w(rems(21.))
                            .flex_1()
                            .child(selected.map_or_else(
                                || empty_detail(colors),
                                |device| device_detail(device, colors),
                            )),
                    ),
            )
    }
}

fn device_row(device: Device, index: usize, selected: bool, colors: palette::Palette) -> Button {
    let kind = device_kind(device.kind);
    let (state_color, state_icon) = state_treatment(device.state, colors);
    Button::new(("device", index))
        .ghost()
        .w_full()
        .justify_start()
        .gap(rems(0.75))
        .px(rems(1.))
        .py(rems(0.75))
        .rounded_none()
        .when(index > 0, |row| {
            row.border_t_1()
                .border_color(colors.separator)
        })
        .when(selected, |row| row.bg(colors.info_wash))
        .child(
            Icon::new(device_icon(device.kind))
                .size(rems(1.125))
                .text_color(colors.text_muted),
        )
        .child(
            div()
                .min_w(rems(9.))
                .flex_1()
                .v_flex()
                .items_start()
                .gap(rems(0.25))
                .child(
                    div()
                        .font_weight(gpui::FontWeight::SEMIBOLD)
                        .child(device.name),
                )
                .child(
                    div()
                        .text_size(rems(0.75))
                        .text_color(colors.text_muted)
                        .child(kind),
                ),
        )
        .child(
            div()
                .flex()
                .items_center()
                .gap(rems(0.25))
                .text_size(rems(0.75))
                .text_color(state_color)
                .child(Icon::new(state_icon).size(rems(0.875)))
                .child(device_state(device.state)),
        )
}

fn device_detail(device: Device, colors: palette::Palette) -> gpui::Div {
    let (state_color, state_icon) = state_treatment(device.state, colors);
    div()
        .v_flex()
        .gap(rems(1.))
        .p(rems(1.))
        .rounded(rems(0.5))
        .bg(colors.surface)
        .child(
            div()
                .flex()
                .items_center()
                .gap(rems(0.75))
                .child(
                    Icon::new(device_icon(device.kind))
                        .size(rems(1.25))
                        .text_color(colors.text_muted),
                )
                .child(
                    div()
                        .v_flex()
                        .gap(rems(0.25))
                        .child(
                            div()
                                .text_size(rems(1.125))
                                .font_weight(gpui::FontWeight::SEMIBOLD)
                                .child(device.name),
                        )
                        .child(
                            div()
                                .text_size(rems(0.75))
                                .text_color(colors.text_muted)
                                .child(device_kind(device.kind)),
                        ),
                ),
        )
        .child(
            div()
                .flex()
                .items_center()
                .gap(rems(0.5))
                .text_color(state_color)
                .child(Icon::new(state_icon).size(rems(1.)))
                .child(device_state(device.state)),
        )
        .child(detail_row("Device ID", device.id.to_string(), colors))
        .child(
            div()
                .text_size(rems(0.75))
                .line_height(rems(1.125))
                .text_color(colors.text_muted)
                .child("Use the device ID in integrations."),
        )
}

fn detail_row(
    label: &'static str,
    value: impl Into<gpui::SharedString>,
    colors: palette::Palette,
) -> gpui::Div {
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
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .child(value.into()),
        )
}

fn empty_directory(no_devices: bool, colors: palette::Palette) -> gpui::Div {
    div()
        .p(rems(1.))
        .text_color(colors.text_muted)
        .child(if no_devices { "No devices found." } else { "No devices match this search." })
}

fn empty_detail(colors: palette::Palette) -> gpui::Div {
    div()
        .p(rems(1.))
        .rounded(rems(0.5))
        .bg(colors.surface)
        .text_color(colors.text_muted)
        .child("Select a device to view its details.")
}

fn matches_search(device: &Device, query: &str) -> bool {
    query.is_empty()
        || device
            .name
            .to_lowercase()
            .contains(query)
        || device
            .id
            .as_str()
            .to_lowercase()
            .contains(query)
        || device_kind(device.kind)
            .to_lowercase()
            .contains(query)
}

fn device_kind(kind: DeviceKind) -> &'static str {
    match kind {
        DeviceKind::Printer => "Printer",
        DeviceKind::Scale => "Scale",
        DeviceKind::Scanner => "Scanner",
        DeviceKind::Other => "Device",
    }
}

fn device_icon(kind: DeviceKind) -> IconName {
    match kind {
        DeviceKind::Printer => IconName::File,
        DeviceKind::Scale => IconName::ChartPie,
        DeviceKind::Scanner => IconName::Frame,
        DeviceKind::Other => IconName::SquareTerminal,
    }
}

fn device_state(state: DeviceState) -> &'static str {
    match state {
        DeviceState::Online => "Online",
        DeviceState::Offline => "Offline",
        DeviceState::Degraded => "Needs attention",
        DeviceState::Blocked => "Blocked",
        DeviceState::Unknown => "Checking",
    }
}

fn state_treatment(state: DeviceState, colors: palette::Palette) -> (gpui::Hsla, IconName) {
    match state {
        DeviceState::Online => (colors.success, IconName::CircleCheck),
        DeviceState::Degraded | DeviceState::Blocked => (colors.danger, IconName::TriangleAlert),
        DeviceState::Offline => (colors.text_muted, IconName::CircleX),
        DeviceState::Unknown => (colors.warning, IconName::Info),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn device() -> Device {
        Device {
            id: DeviceId::parse("dev_front_desk").unwrap(),
            name: "Front desk printer".into(),
            kind: DeviceKind::Printer,
            state: DeviceState::Online,
        }
    }

    #[test]
    fn search_matches_name_kind_and_stable_identifier() {
        let device = device();

        assert!(matches_search(&device, "front desk"));
        assert!(matches_search(&device, "printer"));
        assert!(matches_search(&device, "dev_front"));
        assert!(!matches_search(&device, "scanner"));
    }

    #[test]
    fn empty_search_keeps_every_device_visible() {
        assert!(matches_search(&device(), ""));
    }
}
