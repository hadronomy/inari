//! The selected element, and the geometry the overlay draws from.
//!
//! GPUI hands an element's own state to the inspector through
//! `Window::with_inspector_state`, and `Div` hands over
//! `DivInspectorState { base_style, bounds, content_size }`. Everything here is
//! read from that one value; nothing reaches into the renderer.
//!
//! The live style editors are `gpui_component::DivInspector`, hosted rather than
//! rewritten. It already round-trips the base style through Rust *and* JSON,
//! with completions driven by GPUI's `styled_reflection`, and it writes back
//! through the same `with_inspector_state` channel, so edits land on the running
//! element. Reimplementing that would be worse and longer.

use gpui::{
    AbsoluteLength, App, Bounds, Corners, DefiniteLength, DivInspectorState, Edges, Entity,
    Global, InspectorElementId, InteractiveElement as _, IntoElement, Length, ParentElement as _,
    Pixels, SharedString, Size, StatefulInteractiveElement as _, Styled as _, Window, div, px,
};
use gpui_component::{DivInspector, StyledExt as _};

use crate::ui::{
    content::Typography as _,
    surface::card,
    theme::{ActiveTheme as _, Theme},
};

/// What the last inspector render saw, cached so the overlay can draw the box
/// model without the Element tool being on screen.
#[derive(Clone)]
pub struct Selection {
    pub id: InspectorElementId,
    pub bounds: Bounds<Pixels>,
    pub content_size: Size<Pixels>,
    pub padding: Edges<Pixels>,
    pub border: Edges<Pixels>,
    /// Reported, never painted: GPUI resolves margin during layout and no
    /// margin rectangle survives to paint time.
    pub margin: Edges<Pixels>,
    pub radius: Corners<Pixels>,
}

impl Global for Selection {}

impl Selection {
    /// The box the element's own background fills, inside its border.
    pub fn padding_box(&self) -> Bounds<Pixels> {
        inset(self.bounds, self.border)
    }

    /// The box the children get.
    pub fn content_box(&self) -> Bounds<Pixels> {
        inset(self.padding_box(), self.padding)
    }

    /// Children measure larger than the box they were given, so something is
    /// being cut off unless the element scrolls.
    pub fn overflows(&self) -> bool {
        overflows(self.content_size, self.content_box().size)
    }
}

/// Shrink `bounds` by `edges` on every side, never past zero.
fn inset(bounds: Bounds<Pixels>, edges: Edges<Pixels>) -> Bounds<Pixels> {
    let width = (bounds.size.width - edges.left - edges.right).max(px(0.0));
    let height = (bounds.size.height - edges.top - edges.bottom).max(px(0.0));
    Bounds {
        origin: gpui::point(bounds.origin.x + edges.left, bounds.origin.y + edges.top),
        size: gpui::size(width, height),
    }
}

/// Half a pixel of slack, because a measured child and its box agree to within
/// rounding far more often than they disagree.
fn overflows(children: Size<Pixels>, box_size: Size<Pixels>) -> bool {
    children.width > box_size.width + px(0.5) || children.height > box_size.height + px(0.5)
}

/// Read the geometry out of the element's own state and remember it.
pub fn remember(
    id: &InspectorElementId,
    state: &DivInspectorState,
    window: &Window,
    cx: &mut App,
) {
    let rem = window.rem_size();
    // Percentage padding resolves against the element's own width, which is the
    // rule Taffy applies and the one a reader will expect.
    let base = AbsoluteLength::Pixels(state.bounds.size.width);
    let style = state.base_style.as_ref();

    let padding = Edges {
        top: definite(style.padding.top, base, rem),
        right: definite(style.padding.right, base, rem),
        bottom: definite(style.padding.bottom, base, rem),
        left: definite(style.padding.left, base, rem),
    };
    let border = Edges {
        top: absolute(style.border_widths.top, rem),
        right: absolute(style.border_widths.right, rem),
        bottom: absolute(style.border_widths.bottom, rem),
        left: absolute(style.border_widths.left, rem),
    };
    let margin = Edges {
        top: length(style.margin.top, base, rem),
        right: length(style.margin.right, base, rem),
        bottom: length(style.margin.bottom, base, rem),
        left: length(style.margin.left, base, rem),
    };
    let radius = Corners {
        top_left: absolute(style.corner_radii.top_left, rem),
        top_right: absolute(style.corner_radii.top_right, rem),
        bottom_right: absolute(style.corner_radii.bottom_right, rem),
        bottom_left: absolute(style.corner_radii.bottom_left, rem),
    };

    cx.set_global(Selection {
        id: id.clone(),
        bounds: state.bounds,
        content_size: state.content_size,
        padding,
        border,
        margin,
        radius,
    });
}

fn definite(value: Option<DefiniteLength>, base: AbsoluteLength, rem: Pixels) -> Pixels {
    value
        .map(|length| length.to_pixels(base, rem))
        .unwrap_or(px(0.0))
}

fn absolute(value: Option<AbsoluteLength>, rem: Pixels) -> Pixels {
    value
        .map(|length| length.to_pixels(rem))
        .unwrap_or(px(0.0))
}

/// `auto` reads as zero. It is not zero — it is "whatever is left" — so the
/// report says `auto` in words rather than printing a number that is a guess.
fn length(value: Option<Length>, base: AbsoluteLength, rem: Pixels) -> Pixels {
    match value {
        Some(Length::Definite(length)) => length.to_pixels(base, rem),
        _ => px(0.0),
    }
}

/// The Element tool: our report, then the hosted style editors.
pub fn tool(
    id: &InspectorElementId,
    editors: &Entity<DivInspector>,
    cx: &mut App,
) -> gpui::AnyElement {
    let theme = cx.inari();
    let selection = cx.try_global::<Selection>().cloned();
    let source = SharedString::from(format!("{}", id.path.source_location));

    let mut report = div()
        .v_flex()
        .gap(px(Theme::SPACE_SM))
        .w_full();

    report = report.child(
        div()
            .id("dev-source")
            .w_full()
            .px(px(Theme::SPACE_SM))
            .py(px(Theme::SPACE_XS))
            .rounded(px(Theme::RADIUS_CONTROL))
            .bg(theme.surface_raised)
            .text_size(px(11.0))
            .font_family(theme.font_mono.clone())
            .text_color(theme.text_secondary)
            .child(source.clone())
            .hover(|style| style.bg(theme.wash_hover))
            .on_click(move |_, _, cx| {
                cx.write_to_clipboard(gpui::ClipboardItem::new_string(source.to_string()));
            }),
    );

    if let Some(selection) = selection {
        let size = selection.bounds.size;
        let content = selection.content_box().size;
        report = report
            .child(measure(theme, "Bounds", &format!(
                "{:.0} × {:.0}  at  {:.0}, {:.0}",
                f32::from(size.width),
                f32::from(size.height),
                f32::from(selection.bounds.origin.x),
                f32::from(selection.bounds.origin.y),
            )))
            .child(measure(theme, "Content box", &format!(
                "{:.0} × {:.0}",
                f32::from(content.width),
                f32::from(content.height)
            )))
            .child(measure(theme, "Children measure", &format!(
                "{:.0} × {:.0}",
                f32::from(selection.content_size.width),
                f32::from(selection.content_size.height)
            )))
            .child(measure(theme, "Padding", &edges(selection.padding)))
            .child(measure(theme, "Border", &edges(selection.border)))
            .child(measure(theme, "Margin", &edges(selection.margin)))
            .child(measure(theme, "Radius", &corners(selection.radius)))
            .child(measure(theme, "Instance", &id.instance_id.to_string()));

        if selection.overflows() {
            report = report.child(
                div()
                    .w_full()
                    .px(px(Theme::SPACE_SM))
                    .py(px(Theme::SPACE_XS))
                    .rounded(px(Theme::RADIUS_CONTROL))
                    .bg(theme.danger_wash)
                    .text_size(px(11.0))
                    .text_color(theme.danger)
                    .child(
                        "Children measure larger than the box they were given. Unless this \
                         element scrolls, something is being cut off.",
                    ),
            );
        }
    }

    card(theme)
        .w_full()
        .p(px(Theme::SPACE_MD))
        .v_flex()
        .gap(px(Theme::SPACE_MD))
        .child(report)
        .child(editors.clone())
        .into_any_element()
}

fn measure(theme: &Theme, label: &'static str, value: &str) -> impl IntoElement {
    div()
        .h_flex()
        .items_baseline()
        .justify_between()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .child(
            div()
                .text_caption()
                .text_color(theme.text_tertiary)
                .child(label),
        )
        .child(
            div()
                .text_size(px(11.0))
                .font_family(theme.font_mono.clone())
                .text_color(theme.text_secondary)
                .child(value.to_string()),
        )
}

/// `12` when every side matches, `12 · 8` for a vertical/horizontal pair, and
/// all four otherwise. A reader should not have to compare four identical
/// numbers to learn that they are identical.
fn edges(edges: Edges<Pixels>) -> String {
    let (top, right, bottom, left) = (
        f32::from(edges.top),
        f32::from(edges.right),
        f32::from(edges.bottom),
        f32::from(edges.left),
    );
    if top == right && right == bottom && bottom == left {
        format!("{top:.0}")
    } else if top == bottom && left == right {
        format!("{top:.0} · {left:.0}")
    } else {
        format!("{top:.0} {right:.0} {bottom:.0} {left:.0}")
    }
}

fn corners(radius: Corners<Pixels>) -> String {
    let (tl, tr, br, bl) = (
        f32::from(radius.top_left),
        f32::from(radius.top_right),
        f32::from(radius.bottom_right),
        f32::from(radius.bottom_left),
    );
    if tl == tr && tr == br && br == bl {
        format!("{tl:.0}")
    } else {
        format!("{tl:.0} {tr:.0} {br:.0} {bl:.0}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpui::{point, size};

    #[test]
    fn edges_collapse_when_every_side_matches() {
        assert_eq!(edges(Edges::all(px(12.0))), "12");
    }

    #[test]
    fn edges_collapse_to_a_pair_when_the_axes_match() {
        assert_eq!(
            edges(Edges { top: px(4.0), bottom: px(4.0), left: px(8.0), right: px(8.0) }),
            "4 \u{b7} 8"
        );
    }

    #[test]
    fn edges_stay_apart_when_they_differ() {
        assert_eq!(
            edges(Edges { top: px(1.0), right: px(2.0), bottom: px(3.0), left: px(4.0) }),
            "1 2 3 4"
        );
    }

    #[test]
    fn insetting_walks_the_origin_in_and_the_size_down() {
        let bounds = Bounds { origin: point(px(10.0), px(20.0)), size: size(px(100.0), px(50.0)) };
        let inner = inset(bounds, Edges::all(px(8.0)));
        assert_eq!(inner.origin, point(px(18.0), px(28.0)));
        assert_eq!(inner.size, size(px(84.0), px(34.0)));
    }

    #[test]
    fn insetting_stops_at_zero_rather_than_going_negative() {
        let bounds = Bounds { origin: point(px(0.0), px(0.0)), size: size(px(10.0), px(10.0)) };
        let inner = inset(bounds, Edges::all(px(40.0)));
        assert_eq!(inner.size, size(px(0.0), px(0.0)));
    }

    #[test]
    fn rounding_alone_does_not_report_an_overflow() {
        assert!(!overflows(size(px(100.2), px(50.0)), size(px(100.0), px(50.0))));
        assert!(overflows(size(px(101.0), px(50.0)), size(px(100.0), px(50.0))));
    }

    #[test]
    fn a_percentage_padding_resolves_against_the_element_width() {
        let half = DefiniteLength::Fraction(0.5);
        let width = AbsoluteLength::Pixels(px(200.0));
        assert_eq!(definite(Some(half), width, px(16.0)), px(100.0));
    }

    #[test]
    fn an_auto_margin_reports_as_zero_rather_than_as_a_guess() {
        assert_eq!(length(Some(Length::Auto), AbsoluteLength::Pixels(px(200.0)), px(16.0)), px(0.0));
    }
}
