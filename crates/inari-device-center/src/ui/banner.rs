//! Banners: a state that needs a sentence and sometimes an action.
//!
//! The title takes the tone color and the body stays in normal text. Tinting
//! both puts every word of the explanation at the tone's contrast ratio, which
//! is the point where a warning becomes harder to read than the thing it warns
//! about.
//!
//! The surface is frost built from layers, not a flat tinted rectangle: a tonal
//! wash, a light fall from the top edge, an edge in the tone's own color, and a
//! lit lip where a raised plate would catch the light. GPUI 0.2.2 blurs only
//! what is behind the whole window, so a banner cannot blur the panel it sits
//! on. What it can do is carry the same light as the window frost around it,
//! which is what makes it read as part of the same material.

use gpui::{
    AnimationExt as _, AnyElement, Hsla, InteractiveElement as _, IntoElement, ParentElement as _,
    RenderOnce, SharedString, Styled, Transformation, div, hsla, linear_color_stop,
    linear_gradient, percentage, px,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    icon::Symbol,
    motion,
    status::Tone,
    theme::{ActiveTheme as _, Theme},
};

#[derive(IntoElement)]
pub struct Banner {
    id: &'static str,
    tone: Tone,
    title: SharedString,
    detail: SharedString,
    action: Option<AnyElement>,
}

impl Banner {
    pub fn new(
        id: &'static str,
        tone: Tone,
        title: impl Into<SharedString>,
        detail: impl Into<SharedString>,
    ) -> Self {
        Self { id, tone, title: title.into(), detail: detail.into(), action: None }
    }

    /// A recovery control, placed on the banner's trailing edge.
    pub fn action(mut self, action: impl IntoElement) -> Self {
        self.action = Some(action.into_any_element());
        self
    }
}

impl RenderOnce for Banner {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let color = self.tone.color(theme);
        let dark = theme.is_dark();

        div()
            .id(self.id)
            .relative()
            .h_flex()
            .items_start()
            .gap(px(Theme::SPACE_MD))
            .w_full()
            .p(px(Theme::SPACE_MD + 2.0))
            .rounded(px(Theme::RADIUS_CARD))
            .bg(self.tone.wash(theme))
            .border_1()
            // The edge is the tone's own color rather than a grey rule, so the
            // banner reads as one tinted object instead of a grey box that
            // happens to be filled.
            .border_color(Hsla { a: 0.22, ..color })
            .child(frost(dark))
            .children(top_lip(dark))
            .child(glyph(self.tone, color))
            .child(
                div()
                    .relative()
                    .v_flex()
                    .flex_1()
                    .gap(px(2.0))
                    .child(
                        div()
                            .text_body()
                            .font_weight(gpui::FontWeight::SEMIBOLD)
                            .text_color(color)
                            .child(self.title),
                    )
                    .child(
                        div()
                            .text_body()
                            .text_color(theme.text_secondary)
                            .child(self.detail),
                    ),
            )
            .children(self.action.map(|action| {
                div()
                    .relative()
                    .flex_none()
                    .child(action)
            }))
    }
}

/// The tone glyph, bare and unboxed.
///
/// A loader is the one glyph here that makes a claim about right now, so it
/// turns while the work it stands for is running. The others are labels for a
/// settled state and hold still.
fn glyph(tone: Tone, color: Hsla) -> AnyElement {
    let icon = Icon::from(Symbol::Component(tone.symbol()))
        .size(px(16.0))
        .text_color(color);
    // The wrapper carries the offset so both branches sit on the same baseline
    // as the title beside them, whether or not the glyph is turning.
    let slot = div().relative().flex_none().mt(px(1.0));
    if tone == Tone::Busy && motion::enabled() {
        slot.child(icon.with_animation("banner-loader", motion::spin(), |icon, delta| {
            icon.transform(Transformation::rotate(percentage(delta)))
        }))
        .into_any_element()
    } else {
        slot.child(icon).into_any_element()
    }
}

/// Light falling from the top edge, the way it does through real frost.
///
/// Dark appearances only. On a light palette the banner is already brighter
/// than the panel under it, and adding white to its top reads as fog rather
/// than as depth.
fn frost(dark: bool) -> gpui::Div {
    let strength = if dark { 0.05 } else { 0.0 };
    div()
        .absolute()
        .inset_0()
        .rounded(px(Theme::RADIUS_CARD))
        .bg(linear_gradient(
            180.0,
            linear_color_stop(hsla(0.0, 0.0, 1.0, strength), 0.0),
            linear_color_stop(hsla(0.0, 0.0, 1.0, 0.0), 0.62),
        ))
}

/// A hairline of light along the top edge, stopping short of the corners.
///
/// The same lip the surface ladder uses, so a banner and a card catch the light
/// from the same direction. A straight line carried into the curve is what
/// makes a highlight look drawn on instead of lit.
fn top_lip(dark: bool) -> Option<gpui::Div> {
    dark.then(|| {
        div()
            .absolute()
            .top_0()
            .left(px(Theme::RADIUS_CARD))
            .right(px(Theme::RADIUS_CARD))
            .h(px(1.0))
            .bg(hsla(0.0, 0.0, 1.0, 0.07))
    })
}
