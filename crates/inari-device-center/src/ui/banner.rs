//! Banners: a state that needs a sentence and sometimes an action.
//!
//! One box for every message is the thing this component used to get wrong. A
//! healthy service, a computer waiting to be enrolled, and an enrollment that
//! failed all arrived as the same bordered rectangle at the same weight, which
//! flattens severity into a single shape and leaves the operator to read every
//! one of them to find out which mattered.
//!
//! So a banner has two registers and the tone picks between them.
//!
//! A **notice** carries a state that blocks nothing: healthy, idle, working.
//! It has no container at all — a status dot and two lines, sitting directly on
//! whatever surface it is on, in the same shape the gate uses to report the
//! agent. Nothing is wrong, so nothing draws a box.
//!
//! An **alert** carries a state that stops device work. It gets a contained
//! surface, because it should stop the eye too: a tonal wash, the lit lip the
//! surface ladder uses, and a solid rule down the leading edge in the tone's
//! own colour. The rule replaces the outline rather than decorating it — the
//! edge that carries the severity is the only edge drawn, so an alert does not
//! read as one more card in a column of cards.
//!
//! Neither register animates. The glyphs here label a settled state, and a
//! spinner beside "this computer is not connected" claims work that nobody is
//! doing.

use gpui::{
    AnyElement, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce, SharedString,
    Styled, div, hsla, px,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    icon::Symbol,
    status::{StatusDot, Tone},
    theme::{ActiveTheme as _, Theme},
};

/// The width of the leading rule on an alert. Thick enough to read as a solid
/// edge rather than as a hairline that lost its other three sides.
const RULE: f32 = 3.0;

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

    /// Whether this state stops device work, and so earns a contained surface.
    fn is_alert(&self) -> bool {
        matches!(self.tone, Tone::Caution | Tone::Critical)
    }
}

impl RenderOnce for Banner {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        if self.is_alert() { self.alert(cx).into_any_element() } else { self.notice(cx) }
    }
}

impl Banner {
    /// A state that blocks nothing: reported, not announced.
    fn notice(self, cx: &mut gpui::App) -> AnyElement {
        let theme = cx.inari();
        div()
            .id(self.id)
            .h_flex()
            .items_start()
            .gap(px(Theme::SPACE_SM))
            .w_full()
            .child(
                // Aligned to the cap height of the first line rather than to
                // the top of the text box, which is where a dot beside a
                // sentence looks like it belongs.
                div()
                    .flex_none()
                    .mt(px(6.0))
                    .child(StatusDot::new(self.tone)),
            )
            .child(
                div()
                    .v_flex()
                    .flex_1()
                    .gap(px(1.0))
                    .child(
                        div()
                            .text_body()
                            .font_weight(gpui::FontWeight::MEDIUM)
                            // The tone is already on the dot. Repeating it on
                            // the words costs contrast and buys nothing.
                            .text_color(theme.text)
                            .child(self.title),
                    )
                    .child(
                        div()
                            .text_caption()
                            .text_color(theme.text_secondary)
                            .child(self.detail),
                    ),
            )
            .children(
                self.action
                    .map(|action| div().flex_none().child(action)),
            )
            .into_any_element()
    }

    /// A state that stops device work: contained, and edged in its own tone.
    fn alert(self, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let color = self.tone.color(theme);
        div()
            .id(self.id)
            .relative()
            .h_flex()
            .items_start()
            .gap(px(Theme::SPACE_MD))
            .w_full()
            .p(px(Theme::SPACE_MD + 2.0))
            // Clears the rule, so the glyph sits on the same left margin the
            // text would have had without it.
            .pl(px(Theme::SPACE_MD + 2.0 + RULE))
            .rounded(px(Theme::RADIUS_CARD))
            // The container clips the rule, so one radius serves both and the
            // rule never needs a corner of its own.
            .overflow_hidden()
            .bg(self.tone.wash(theme))
            .child(
                div()
                    .absolute()
                    .left_0()
                    .top_0()
                    .bottom_0()
                    .w(px(RULE))
                    .bg(color),
            )
            .children(top_lip(theme.is_dark()))
            .child(
                Icon::from(Symbol::Component(self.tone.symbol()))
                    .size(px(16.0))
                    .flex_none()
                    .mt(px(1.0))
                    .text_color(color),
            )
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

/// A hairline of light along the top edge, stopping short of the corners.
///
/// The same lip the surface ladder uses, so an alert and a card catch the light
/// from the same direction. It starts after the rule so the two do not meet in
/// a corner and read as a drawn outline.
fn top_lip(dark: bool) -> Option<gpui::Div> {
    dark.then(|| {
        div()
            .absolute()
            .top_0()
            .left(px(RULE + Theme::RADIUS_CARD))
            .right(px(Theme::RADIUS_CARD))
            .h(px(1.0))
            .bg(hsla(0.0, 0.0, 1.0, 0.07))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_states_that_block_device_work_earn_a_container() {
        for tone in [Tone::Caution, Tone::Critical] {
            assert!(Banner::new("id", tone, "title", "detail").is_alert());
        }
        // A healthy service reported inside an alert box is the loudest thing
        // on the screen saying nothing is wrong.
        for tone in [Tone::Positive, Tone::Busy, Tone::Neutral] {
            assert!(!Banner::new("id", tone, "title", "detail").is_alert());
        }
    }
}
