use std::sync::Arc;

use gpui::{
    AppContext as _, Context, Entity, InteractiveElement as _, IntoElement, ParentElement as _,
    Render, StatefulInteractiveElement as _, Styled, Subscription, Window, div,
    prelude::FluentBuilder as _, rems,
};
use gpui_component::{
    StyledExt as _,
    input::{Input, InputEvent, InputState},
};
use inari_agent_client::{Device, DeviceId, DeviceKind, DeviceState};

use crate::ui::{PageHeader, palette};

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
                .placeholder("Search by device name, kind, or identifier")
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

        div()
            .v_flex()
            .gap(rems(1.35))
            .max_w(rems(78.))
            .mx_auto()
            .px(rems(2.5))
            .py(rems(2.25))
            .child(PageHeader::new(
                "Devices",
                "Find connected hardware and understand its current state.",
            ))
            .child(
                div()
                    .max_w(rems(30.))
                    .child(Input::new(&self.search).cleanable(true)),
            )
            .child(
                div()
                    .flex()
                    .flex_wrap()
                    .items_start()
                    .gap(rems(0.85))
                    .child(
                        div()
                            .id("device-directory")
                            .min_w(rems(24.))
                            .flex_1()
                            .v_flex()
                            .rounded(rems(1.))
                            .border_1()
                            .border_color(colors.border)
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
                            .min_w(rems(18.))
                            .w(rems(23.))
                            .flex_1()
                            .child(selected.map_or_else(
                                || empty_detail(colors),
                                |device| device_detail(device, colors),
                            )),
                    ),
            )
    }
}

fn device_row(
    device: Device,
    index: usize,
    selected: bool,
    colors: palette::Palette,
) -> gpui::Stateful<gpui::Div> {
    let kind = device_kind(device.kind);
    div()
        .id(("device", index))
        .flex()
        .items_center()
        .gap(rems(1.))
        .px(rems(1.15))
        .py(rems(1.))
        .cursor_pointer()
        .when(index > 0, |row| {
            row.border_t_1()
                .border_color(colors.border)
        })
        .when(selected, |row| row.bg(colors.blue_wash))
        .hover(|row| row.bg(colors.surface_raised))
        .child(
            div()
                .size(rems(2.35))
                .rounded(rems(0.7))
                .bg(colors.surface_raised)
                .flex()
                .items_center()
                .justify_center()
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .child(
                    kind.chars()
                        .next()
                        .unwrap_or('D')
                        .to_string(),
                ),
        )
        .child(
            div()
                .v_flex()
                .gap(rems(0.15))
                .child(
                    div()
                        .font_weight(gpui::FontWeight::MEDIUM)
                        .child(device.name),
                )
                .child(
                    div()
                        .text_size(rems(0.78))
                        .text_color(colors.text_muted)
                        .child(kind),
                ),
        )
        .child(
            div()
                .ml_auto()
                .text_size(rems(0.8))
                .text_color(state_color(device.state, colors))
                .child(device_state(device.state)),
        )
}

fn device_detail(device: Device, colors: palette::Palette) -> gpui::Div {
    div()
        .v_flex()
        .gap(rems(1.15))
        .p(rems(1.25))
        .rounded(rems(1.))
        .border_1()
        .border_color(colors.border)
        .bg(colors.surface)
        .child(
            div()
                .v_flex()
                .gap(rems(0.25))
                .child(
                    div()
                        .text_size(rems(1.2))
                        .font_weight(gpui::FontWeight::SEMIBOLD)
                        .child(device.name),
                )
                .child(
                    div()
                        .text_size(rems(0.8))
                        .text_color(colors.text_muted)
                        .child(device_kind(device.kind)),
                ),
        )
        .child(detail_row("Status", device_state(device.state), colors))
        .child(detail_row("Device ID", device.id.to_string(), colors))
        .child(
            div()
                .pt(rems(0.4))
                .text_size(rems(0.78))
                .line_height(rems(1.2))
                .text_color(colors.text_muted)
                .child("The stable device ID is the value integrations should use."),
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
                .text_size(rems(0.7))
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .text_color(colors.text_muted)
                .child(label.to_uppercase()),
        )
        .child(
            div()
                .text_size(rems(0.82))
                .font_weight(gpui::FontWeight::MEDIUM)
                .child(value.into()),
        )
}

fn empty_directory(no_devices: bool, colors: palette::Palette) -> gpui::Div {
    div()
        .p(rems(1.25))
        .text_color(colors.text_muted)
        .child(if no_devices {
            "No devices have been discovered yet."
        } else {
            "No devices match this search."
        })
}

fn empty_detail(colors: palette::Palette) -> gpui::Div {
    div()
        .p(rems(1.25))
        .rounded(rems(1.))
        .border_1()
        .border_color(colors.border)
        .text_color(colors.text_muted)
        .child("Select a device to see its operational details.")
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

fn device_state(state: DeviceState) -> &'static str {
    match state {
        DeviceState::Online => "Online",
        DeviceState::Offline => "Offline",
        DeviceState::Degraded => "Needs attention",
        DeviceState::Blocked => "Blocked",
        DeviceState::Unknown => "Checking",
    }
}

fn state_color(state: DeviceState, colors: palette::Palette) -> gpui::Hsla {
    match state {
        DeviceState::Online => colors.green,
        DeviceState::Degraded | DeviceState::Blocked => colors.vermilion,
        DeviceState::Offline | DeviceState::Unknown => colors.text_muted,
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
