//! Window chrome: the titlebar, the navigation rail, and the content panel.
//!
//! The shell is one translucent plane. The titlebar and the rail paint nothing
//! of their own — they let the window's frost show through — and the content
//! sits on an inset panel with its own surface. That inset is what makes the
//! glass legible: text never lands directly on a blurred desktop, and the panel
//! reads as a real object resting on the shell.
//!
//! The brand shares the native window controls' optical centerline. The
//! navigation starts on the content panel's top edge below it.

use gpui::{
    Action, AnimationExt as _, Div, InteractiveElement as _, IntoElement, KeyDownEvent,
    ParentElement as _, RenderOnce, SharedString, Stateful, StatefulInteractiveElement as _,
    Styled, Window, div, prelude::FluentBuilder as _, px, svg,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    focus,
    icon::Symbol,
    motion,
    theme::{ActiveTheme as _, Theme},
};

/// Nav item geometry. The rail's sliding indicator is positioned from these,
/// so they are constants rather than inline numbers in two places.
const ITEM_HEIGHT: f32 = 34.0;
const ITEM_GAP: f32 = 2.0;
/// Distance from the rail's edge to an item's icon.
const ITEM_INSET: f32 = Theme::SPACE_SM;
const INDICATOR_HEIGHT: f32 = 16.0;
const INDICATOR_WIDTH: f32 = 3.0;
/// The rail's own inset from the window edge. Wider than [`PANEL_INSET`] so
/// the selection indicator, which sits in the gutter left of an item, still
/// clears the window edge.
const RAIL_INSET: f32 = Theme::SPACE_LG;
/// Gap between an item's icon and its label.
const ITEM_ICON_GAP: f32 = Theme::SPACE_SM + 2.0;
/// Icon column width. Fixed so labels align even though the glyphs differ.
const ITEM_ICON_SIZE: f32 = 16.0;

/// One destination in the navigation rail.
pub struct RailItem {
    pub id: &'static str,
    pub label: SharedString,
    pub symbol: Symbol,
    pub action: Box<dyn Action>,
}

impl RailItem {
    pub fn new(
        id: &'static str,
        label: impl Into<SharedString>,
        symbol: impl Into<Symbol>,
        action: impl Action,
    ) -> Self {
        Self { id, label: label.into(), symbol: symbol.into(), action: Box::new(action) }
    }
}

/// The primary navigation rail.
#[derive(IntoElement)]
pub struct NavigationRail {
    items: Vec<RailItem>,
    active: usize,
    /// Where the indicator starts its slide. Equal to `active` on first paint,
    /// so the rail does not animate into place while the window opens.
    previous: usize,
    enabled: bool,
}

impl NavigationRail {
    pub fn new(items: Vec<RailItem>, active: usize, previous: usize) -> Self {
        Self { items, active, previous, enabled: true }
    }

    /// Dim and deactivate the rail. Used while setup owns the window: the
    /// destinations exist but cannot be reached yet, and hiding them entirely
    /// would make the app look like it has no features.
    pub fn enabled(mut self, enabled: bool) -> Self {
        self.enabled = enabled;
        self
    }

    fn indicator_offset(index: usize) -> f32 {
        index as f32 * (ITEM_HEIGHT + ITEM_GAP) + (ITEM_HEIGHT - INDICATOR_HEIGHT) / 2.0
    }
}

impl RenderOnce for NavigationRail {
    fn render(self, _: &mut Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let active = self.active;
        let enabled = self.enabled;
        let from = Self::indicator_offset(self.previous);
        let to = Self::indicator_offset(active);

        let indicator = div()
            .absolute()
            .left(px(-Theme::SPACE_SM))
            .w(px(INDICATOR_WIDTH))
            .h(px(INDICATOR_HEIGHT))
            .rounded_r(px(INDICATOR_WIDTH / 2.0))
            .bg(theme.accent);
        // Restarting the animation is keyed on `active`: a new id makes GPUI
        // treat this as a fresh element and replay from delta 0, which is what
        // turns a destination change into a slide.
        let indicator = if motion::enabled() && from != to {
            div()
                .child(indicator.with_animation(
                    ("rail-indicator", active),
                    motion::settle(),
                    move |bar, delta| bar.top(px(from + (to - from) * delta)),
                ))
                .into_any_element()
        } else {
            indicator.top(px(to)).into_any_element()
        };

        div()
            .v_flex()
            .w(px(Theme::RAIL_WIDTH))
            .h_full()
            .flex_none()
            .pt(px(PANEL_INSET))
            .pb(px(Theme::SPACE_MD))
            .px(px(RAIL_INSET))
            .child(
                div()
                    .relative()
                    .v_flex()
                    .gap(px(ITEM_GAP))
                    .when(enabled, |list| list.child(indicator))
                    .children(
                        self.items
                            .into_iter()
                            .enumerate()
                            .map(|(index, item)| rail_item(item, index == active, enabled, theme)),
                    ),
            )
    }
}

/// A fixed-width, centered slot for a rail glyph.
///
/// Centering each glyph in the same column keeps all navigation labels on one
/// axis when their source artwork has different bounds.
fn icon_column() -> Div {
    div()
        .flex()
        .flex_none()
        .items_center()
        .justify_center()
        .w(px(ITEM_ICON_SIZE))
}

/// The brand lockup for the titlebar.
///
/// Its full-height box shares the titlebar's alignment context. A macOS-only
/// correction matches the mark and label to the AppKit controls.
pub fn brand_lockup(theme: &Theme) -> Div {
    div()
        .h_flex()
        .items_center()
        .gap(px(ITEM_ICON_GAP))
        .h_full()
        .flex_none()
        // GPUI Component reserves the native control frames. The gap clears
        // the final light. The 2px correction matches AppKit's button center
        // at this custom titlebar height.
        .when(cfg!(target_os = "macos"), |lockup| {
            lockup
                .ml(px(Theme::SPACE_LG))
                .relative()
                .top(px(2.0))
        })
        .child(
            svg()
                .path("inari-mark-torii-ui.svg")
                .size(px(ITEM_ICON_SIZE + 2.0))
                .flex_none()
                .text_color(theme.accent),
        )
        .child(
            div()
                .text_size(px(14.0))
                .font_weight(gpui::FontWeight::SEMIBOLD)
                .text_color(theme.text)
                .child("Inari"),
        )
}

fn rail_item(item: RailItem, active: bool, enabled: bool, theme: &Theme) -> Stateful<Div> {
    let click_action = item.action.boxed_clone();
    let key_action = item.action;
    let color = if !enabled {
        theme.text_tertiary
    } else if active {
        theme.text
    } else {
        theme.text_secondary
    };
    div()
        .id(item.id)
        .h_flex()
        .items_center()
        .gap(px(ITEM_ICON_GAP))
        .h(px(ITEM_HEIGHT))
        .px(px(ITEM_INSET))
        .rounded(px(Theme::RADIUS_CONTROL + 1.0))
        .text_color(color)
        // A transparent border at rest keeps the focus ring from changing the
        // item's size when it appears.
        .border_1()
        .border_color(gpui::transparent_black())
        .when(active, |row| row.bg(theme.wash_selected))
        .when(enabled, |row| {
            row.cursor_pointer()
                .focusable()
                .tab_stop(true)
                .when(focus::visible(), |row| {
                    row.focus(|style| style.border_color(theme.focus_ring))
                })
                .when(!active, |row| {
                    row.on_hover(move |hovered, window, _| {
                        if motion::hover_set(item.id, *hovered) {
                            // Refresh: request_animation_frame panics outside
                            // paint (see hover_set).
                            window.refresh();
                        }
                    })
                    .bg(motion::hover_blend(item.id, theme.wash_hover))
                    .active(|row| row.bg(theme.wash_pressed))
                })
                .on_click(move |_, window, cx| {
                    window.dispatch_action(click_action.boxed_clone(), cx);
                })
                .on_key_down(move |event: &KeyDownEvent, window, cx| {
                    if is_activation(event) {
                        window.dispatch_action(key_action.boxed_clone(), cx);
                        cx.stop_propagation();
                    }
                })
        })
        .child(
            icon_column().child(
                Icon::from(item.symbol)
                    .size(px(ITEM_ICON_SIZE))
                    .flex_none(),
            ),
        )
        .child(
            div()
                .text_body()
                .when(active, |label| label.font_weight(gpui::FontWeight::MEDIUM))
                .child(item.label),
        )
}

/// Whether a key event should activate the focused control.
///
/// Enter and Space, the two keys every desktop toolkit treats as activation.
/// Without this a keyboard user can reach a rail item and then have no way to
/// open it.
pub fn is_activation(event: &KeyDownEvent) -> bool {
    matches!(event.keystroke.key.as_str(), "enter" | "space")
        && !event.keystroke.modifiers.modified()
}

/// The inset the content panel keeps from the window edge.
///
/// Matches the rail's own padding, so the gap on the panel's left, right, and
/// bottom is the same measure all the way round.
pub const PANEL_INSET: f32 = Theme::SPACE_MD;

/// The inset panel the content sits on.
///
/// Always the same size for a given window: it fills the space beside the rail
/// whatever the screen inside it contains, so moving between destinations
/// never resizes the surface under the operator's eyes.
pub fn content_panel(theme: &Theme) -> Div {
    super::surface::panel(theme)
        .flex_1()
        .h_full()
        .min_w(px(0.0))
        .overflow_hidden()
        // Inset from the chrome above and the window edge to its right. The
        // rail's padding carries the left; the bottom runs into the window
        // edge, which is why the panel squares off down there.
        .mt(px(PANEL_INSET))
        .mr(px(PANEL_INSET))
}
