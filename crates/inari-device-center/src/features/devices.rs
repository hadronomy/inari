//! The device directory: find a device, then read its state.
//!
//! The list and the detail pane share one selection. On a narrow window the
//! detail pane drops below the list rather than squeezing beside it, because a
//! device name truncated to fit two columns is worse than one column of names
//! you can actually read.

use std::sync::Arc;

use gpui::{
    AppContext as _, Context, Entity, InteractiveElement as _, IntoElement, KeyDownEvent,
    ParentElement as _, Render, StatefulInteractiveElement as _, Styled, Subscription, Window, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::{
    Icon, IconName, StyledExt as _,
    input::{Input, InputEvent, InputState},
};
use inari_agent_client::{Device, DeviceId, DeviceKind};

/// The detail pane's width. Fixed, so selecting a device with a long name or
/// clearing the selection entirely never resizes the list beside it.
const DETAIL_WIDTH: f32 = 320.0;
/// The height both columns hold whatever they contain, so the page does not
/// reflow as devices appear, disappear, or get filtered out.
const COLUMN_MIN_HEIGHT: f32 = 300.0;
/// The width the list asks for before the detail pane drops below it.
///
/// This is the list's flex *basis*, not a minimum. A minimum wide enough to
/// matter leaves a band of window widths where the row is too wide to fit and
/// still too narrow to wrap, and the detail pane gets clipped by the panel
/// edge. Driving the wrap from the basis and letting the list shrink freely
/// means the row always either fits or stacks.
const LIST_BASIS_WIDTH: f32 = 260.0;

use crate::ui::{
    chrome::is_activation,
    content::{EmptyState, Field, PageTitle, Section, Typography as _, page, row_divider},
    focus,
    icon::{Glyph, Symbol},
    motion,
    status::{Status, StatusChip, StatusDot},
    surface::{card, list_card},
    theme::{ActiveTheme as _, Theme},
};

pub struct DeviceDirectory {
    devices: Arc<[Device]>,
    search: Entity<InputState>,
    selected: Option<DeviceId>,
    /// Owned by the list so Up and Down move the selection when the list has
    /// focus, without stealing those keys from the search field.
    list_focus: gpui::FocusHandle,
    _search_subscription: Subscription,
}

impl DeviceDirectory {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let search = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("Search by name, kind, or ID")
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
            list_focus: cx.focus_handle(),
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

    /// Select `id`, whether the click came from this screen or from an
    /// attention item on Overview.
    pub fn select(&mut self, id: DeviceId, cx: &mut Context<Self>) {
        self.selected = Some(id);
        cx.notify();
    }

    /// Move the selection `delta` rows through the currently visible devices.
    ///
    /// Clamped rather than wrapping: an operator holding Down to reach the end
    /// of a long list should stop there, not silently return to the top and
    /// start reading the same names again.
    fn select_relative(&mut self, delta: isize, visible: &[Device], cx: &mut Context<Self>) {
        if visible.is_empty() {
            return;
        }
        let current = self.selected.as_ref().and_then(|id| {
            visible
                .iter()
                .position(|device| &device.id == id)
        });
        let next = match current {
            Some(index) => (index as isize + delta).clamp(0, visible.len() as isize - 1) as usize,
            None if delta > 0 => 0,
            None => visible.len() - 1,
        };
        self.select(visible[next].id.clone(), cx);
    }
}

impl Render for DeviceDirectory {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        // Keep the hover fades in this view's rows walking; see the root
        // view for the other half of the loop.
        if motion::hover_fades_live() {
            window.request_animation_frame();
        }
        let theme = cx.inari();
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
        let searching = !query.is_empty();
        let no_devices = self.devices.is_empty();

        page("devices")
            .child(PageTitle::new("Devices", "Hardware this computer's agent can reach."))
            .child(
                Section::new("Directory")
                    .aside(
                        div()
                            .w(px(260.0))
                            .child(Input::new(&self.search).cleanable(true)),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_wrap()
                            .items_start()
                            .gap(px(Theme::SPACE_MD))
                            .w_full()
                            .child(
                                list_card(theme)
                                    .id("device-list")
                                    .flex_grow()
                                    .flex_shrink()
                                    .flex_basis(px(LIST_BASIS_WIDTH))
                                    .min_w(px(0.0))
                                    .min_h(px(COLUMN_MIN_HEIGHT))
                                    .track_focus(&self.list_focus)
                                    .on_key_down(cx.listener({
                                        let visible = visible.clone();
                                        move |directory, event: &KeyDownEvent, _, cx| {
                                            let delta = match event.keystroke.key.as_str() {
                                                "down" => 1,
                                                "up" => -1,
                                                _ => return,
                                            };
                                            directory.select_relative(delta, &visible, cx);
                                            cx.stop_propagation();
                                        }
                                    }))
                                    .when(visible.is_empty(), |list| {
                                        list.child(empty_directory(no_devices, searching))
                                    })
                                    .children(visible.iter().cloned().enumerate().map(
                                        |(index, device)| {
                                            let active = self.selected.as_ref() == Some(&device.id);
                                            let id = device.id.clone();
                                            let key_id = device.id.clone();
                                            div()
                                                .v_flex()
                                                .w_full()
                                                .when(index > 0, |row| {
                                                    row.child(row_divider(theme))
                                                })
                                                .child(
                                                    device_row(device, active, theme)
                                                        // A hovered or selected row is a
                                                        // full-bleed wash, and the card's
                                                        // rectangular mask cannot round it:
                                                        // the first and last rows carry the
                                                        // card's own corner curve, or their
                                                        // washes square off the card.
                                                        .when(index == 0, |row| {
                                                            row.rounded_t(px(Theme::RADIUS_CARD))
                                                        })
                                                        .when(
                                                            index == visible.len() - 1,
                                                            |row| {
                                                                row.rounded_b(px(
                                                                    Theme::RADIUS_CARD,
                                                                ))
                                                            },
                                                        )
                                                        .on_click(cx.listener(
                                                            move |directory, _, _, cx| {
                                                                directory.select(id.clone(), cx);
                                                            },
                                                        ))
                                                        .on_key_down(cx.listener(
                                                            move |directory,
                                                                  event: &KeyDownEvent,
                                                                  _,
                                                                  cx| {
                                                                if is_activation(event) {
                                                                    directory.select(
                                                                        key_id.clone(),
                                                                        cx,
                                                                    );
                                                                    cx.stop_propagation();
                                                                }
                                                            },
                                                        )),
                                                )
                                        },
                                    )),
                            )
                            .child(
                                div()
                                    .w(px(DETAIL_WIDTH))
                                    .flex_none()
                                    .child(selected.map_or_else(
                                        || {
                                            card(theme)
                                                .w_full()
                                                .min_h(px(COLUMN_MIN_HEIGHT))
                                                .child(no_selection())
                                                .into_any_element()
                                        },
                                        |device| device_detail(device, cx).into_any_element(),
                                    )),
                            ),
                    ),
            )
    }
}

fn device_row(device: Device, active: bool, theme: &Theme) -> gpui::Stateful<gpui::Div> {
    let status = Status::device(device.state);
    let tone = status.tone;
    let fade_key = gpui::SharedString::from(format!("device-{}", device.id));
    div()
        .id(fade_key.clone())
        .relative()
        .h_flex()
        .items_center()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .px(px(Theme::SPACE_MD + 2.0))
        .py(px(Theme::SPACE_MD))
        .cursor_pointer()
        .focusable()
        .tab_stop(true)
        .border_1()
        .border_color(gpui::transparent_black())
        .when(focus::visible(), |row| row.focus(|style| style.border_color(theme.focus_ring)))
        .when(active, |row| row.bg(theme.wash_selected))
        .when(!active, |row| {
            row.on_hover({
                let fade_key = fade_key.clone();
                move |hovered, window, _| {
                    if motion::hover_set(fade_key.clone(), *hovered) {
                        // Refresh: request_animation_frame panics outside
                        // paint (see hover_set).
                        window.refresh();
                    }
                }
            })
            .bg(motion::hover_blend(fade_key, theme.wash_hover))
            .active(|row| row.bg(theme.wash_pressed))
        })
        // Selection is carried by an accent edge as well as the wash, so it
        // survives a grayscale screenshot and Differentiate Without Color.
        .when(active, |row| {
            row.child(
                div()
                    .absolute()
                    .left(px(0.0))
                    .top(px(8.0))
                    .bottom(px(8.0))
                    .w(px(3.0))
                    .rounded_r(px(1.5))
                    .bg(theme.accent),
            )
        })
        .child(
            Icon::from(device_symbol(device.kind))
                .size(px(17.0))
                .flex_none()
                .text_color(theme.text_secondary),
        )
        .child(
            div()
                .v_flex()
                .flex_1()
                .min_w(px(0.0))
                .gap(px(1.0))
                .child(
                    div()
                        .text_body()
                        .font_weight(gpui::FontWeight::MEDIUM)
                        .child(device.name),
                )
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_secondary)
                        .child(device_kind(device.kind)),
                ),
        )
        .child(
            div()
                .h_flex()
                .items_center()
                .gap(px(Theme::SPACE_SM))
                .flex_none()
                .child(StatusDot::new(tone).size(7.0))
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_secondary)
                        .child(status.label),
                ),
        )
}

fn device_detail(device: Device, cx: &gpui::App) -> impl IntoElement {
    let theme = cx.inari();
    let status = Status::device(device.state);
    card(theme)
        .gap(px(Theme::SPACE_LG))
        .w_full()
        .min_h(px(COLUMN_MIN_HEIGHT))
        .p(px(Theme::SPACE_LG))
        .child(
            div()
                .h_flex()
                .items_start()
                .justify_between()
                .gap(px(Theme::SPACE_MD))
                .child(
                    div()
                        .h_flex()
                        .items_center()
                        .gap(px(Theme::SPACE_MD))
                        .min_w(px(0.0))
                        .child(
                            Icon::from(device_symbol(device.kind))
                                .size(px(22.0))
                                .flex_none()
                                .text_color(theme.text_secondary),
                        )
                        .child(
                            div()
                                .v_flex()
                                .min_w(px(0.0))
                                .gap(px(1.0))
                                .child(div().text_heading().child(device.name))
                                .child(
                                    div()
                                        .text_caption()
                                        .text_color(theme.text_secondary)
                                        .child(device_kind(device.kind)),
                                ),
                        ),
                )
                .child(StatusChip::new(status.clone())),
        )
        .child(
            div()
                .text_body()
                .text_color(theme.text_secondary)
                .child(status.detail),
        )
        .child(Field::new("Device ID", device.id.to_string()).technical())
        .child(
            div()
                .text_caption()
                .text_color(theme.text_tertiary)
                .child("Use the device ID when an integration or an administrator asks for it."),
        )
}

fn no_selection() -> impl IntoElement {
    EmptyState::new(
        Glyph::Device,
        "No device selected",
        "Choose a device to read its state and identifier.",
    )
}

fn empty_directory(no_devices: bool, searching: bool) -> impl IntoElement {
    if no_devices {
        EmptyState::new(
            Glyph::Device,
            "No devices found",
            "The agent has not found any hardware yet. Check that devices are powered on and connected.",
        )
    } else if searching {
        EmptyState::new(
            Symbol::Component(IconName::Search),
            "No matches",
            "No device matches this search. Clear it to see every device.",
        )
    } else {
        EmptyState::new(Glyph::Device, "No devices found", "The agent reported no devices.")
    }
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

pub(crate) fn device_kind(kind: DeviceKind) -> &'static str {
    match kind {
        DeviceKind::Printer => "Printer",
        DeviceKind::Scale => "Scale",
        DeviceKind::Scanner => "Scanner",
        DeviceKind::Other => "Device",
    }
}

pub(crate) fn device_symbol(kind: DeviceKind) -> Symbol {
    match kind {
        DeviceKind::Printer => Glyph::Printer.into(),
        DeviceKind::Scale => Glyph::Scale.into(),
        DeviceKind::Scanner => Glyph::Scanner.into(),
        DeviceKind::Other => Glyph::Device.into(),
    }
}

#[cfg(test)]
mod tests {
    use inari_agent_client::DeviceState;

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

    #[test]
    fn every_device_kind_has_its_own_glyph() {
        let kinds =
            [DeviceKind::Printer, DeviceKind::Scale, DeviceKind::Scanner, DeviceKind::Other];
        for (index, kind) in kinds.iter().enumerate() {
            for other in &kinds[index + 1..] {
                assert_ne!(icon_path(device_symbol(*kind)), icon_path(device_symbol(*other)));
            }
        }
    }

    fn icon_path(symbol: Symbol) -> String {
        match symbol {
            Symbol::House(glyph) => glyph.path().to_string(),
            Symbol::Component(_) => "component".into(),
        }
    }
}
