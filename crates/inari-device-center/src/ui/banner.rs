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
    AnimationExt as _, AnyElement, Bounds, Canvas, Hsla, InteractiveElement as _, IntoElement,
    ParentElement as _, Pixels, RenderOnce, SharedString, Styled, canvas, div, fill, hsla, point,
    px, size,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    motion,
    status::{StatusDot, Tone},
    theme::{ActiveTheme as _, Theme},
};

/// One cascade cell, and the gap between cells.
const CELL: f32 = 2.0;
const GAP: f32 = 2.0;
/// How far the cascade runs in from the leading edge. Past the glyph and a
/// little way under the first words, by which point the squared falloff has
/// taken it to almost nothing.
const COLUMNS: usize = 21;
/// How far apart two neighbouring columns sit in the wave. Small enough that
/// the crest reads as one travelling band rather than as cells taking turns.
const STAGGER: f32 = 0.055;
/// What a cell dims to between crests, as a fraction of its lit alpha. Never
/// zero: the cascade has to describe the same edge when motion is off as when
/// it is running.
const REST: f32 = 0.28;
/// How opaque a cell is at the leading edge on the crest. The wall has to read
/// against a tinted wash, so this is the cell's own alpha and the crest scales
/// it — not the other way round, which leaves every cell at `REST` and the
/// whole band invisible.
const PEAK: f32 = 0.85;
/// How sharply the light dies across the wall. Above one so it still gathers at
/// the leading edge; well below two, which spent the whole reach in the first
/// few columns.
const FALLOFF: f32 = 1.4;
/// The width the cascade occupies, including its trailing gap.
const BAND: f32 = COLUMNS as f32 * (CELL + GAP);
/// The leading column of the alert: the cascade runs through it and the tone
/// glyph sits centred in it, far enough in that the light has died away.
const GUTTER: f32 = 48.0;

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
            // A plain flex row rather than `h_flex`, which centres its items.
            // Left at the default the children stretch, so the gutter is as
            // tall as the alert however many lines the message runs to and its
            // glyph stays on the centre line.
            .flex()
            .flex_row()
            .w_full()
            .py(px(Theme::SPACE_MD + 2.0))
            .pr(px(Theme::SPACE_MD + 2.0))
            .rounded(px(Theme::RADIUS_CARD))
            // Trims the wall's overrun at the top, the bottom and the leading
            // edge. Only the overrun: GPUI's clip is rectangular, so the two
            // rounded corners are the paint's own problem.
            .overflow_hidden()
            .bg(self.tone.wash(theme))
            // Behind everything, edge to edge: the wall spans the alert's
            // whole box, padding included, and reaches past the glyph, so the
            // light is something the alert sits in rather than a bar bolted to
            // its edge.
            .child(cascade(color, Theme::RADIUS_CARD))
            .children(top_lip(theme.is_dark()))
            .child(
                // The leading column. The glyph is centred in it on both axes
                // and paints over the wall, at the point where the falloff has
                // already taken the light down.
                div()
                    .relative()
                    .flex_none()
                    .w(px(GUTTER))
                    .flex()
                    .items_center()
                    .justify_center()
                    .child(
                        Icon::from(self.tone.symbol())
                            .size(px(17.0))
                            .flex_none()
                            .text_color(color),
                    ),
            )
            .child(
                div()
                    .relative()
                    .v_flex()
                    .flex_1()
                    .gap(px(2.0))
                    .py(px(1.0))
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
                    .flex()
                    .items_center()
                    .child(action)
            }))
    }
}

/// The lit edge of an alert: a wall of cells with a crest running in from the
/// leading edge and dying out over a short distance.
///
/// Painted rather than built from elements, because the wall has to answer to
/// the size it is given. A fixed grid of divs cannot: the row count is decided
/// before layout runs, so a taller alert leaves the wall short, and a shorter
/// one makes flex compress the cells until the pixels stop being square. A
/// canvas is handed the real bounds at paint time, so the rows are counted from
/// the height it actually got and every cell keeps its own size at any
/// dimension.
///
/// The falloff is curved rather than linear. A linear ramp reads as a bar that
/// someone faded out; a curve keeps the light gathered at the edge and lets the
/// tail go to almost nothing, which is what makes it read as light rather than
/// as a shape. Squared put the whole of it in the first few columns and left
/// the rest of the reach dark, so the exponent sits just above one: the light
/// still gathers at the edge, but it carries the distance it is given.
fn cascade(color: Hsla, radius: f32) -> impl IntoElement {
    if !motion::enabled() {
        return wall(0.0, color, radius).into_any_element();
    }
    // Each frame rebuilds the canvas with the current place in the wave, which
    // is how a painted effect reads an animation that only ever hands an
    // element a scalar.
    wall(0.0, color, radius)
        .with_animation("banner-cascade", motion::cascade(), move |_, delta| {
            wall(delta, color, radius)
        })
        .into_any_element()
}

/// The wall at one moment in the wave, placed over the alert's whole box.
///
/// Absolute and inset to nothing, because a canvas asks for no size of its own:
/// as an ordinary flex child it measures zero and paints a single row inside
/// the alert's padding. Taffy resolves an absolute child's insets against the
/// parent's padding box, so `inset_0` is the alert's own edges — full height,
/// no inset from the padding above and below.
fn wall(delta: f32, color: Hsla, radius: f32) -> Canvas<()> {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, _| {
            paint_cascade(bounds, delta, color, radius, window);
        },
    )
    .absolute()
    .inset_0()
}

/// Paint the wall across whatever bounds the layout gave it.
fn paint_cascade(
    bounds: Bounds<Pixels>,
    delta: f32,
    color: Hsla,
    radius: f32,
    window: &mut gpui::Window,
) {
    let pitch = CELL + GAP;
    let height = f32::from(bounds.size.height);
    let columns = (BAND / pitch).floor().max(1.0) as usize;
    let rows = rows_for(height);
    // Centred, so the overrun is cut evenly at the top and the bottom.
    let top = f32::from(bounds.origin.y) + (height - rows as f32 * pitch) / 2.0;

    for column in 0..columns {
        let reach = 1.0 - column as f32 / columns as f32;
        let phase = motion::staggered_phase(delta, column, STAGGER);
        // The crest is narrow: raising the wave to a high power leaves a
        // travelling band instead of the whole wall breathing together.
        let crest = motion::pulse_wave(phase).powf(6.0);
        let alpha = PEAK * reach.powf(FALLOFF) * (REST + (1.0 - REST) * crest);
        if alpha < 0.004 {
            continue;
        }
        let x = f32::from(bounds.origin.x) + column as f32 * pitch;
        for row in 0..rows {
            let y = top + row as f32 * pitch;
            let coverage = corner_coverage(bounds, radius, x, y);
            if coverage <= 0.0 {
                continue;
            }
            window.paint_quad(fill(
                Bounds { origin: point(px(x), px(y)), size: size(px(CELL), px(CELL)) },
                Hsla { a: alpha * coverage, ..color },
            ));
        }
    }
}

/// How many rows cover `height` at the cascade's own pitch.
///
/// One more than fits, so the grid always overruns the surface and the wall
/// reaches both edges rather than stopping short of them. Density is a
/// constant: the count follows the height instead of the cells stretching to
/// meet it, which is what keeps a pixel square at every size.
fn rows_for(height: f32) -> usize {
    (height / (CELL + GAP)).ceil() as usize + 1
}

/// What fraction of the cell at (`x`, `y`) the alert's rounded corner leaves
/// visible.
///
/// The container cannot clip this for us. GPUI masks children through
/// [`gpui::ContentMask`], a plain rectangle: `overflow_hidden` trims the wall
/// at the alert's edges, while a corner radius shapes only the parent's own
/// background quad (`distance_from_clip_rect` in gpui's shaders takes no
/// radius). So the wall shades itself along the same arc the background
/// follows — a cell the corner covers fully stays at full strength, a cell it
/// covers partly dims by the covered fraction, and a cell it misses drops
/// out. Dropping whole cells instead cut a second, coarser corner into the
/// surface.
///
/// Coverage is sampled on a three-by-three grid per cell. At two pixels a
/// side the error is under a pixel, which is what keeps the fade continuous
/// rather than stepped.
fn corner_coverage(bounds: Bounds<Pixels>, radius: f32, x: f32, y: f32) -> f32 {
    let left = f32::from(bounds.origin.x);
    let top = f32::from(bounds.origin.y);
    let bottom = top + f32::from(bounds.size.height);
    // Past the leading arcs the edges are straight and every cell is inside.
    if x >= left + radius {
        return 1.0;
    }
    const SAMPLES: u32 = 3;
    let step = CELL / SAMPLES as f32;
    let mut inside = 0;
    for column in 0..SAMPLES {
        for row in 0..SAMPLES {
            let sample_x = x + (column as f32 + 0.5) * step;
            let sample_y = y + (row as f32 + 0.5) * step;
            let (centre_x, centre_y) = if sample_y < top + radius {
                (left + radius, top + radius)
            } else if sample_y > bottom - radius {
                (left + radius, bottom - radius)
            } else {
                inside += 1;
                continue;
            };
            let dx = sample_x - centre_x;
            let dy = sample_y - centre_y;
            if dx * dx + dy * dy <= radius * radius {
                inside += 1;
            }
        }
    }
    let total = SAMPLES * SAMPLES;
    inside as f32 / total as f32
}

/// A hairline of light along the top edge, stopping short of the corners.
///
/// The same lip the surface ladder uses, so an alert and a card catch the light
/// from the same direction. It starts past the gutter, clear of the bright end
/// of the cascade, where two kinds of light meeting would read as a drawn
/// outline.
fn top_lip(dark: bool) -> Option<gpui::Div> {
    dark.then(|| {
        div()
            .absolute()
            .top_0()
            .left(px(GUTTER))
            .right(px(Theme::RADIUS_CARD))
            .h(px(1.0))
            .bg(hsla(0.0, 0.0, 1.0, 0.07))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_wall_always_overruns_the_height_it_is_given() {
        // Whatever the alert measures, the grid covers it and then some, so the
        // light reaches the top and bottom edges instead of stopping short.
        for height in [24.0_f32, 48.0, 61.0, 137.0, 400.0] {
            let covered = rows_for(height) as f32 * (CELL + GAP);
            assert!(covered >= height, "{height} left uncovered");
        }
    }

    #[test]
    fn a_taller_alert_gets_more_rows_rather_than_taller_ones() {
        // The failure this replaces: a fixed row count meant the cells had to
        // stretch or squash to fit, and the pixels stopped being square.
        assert!(rows_for(200.0) > rows_for(60.0));
    }

    #[test]
    fn the_wall_meets_the_corner_the_mask_cannot_cut() {
        let bounds = Bounds { origin: point(px(0.0), px(0.0)), size: size(px(400.0), px(60.0)) };
        let radius = 12.0;
        // The corner cells sit wholly outside the arc: no light escapes the
        // surface, which is what the rectangular mask cannot guarantee.
        assert_eq!(corner_coverage(bounds, radius, 0.0, 0.0), 0.0);
        assert_eq!(corner_coverage(bounds, radius, 0.0, 56.0), 0.0);
        // A cell straddling the curve fades rather than dropping, so the wall
        // follows the same arc the alert's own background paints.
        let straddling = corner_coverage(bounds, radius, 8.0, 0.0);
        assert!(straddling > 0.0 && straddling < 1.0, "{straddling}");
        // Away from the corners the wall is untouched, top edge included.
        assert_eq!(corner_coverage(bounds, radius, 0.0, 30.0), 1.0);
        assert_eq!(corner_coverage(bounds, radius, 12.0, 0.0), 1.0);
        // The fade follows the arc: nearer the curve reads dimmer than just
        // inside it.
        let nearer = corner_coverage(bounds, radius, 0.0, 8.0);
        let inside = corner_coverage(bounds, radius, 4.0, 4.0);
        assert!(nearer < inside, "{nearer} should dim harder than {inside}");
    }

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
