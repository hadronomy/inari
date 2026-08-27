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
//! surface ladder uses, and a cascade of lit cells running in from the leading
//! edge. The cascade replaces the outline rather than decorating it — the edge
//! that carries the severity is the only edge drawn, so an alert does not read
//! as one more card in a column of cards.
//!
//! The cascade is a grid of real quads, not a shader. GPUI 0.2.2 has no
//! application shader hook and no wgpu; the reference implementation of this
//! effect pins a forked GPUI to add renderer primitives. See
//! `docs/device-center-pixel-cascade.md` for that route and what it would cost.
//!
//! No glyph animates. They label a settled state, and a spinner beside "this
//! computer is not connected" claims work that nobody is doing.

use gpui::{
    AnimationExt as _, AnyElement, Hsla, InteractiveElement as _, IntoElement, ParentElement as _,
    RenderOnce, SharedString, Styled, div, hsla, px,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    icon::Symbol,
    motion,
    status::{StatusDot, Tone},
    theme::{ActiveTheme as _, Theme},
};

/// One cascade cell, and the gap between cells.
const CELL: f32 = 2.0;
const GAP: f32 = 1.0;
/// How far the cascade runs in from the edge, and how tall it stands.
const COLUMNS: usize = 13;
const ROWS: usize = 9;
/// How far apart two neighbouring columns sit in the wave. Small enough that
/// the crest reads as one travelling band rather than as cells taking turns.
const STAGGER: f32 = 0.055;
/// What a cell sits at between crests. Never zero: the cascade has to describe
/// the same edge when motion is off as when it is running.
const REST: f32 = 0.10;
/// The width the cascade occupies, including its trailing gap.
const BAND: f32 = COLUMNS as f32 * (CELL + GAP);

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
            // Clears the cascade, so the glyph sits on the same left margin the
            // text would have had without it.
            .pl(px(Theme::SPACE_MD + 2.0 + BAND))
            .rounded(px(Theme::RADIUS_CARD))
            // The container clips the cascade, so one radius serves both and
            // no cell needs a corner of its own.
            .overflow_hidden()
            .bg(self.tone.wash(theme))
            .child(cascade(color))
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

/// The lit edge of an alert: a wall of cells with a crest running in from the
/// leading edge and dying out over a short distance.
///
/// Each column is one repeating animation on a shared period, reading its own
/// place in the wave from its index, so the whole wall stays phase-locked
/// without a clock to keep. Cells only ever change colour inside a fixed slot,
/// so nothing here can move the text beside it.
///
/// The falloff is squared rather than linear. A linear ramp reads as a bar that
/// someone faded out; a squared one keeps the light gathered at the edge and
/// lets the tail go to almost nothing, which is what makes it read as light
/// rather than as a shape.
fn cascade(color: Hsla) -> gpui::Div {
    let running = motion::enabled();
    div()
        .absolute()
        .left_0()
        .top_0()
        .bottom_0()
        .w(px(BAND))
        .flex()
        .items_center()
        .children((0..COLUMNS).map(move |column| {
            let reach = 1.0 - (column as f32 / COLUMNS as f32);
            let reach = reach * reach;
            let column_element = div()
                .w(px(CELL))
                .mr(px(GAP))
                .h_full()
                .flex()
                .flex_col()
                .justify_center()
                .children((0..ROWS).map(move |row| {
                    // The wall is brightest on its centre line and thins at the
                    // top and bottom, so it reads as a band of light rather
                    // than a rectangle of cells.
                    let centre = (row as f32 - (ROWS as f32 - 1.0) / 2.0).abs();
                    let spread = 1.0 - centre / ((ROWS as f32 - 1.0) / 2.0 + 1.0);
                    div()
                        .h(px(CELL))
                        .mb(px(GAP))
                        .w_full()
                        .bg(Hsla { a: REST * reach * spread, ..color })
                }));
            if running {
                column_element
                    .with_animation(
                        ("banner-cascade", column),
                        motion::cascade(),
                        move |cells, delta| {
                            let phase = motion::staggered_phase(delta, column, STAGGER);
                            // The crest is narrow: raising the wave to a high
                            // power leaves a travelling band instead of the
                            // whole wall breathing together.
                            let crest = motion::pulse_wave(phase).powf(6.0);
                            cells.opacity(REST + (1.0 - REST) * crest)
                        },
                    )
                    .into_any_element()
            } else {
                column_element.into_any_element()
            }
        }))
}

/// A hairline of light along the top edge, stopping short of the corners.
///
/// The same lip the surface ladder uses, so an alert and a card catch the light
/// from the same direction. It starts after the cascade so the two do not meet
/// in a corner and read as a drawn outline.
fn top_lip(dark: bool) -> Option<gpui::Div> {
    dark.then(|| {
        div()
            .absolute()
            .top_0()
            .left(px(BAND + Theme::RADIUS_CARD))
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
