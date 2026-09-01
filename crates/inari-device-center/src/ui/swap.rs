//! Swapping a control's glyph and its label without either one snapping.
//!
//! A copy button that answers with a tick is reporting a state change, which
//! is one of the few things Emil Kowalski's framework says is always worth
//! animating: the user pressed something and the interface has to say it
//! heard. Landing the tick in a single frame throws that away — the change is
//! over before the eye that caused it arrives.
//!
//! The web recipe is transitions.dev's icon swap and text swap. Both stack the
//! two states in one slot and cross-fade between them: the glyph scales, the
//! label leaves upward and arrives from below, and a 2px blur bridges the
//! moment when both are half-present.
//!
//! One thing had to change on the way over. **There is no scale transform for
//! a div.** GPUI can scale an SVG, about its own centre, and nothing else — so
//! the glyph is painted through [`gpui::svg`] rather than as a styled box, and
//! the label moves instead of scaling, which is what the web recipe does for
//! text anyway.
//!
//! The blur is a real Gaussian, through [`effect::blurred`]: the half that is
//! leaving goes out of focus as it shrinks, and the half arriving comes into
//! focus as it grows. It is what lets the two halves cross at half strength
//! each without reading as two marks stacked on one another, and what lets
//! [`motion::SWAP_SCALE`] be small enough for the swap to have a pop in it.

use gpui::{
    AnyElement, App, Hsla, IntoElement, ParentElement as _, Pixels, RenderOnce, SharedString,
    Styled, Transformation, Window, div, px, size, svg,
};

use super::{content::Typography as _, effect, icon::Symbol, motion};

/// Where a swap has got to, as the fractions its two halves need.
struct Phase {
    /// How present the state being left behind still is.
    leaving: f32,
    /// How present the state arriving is.
    arriving: f32,
    /// The eased fraction itself, for whatever moves rather than fades.
    eased: f32,
}

impl Phase {
    /// A constant-power crossfade.
    ///
    /// Fading one mark down while another comes up is the same problem as
    /// crossfading two uncorrelated sounds: the halves are different shapes in
    /// different places, so their presence adds in power rather than in
    /// amplitude. Split the fraction linearly and the midpoint has two marks at
    /// half strength and reads as *less* than either — a dip where the control
    /// briefly says nothing. Taking the square root holds the pair at constant
    /// strength right through the crossing.
    fn new(eased: f32) -> Self {
        Self {
            leaving: (1.0 - eased).sqrt(),
            arriving: eased.sqrt(),
            eased,
        }
    }
}

/// Aim a swap at `active` and report where it has got to.
///
/// Driven from the render pass rather than from an event, because the state
/// that flips it arrives with data — a copy landing on the clipboard — and not
/// with a pointer. The window is refreshed by whatever recorded the copy; this
/// only has to notice that the target moved.
fn phase(
    window: &mut Window,
    key: &SharedString,
    active: bool,
    duration: std::time::Duration,
) -> Phase {
    if motion::fade_target(key.clone()) != f32::from(active) {
        motion::swap_set(key.clone(), active, duration);
        // The root asked whether anything was mid-flight before this element
        // rendered, so a swap starting now would wait for an unrelated repaint
        // to move. Marking the window dirty is what walks it forward.
        window.refresh();
    }
    // The store's value is already "how active is this", not "how far along
    // the current leg": it climbs to 1 heading for the active state and falls
    // to 0 heading back. Inverting it for the return trip plays the swap
    // backwards and settles it on the wrong glyph.
    Phase::new(motion::fade_fraction(key.clone()))
}

/// Two glyphs in one slot, the second replacing the first.
///
/// `resting` is what the control shows when nothing has happened; `active` is
/// what it shows while it is reporting. The slot is a fixed square, so nothing
/// around it moves whichever glyph is up.
#[derive(IntoElement)]
pub struct IconSwap {
    key: SharedString,
    resting: Symbol,
    active: Symbol,
    showing_active: bool,
    edge: f32,
    resting_color: Hsla,
    active_color: Hsla,
    /// A point on the curve to hold still at, instead of running. See
    /// [`IconSwap::pinned`].
    #[cfg(debug_assertions)]
    pinned: Option<f32>,
    /// How present the resting glyph is allowed to be at most. A copy hint
    /// that only exists under the pointer carries the hover fade here, while
    /// the state it swaps to ignores it — an acknowledgement the operator can
    /// walk away from before it has finished is not an acknowledgement.
    resting_alpha: f32,
}

pub fn icon(
    key: impl Into<SharedString>,
    resting: impl Into<Symbol>,
    active: impl Into<Symbol>,
    showing_active: bool,
) -> IconSwap {
    IconSwap {
        key: key.into(),
        resting: resting.into(),
        active: active.into(),
        showing_active,
        edge: 14.0,
        resting_color: gpui::white(),
        active_color: gpui::white(),
        #[cfg(debug_assertions)]
        pinned: None,
        resting_alpha: 1.0,
    }
}

impl IconSwap {
    pub fn size(mut self, edge: f32) -> Self {
        self.edge = edge;
        self
    }

    pub fn tones(mut self, resting: Hsla, active: Hsla) -> Self {
        self.resting_color = resting;
        self.active_color = active;
        self
    }

    pub fn resting_alpha(mut self, alpha: f32) -> Self {
        self.resting_alpha = alpha;
        self
    }

    /// Hold the swap still, `fraction` of the way through.
    ///
    /// A swap is [`motion::SWAP`] long, which is too fast to judge while it
    /// runs and is exactly what has to be judged: the frames worth arguing
    /// about are the ones where both halves are present. Pinning renders those
    /// frames through the same code the running control uses, so a preview
    /// cannot drift from the thing it previews.
    ///
    /// `fraction` is how far along the halves are, not how much time has
    /// passed. [`motion::EASE_SWAP`] is steep enough that even sampling by time
    /// spends most of its frames on the two ends where nothing moves, and shows
    /// the crossing in one of them.
    #[cfg(debug_assertions)]
    pub fn pinned(mut self, fraction: f32) -> Self {
        self.pinned = Some(fraction);
        self
    }
}

impl RenderOnce for IconSwap {
    fn render(self, window: &mut Window, _: &mut App) -> impl IntoElement {
        #[cfg(debug_assertions)]
        let phase = match self.pinned {
            Some(fraction) => Phase::new(fraction),
            None => phase(window, &self.key, self.showing_active, motion::SWAP),
        };
        #[cfg(not(debug_assertions))]
        let phase = phase(window, &self.key, self.showing_active, motion::SWAP);
        // Scale runs the whole eased fraction while the fades run their offset
        // halves: the mark that is leaving keeps shrinking after it has gone,
        // and the one arriving is already growing before it can be seen, so
        // neither appears to start or stop moving at the moment it becomes
        // visible.
        let leaving_scale = 1.0 - (1.0 - motion::SWAP_SCALE) * phase.eased;
        let arriving_scale = motion::SWAP_SCALE + (1.0 - motion::SWAP_SCALE) * phase.eased;
        let leaving = phase.leaving * self.resting_alpha;

        // Each half is as far out of focus as it is far from resting, so the
        // blur is deepest where the two cross and gone by the time either one
        // is alone on screen.
        let deepest = self.edge * motion::SWAP_BLUR;
        let leaving_blur = px(deepest * phase.eased);
        let arriving_blur = px(deepest * (1.0 - phase.eased));

        div()
            .relative()
            .flex_none()
            .size(px(self.edge))
            .children((leaving > 0.004).then(|| {
                glyph(
                    self.resting,
                    self.edge,
                    self.resting_color,
                    leaving,
                    leaving_scale,
                    leaving_blur,
                )
            }))
            .children((phase.arriving > 0.004).then(|| {
                glyph(
                    self.active,
                    self.edge,
                    self.active_color,
                    phase.arriving,
                    arriving_scale,
                    arriving_blur,
                )
            }))
    }
}

/// One glyph in the slot, at an opacity, a scale and a blur.
///
/// Painted through `svg` rather than through the icon component because the
/// scale has to reach the mark itself: GPUI applies a transformation to an
/// SVG's own matrix, about its centre, and offers a div no equivalent.
fn glyph(
    symbol: Symbol,
    edge: f32,
    color: Hsla,
    alpha: f32,
    scale: f32,
    blur: Pixels,
) -> impl IntoElement {
    let mark = svg()
        .absolute()
        .inset_0()
        .size(px(edge))
        .path(symbol.path())
        .text_color(color)
        .opacity(alpha)
        .with_transformation(Transformation::scale(size(scale, scale)));
    // A blur costs two textures, and at the ends of a swap it would be two
    // textures to soften something by less than a pixel. `blurred` cannot know
    // that the radius it was handed is about to be zero; the caller can.
    soften(blur, mark)
}

/// Below this a blur moves no pixel anyone can see, and still costs two
/// textures and two composites to do it.
const VISIBLE_BLUR: Pixels = px(0.1);

/// The size of the mark a label swap blurs, which is its own type size. Taken
/// from `Typography::text_body` rather than measured, because a label's blur
/// only has to match the glyph beside it and both are set from the same scale.
const LABEL_SIZE: f32 = 13.5;

/// Blur `content`, unless the radius is too small to see.
fn soften(radius: Pixels, content: impl IntoElement) -> AnyElement {
    if radius < VISIBLE_BLUR {
        return content.into_any_element();
    }
    effect::blurred(radius, content).into_any_element()
}

/// Two labels in one slot, the second replacing the first.
///
/// `resting` stays in the layout and reserves the width, so the control keeps
/// its size and the controls beside it never shuffle along; `active` is
/// painted over it. The one leaving rises and fades, the one arriving comes up
/// from below — the direction transitions.dev uses, and the reason a swap
/// reads as one label becoming another rather than as two labels blinking.
///
/// The resting label is the one that has to be the wider of the two, since it
/// is the one holding the space open.
#[derive(IntoElement)]
pub struct LabelSwap {
    key: SharedString,
    resting: SharedString,
    active: SharedString,
    showing_active: bool,
}

pub fn label(
    key: impl Into<SharedString>,
    resting: impl Into<SharedString>,
    active: impl Into<SharedString>,
    showing_active: bool,
) -> LabelSwap {
    LabelSwap { key: key.into(), resting: resting.into(), active: active.into(), showing_active }
}

impl RenderOnce for LabelSwap {
    fn render(self, window: &mut Window, _: &mut App) -> impl IntoElement {
        let phase = phase(window, &self.key, self.showing_active, motion::SWAP);
        let travel = motion::SWAP_TRAVEL;

        div()
            .relative()
            .flex_none()
            .child(soften(
                px(LABEL_SIZE * motion::SWAP_BLUR * phase.eased),
                div()
                    .text_body()
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .opacity(phase.leaving)
                    // A relative inset, which taffy resolves after layout, so
                    // the label moves the way a CSS transform would and its
                    // neighbours do not follow it.
                    .relative()
                    .top(px(-travel * phase.eased))
                    .child(self.resting),
            ))
            .child(soften(
                px(LABEL_SIZE * motion::SWAP_BLUR * (1.0 - phase.eased)),
                div()
                    .absolute()
                    .inset_0()
                    .flex()
                    .items_center()
                    // Left, not centred. The resting label holds the width, so
                    // centring a shorter one inside it strands the glyph beside
                    // it with a gap the resting state never has; aligned to the
                    // same edge, the mark and the word stay one unit and the
                    // slack falls after them where nothing reads it.
                    .justify_start()
                    .text_body()
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .opacity(phase.arriving)
                    .top(px(travel * (1.0 - phase.eased)))
                    .child(self.active),
            ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_two_halves_hold_constant_power_across_the_crossing() {
        // Presence adds in power, not in amplitude, because the halves are
        // different shapes in different places. Splitting the fraction linearly
        // leaves the midpoint reading as less than either end — the dip this
        // exists to remove.
        for step in 0..=100 {
            let phase = Phase::new(step as f32 / 100.0);
            let power = phase.leaving.powi(2) + phase.arriving.powi(2);
            assert!(
                (power - 1.0).abs() < 1e-5,
                "power is {power} at {step}%"
            );
        }
    }

    #[test]
    fn the_crossing_carries_more_than_half_of_each_mark() {
        // The number that makes the difference: an even split is 0.5 each and
        // visibly thin. Constant power puts both at about 0.707, which is what
        // keeps the control legible while it changes its mind.
        let crossing = Phase::new(0.5);
        assert!(crossing.leaving > 0.7, "leaving is {}", crossing.leaving);
        assert!(crossing.arriving > 0.7, "arriving is {}", crossing.arriving);
    }

    #[test]
    fn a_blur_is_measured_against_the_mark_it_softens() {
        // A flat 2px is a smudge on a 13px readout mark and barely an edge on a
        // 56px one. The ratio keeps a swap looking the same at any size.
        let readout = 13.0 * motion::SWAP_BLUR;
        let oversized = 56.0 * motion::SWAP_BLUR;
        assert!(readout < 2.0, "the small mark is over-blurred: {readout}");
        assert!(oversized > 6.0, "the large mark is under-blurred: {oversized}");
    }

    #[test]
    fn the_blur_is_deepest_where_the_halves_cross_and_gone_at_both_ends() {
        // The blur exists to cover the crossing. If it were not at its deepest
        // there, the halves would meet as two sharp marks at half strength —
        // and if it were not gone at the ends, a resting control would sit
        // permanently out of focus, and pay for two textures to do it.
        let blur = |eased: f32| motion::SWAP_BLUR * eased;
        let counter = |eased: f32| motion::SWAP_BLUR * (1.0 - eased);

        assert_eq!(blur(0.0), 0.0, "the leaving half starts blurred");
        assert_eq!(counter(1.0), 0.0, "the arriving half ends blurred");
        assert_eq!(blur(0.5), counter(0.5), "the halves are not equally soft at the crossing");
        assert!(
            blur(0.5) > 0.0 && blur(0.5) < motion::SWAP_BLUR,
            "the crossing is not blurred: {}",
            blur(0.5)
        );
    }

    #[test]
    fn a_swap_with_no_history_reads_as_fully_resting() {
        // The store hands back 0 for a key it has never seen, and 0 has to mean
        // the resting glyph. Reading it as "1 minus progress" put a tick on
        // every copy button in the app before anything had been copied, and
        // ran the return trip backwards on the way there.
        let untouched = Phase::new(motion::fade_fraction("swap-test-never-set"));
        assert_eq!(untouched.leaving, 1.0);
        assert_eq!(untouched.arriving, 0.0);
    }

    #[test]
    fn a_swap_starts_and_finishes_on_exactly_one_state() {
        let start = Phase::new(0.0);
        assert_eq!(start.leaving, 1.0);
        assert_eq!(start.arriving, 0.0);
        let end = Phase::new(1.0);
        assert_eq!(end.leaving, 0.0);
        assert_eq!(end.arriving, 1.0);
    }

    #[test]
    fn the_curve_is_a_strong_ease_in_out() {
        // Nearly flat at both ends and steep through the middle: at a quarter
        // of the way through it has moved a twentieth, and by three quarters
        // it is all but there. That gap between the built-in ease-in-out and
        // this one is the punch the swap is borrowing.
        let ease = motion::EASE_SWAP;
        assert!(ease.ease(0.0).abs() < 1e-4);
        assert!((ease.ease(1.0) - 1.0).abs() < 1e-4);
        assert!(ease.ease(0.25) < 0.08, "should barely have left: {}", ease.ease(0.25));
        assert!(ease.ease(0.75) > 0.92, "should have all but landed: {}", ease.ease(0.75));
        // Monotonic, so nothing ever runs backwards mid-swap.
        let mut previous = 0.0;
        for step in 0..=200 {
            let value = ease.ease(step as f32 / 200.0);
            assert!(value >= previous - 1e-4, "went backwards at {step}");
            previous = value;
        }
    }

    #[test]
    fn the_solver_agrees_with_the_curve_it_claims_to_be() {
        // Pinned against an independent evaluation of
        // cubic-bezier(0.77, 0, 0.175, 1), so a change to the solver that
        // still looks plausible cannot quietly retune every swap in the app.
        for (progress, expected) in
            [(0.1, 0.0064), (0.25, 0.0529), (0.5, 0.5960), (0.75, 0.9563), (0.9, 0.9945)]
        {
            let actual = motion::EASE_SWAP.ease(progress);
            assert!((actual - expected).abs() < 2e-3, "at {progress}: {actual} != {expected}");
        }
    }

    #[test]
    fn the_curve_stays_inside_the_unit_interval() {
        for step in 0..=1000 {
            let value = motion::EASE_SWAP.ease(step as f32 / 1000.0);
            assert!((0.0..=1.0).contains(&value), "{value} at {step}");
        }
    }
}
