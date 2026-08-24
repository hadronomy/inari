//! Surfaces: the elevation ladder every panel and card is built from.
//!
//! Elevation comes from a tonal shift, a self-colored hairline, and a light
//! lip along the top edge — not from a drop shadow. Over glass an ambient
//! shadow reads as grey mud on the frost, and in dark mode a shadow has almost
//! nothing left to darken.

use gpui::{Div, Hsla, ParentElement as _, Styled, div, hsla, px};
use gpui_component::StyledExt as _;

use super::theme::Theme;

/// The content panel: the surface a whole screen sits on.
///
/// Rounded where it meets the chrome, square where it meets the window's own
/// bottom edge. It runs into that edge rather than floating above it, so no
/// second set of corners competes with the window's own.
pub fn panel(theme: &Theme) -> Div {
    div()
        .relative()
        .v_flex()
        .rounded_t(px(Theme::RADIUS_PANEL))
        .bg(theme.surface)
        .border_t_1()
        .border_l_1()
        .border_r_1()
        // Over glass this edge is the only thing between the content and a
        // backdrop we do not control, so it runs firmer there.
        .border_color(if theme.is_glass() { theme.hairline_strong } else { theme.hairline })
        .children(top_lip(theme, Theme::RADIUS_PANEL))
}

/// A card inside a panel: the standard container for a list, a detail pane, or
/// a group of fields.
pub fn card(theme: &Theme) -> Div {
    div()
        .relative()
        .v_flex()
        .rounded(px(Theme::RADIUS_CARD))
        .bg(theme.surface_raised)
        .border_1()
        .border_color(theme.hairline)
        .children(top_lip(theme, Theme::RADIUS_CARD))
}

/// A card that clips its children, for lists whose rows run edge to edge.
pub fn list_card(theme: &Theme) -> Div {
    card(theme).overflow_hidden()
}

/// A hairline of light along a surface's top edge, the way a raised plate
/// catches light from above.
///
/// Dark appearances only. On a light palette the surface is already the
/// brightest thing in its neighbourhood, and adding white to its top edge
/// reads as a seam rather than as lift.
///
/// It stops short of the corners rather than running the full width: a
/// straight line carried into a curve is what makes a highlight look drawn on
/// instead of lit.
fn top_lip(theme: &Theme, radius: f32) -> Option<Div> {
    theme.is_dark().then(|| {
        div()
            .absolute()
            .top_0()
            .left(px(radius))
            .right(px(radius))
            .h(px(1.0))
            .bg(highlight())
    })
}

fn highlight() -> Hsla {
    hsla(0.0, 0.0, 1.0, 0.055)
}
