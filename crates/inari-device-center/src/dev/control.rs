//! The controls the knob panel is made of.
//!
//! Shaped after DialKit (joshpuckett/dialkit), read at `1d0ca134`: its
//! `src/components/Slider.tsx` for the interaction and `src/styles/theme.css`
//! for the geometry. The one idea worth copying above all others is the row:
//!
//! > Every control is a single 36px row with an 8px radius on a five-percent
//! > white surface. The label sits *inside* the row on the left, the value
//! > *inside* it on the right, and a slider is that same row with a fill and a
//! > handle drawn behind them.
//!
//! That is why a DialKit panel reads as one instrument rather than as a form.
//! A label above its control, which is what a settings screen does, doubles the
//! vertical space and makes twelve knobs unreadable.
//!
//! The arithmetic lives here as free functions, apart from the elements, because
//! it is the part that can be wrong in ways a screenshot will not show: which
//! step a click lands on, how far the band stretches, how many decimals a value
//! prints. Those get tests. The painting does not.

use std::ops::RangeInclusive;

/// One control row. DialKit's `--dial-row-height`.
pub const ROW_HEIGHT: f32 = 36.0;
/// `--dial-radius`.
pub const ROW_RADIUS: f32 = 8.0;
/// The inset the label and the value sit at.
pub const ROW_INSET: f32 = 10.0;
/// Every control prints at one size. DialKit sets 13px on all of them.
pub const TEXT_SIZE: f32 = 13.0;

/// The handle: 3px wide, 20px tall, fully round.
pub const HANDLE_WIDTH: f32 = 3.0;
pub const HANDLE_HEIGHT: f32 = 20.0;
/// At rest the handle is a quarter of its width, so it reads as a tick rather
/// than as a grip until the pointer arrives.
pub const HANDLE_RESTING_SCALE: f32 = 0.25;

/// A hash mark: 1px wide, 8px tall.
pub const MARK_WIDTH: f32 = 1.0;
pub const MARK_HEIGHT: f32 = 8.0;

/// Pointer travel that separates a click from a drag.
pub const CLICK_THRESHOLD: f32 = 3.0;
/// How far past the end the pointer travels before the band starts to stretch.
const DEAD_ZONE: f32 = 32.0;
/// The overshoot at which the band reaches its full stretch.
const MAX_CURSOR_RANGE: f32 = 200.0;
/// The furthest the band stretches.
const MAX_STRETCH: f32 = 8.0;
/// Clearance between the handle and the text it would otherwise sit under.
const HANDLE_BUFFER: f32 = 8.0;

/// A span with at most this many steps is discrete: its marks are its steps and
/// a click lands on one of them. Above it the slider reads as continuous and a
/// click is magnetic to the nearest tenth instead.
const DISCRETE_STEPS: f32 = 10.0;

/// How many decimals a value prints, from the size of its step.
///
/// A step of 1 prints no decimals, 0.1 prints one, 0.01 prints two. Printing
/// more than the step can reach is noise: a slider that moves in tenths has no
/// business showing hundredths.
pub fn decimals_for_step(step: f32) -> usize {
    if step <= 0.0 || step >= 1.0 {
        return 0;
    }
    // `0.1` is not exact in binary, so walk the step up by tens and stop when
    // it reaches a whole number rather than trusting `log10`.
    let mut decimals = 0;
    let mut scaled = step;
    while scaled < 1.0 && decimals < 6 {
        scaled *= 10.0;
        decimals += 1;
    }
    decimals
}

/// Round `value` to the nearest multiple of `step`.
pub fn round_to_step(value: f32, step: f32) -> f32 {
    if step <= 0.0 {
        return value;
    }
    (value / step).round() * step
}

/// Where `value` sits in `span`, as 0..1.
pub fn fraction(value: f32, span: &RangeInclusive<f32>) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    if hi <= lo {
        return 0.0;
    }
    ((value - lo) / (hi - lo)).clamp(0.0, 1.0)
}

/// The value at `fraction` through `span`.
pub fn value_at(fraction: f32, span: &RangeInclusive<f32>) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    (lo + fraction.clamp(0.0, 1.0) * (hi - lo)).clamp(lo, hi)
}

/// The number of steps `span` holds.
pub fn steps(span: &RangeInclusive<f32>, step: f32) -> f32 {
    if step <= 0.0 {
        return f32::INFINITY;
    }
    (*span.end() - *span.start()) / step
}

/// Whether the span is coarse enough that every step gets its own mark.
pub fn is_discrete(span: &RangeInclusive<f32>, step: f32) -> bool {
    steps(span, step) <= DISCRETE_STEPS
}

/// Where a click lands.
///
/// A click is not a drag: it is a request for a round number. On a coarse span
/// it lands on the nearest step; on a fine one it is magnetic to the nearest
/// tenth of the span, so clicking near the middle of a 0..1 slider gives 0.5
/// rather than 0.4913. Dragging is exact — only the click is opinionated.
pub fn snap_on_click(value: f32, span: &RangeInclusive<f32>, step: f32) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    if is_discrete(span, step) {
        (lo + ((value - lo) / step).round() * step).clamp(lo, hi)
    } else {
        snap_to_decile(value, span)
    }
}

/// The nearest tenth of the span.
pub fn snap_to_decile(value: f32, span: &RangeInclusive<f32>) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    if hi <= lo {
        return lo;
    }
    let decile = (hi - lo) / 10.0;
    (lo + ((value - lo) / decile).round() * decile).clamp(lo, hi)
}

/// The marks drawn behind the track, as fractions of its width.
///
/// A coarse span marks every step; a fine one marks the tenths a click is
/// magnetic to, so the marks and the snapping tell the same story.
pub fn marks(span: &RangeInclusive<f32>, step: f32) -> Vec<f32> {
    if is_discrete(span, step) {
        let count = steps(span, step).round() as usize;
        (1..count)
            .map(|index| index as f32 / count as f32)
            .collect()
    } else {
        (1..10)
            .map(|tenth| tenth as f32 / 10.0)
            .collect()
    }
}

/// How far the whole track slides when the pointer is dragged past its end.
///
/// Square-rooted, so the first pixels past the dead zone give most of the
/// movement and the band goes stiff as it approaches its limit — the shape
/// every rubber band on every platform has. `sign` is -1 past the left end and
/// +1 past the right.
pub fn rubber_stretch(distance_past: f32, sign: f32) -> f32 {
    let overflow = (distance_past - DEAD_ZONE).max(0.0);
    sign * MAX_STRETCH * (overflow / MAX_CURSOR_RANGE).min(1.0).sqrt()
}

/// Whether the handle would sit under the label or the value.
///
/// Both are painted inside the track, so at the ends the handle collides with
/// them. It gets out of the way rather than crossing them.
pub fn dodges(fraction: f32, track: f32, label_width: f32, value_width: f32) -> bool {
    if track <= 0.0 {
        return false;
    }
    let left = (ROW_INSET + label_width + HANDLE_BUFFER) / track;
    let right = (track - ROW_INSET - value_width - HANDLE_BUFFER) / track;
    fraction < left || fraction > right
}

/// The handle's opacity: invisible at rest, half-lit under the pointer, nearly
/// solid while dragging, and a ghost when it is dodging text.
pub fn handle_opacity(active: bool, dragging: bool, dodging: bool) -> f32 {
    if !active {
        0.0
    } else if dodging {
        0.1
    } else if dragging {
        0.9
    } else {
        0.5
    }
}

/// Print `value` at the precision its step can reach.
pub fn format_value(value: f32, step: f32) -> String {
    format!("{value:.*}", decimals_for_step(step))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn span(lo: f32, hi: f32) -> RangeInclusive<f32> {
        lo..=hi
    }

    #[test]
    fn decimals_follow_the_step() {
        assert_eq!(decimals_for_step(1.0), 0);
        assert_eq!(decimals_for_step(2.0), 0);
        assert_eq!(decimals_for_step(0.1), 1);
        assert_eq!(decimals_for_step(0.05), 2);
        assert_eq!(decimals_for_step(0.001), 3);
    }

    #[test]
    fn a_value_prints_no_further_than_its_step_can_reach() {
        assert_eq!(format_value(0.4913, 0.1), "0.5");
        assert_eq!(format_value(12.0, 1.0), "12");
    }

    #[test]
    fn a_fraction_and_a_value_are_inverses() {
        let span = span(8.0, 64.0);
        for step in 0..=10 {
            let f = step as f32 / 10.0;
            assert!((fraction(value_at(f, &span), &span) - f).abs() < 1e-5);
        }
    }

    #[test]
    fn a_value_outside_the_span_reads_as_an_end() {
        let span = span(0.0, 1.0);
        assert_eq!(fraction(-4.0, &span), 0.0);
        assert_eq!(fraction(9.0, &span), 1.0);
    }

    #[test]
    fn a_span_with_no_width_does_not_divide_by_zero() {
        let span = span(5.0, 5.0);
        assert_eq!(fraction(5.0, &span), 0.0);
        assert_eq!(snap_to_decile(5.0, &span), 5.0);
    }

    #[test]
    fn a_coarse_span_is_discrete_and_a_fine_one_is_not() {
        assert!(is_discrete(&span(0.0, 10.0), 2.0));
        assert!(is_discrete(&span(0.0, 3.0), 1.0));
        assert!(!is_discrete(&span(0.0, 1.0), 0.01));
    }

    #[test]
    fn a_click_on_a_coarse_span_lands_on_a_step() {
        let span = span(0.0, 3.0);
        assert_eq!(snap_on_click(1.4, &span, 1.0), 1.0);
        assert_eq!(snap_on_click(1.6, &span, 1.0), 2.0);
    }

    #[test]
    fn a_click_on_a_fine_span_is_magnetic_to_the_nearest_tenth() {
        let span = span(0.0, 1.0);
        assert!((snap_on_click(0.4913, &span, 0.01) - 0.5).abs() < 1e-5);
        assert!((snap_on_click(0.0312, &span, 0.01) - 0.0).abs() < 1e-5);
    }

    #[test]
    fn snapping_never_leaves_the_span() {
        let span = span(8.0, 64.0);
        assert_eq!(snap_on_click(1000.0, &span, 0.5), 64.0);
        assert_eq!(snap_on_click(-1000.0, &span, 0.5), 8.0);
    }

    #[test]
    fn a_coarse_span_marks_every_step_and_a_fine_one_marks_the_tenths() {
        assert_eq!(marks(&span(0.0, 3.0), 1.0), vec![1.0 / 3.0, 2.0 / 3.0]);
        assert_eq!(marks(&span(0.0, 1.0), 0.01).len(), 9);
    }

    #[test]
    fn the_marks_agree_with_where_a_click_lands() {
        // A mark a click cannot reach is a lie about the control.
        let span = span(0.0, 3.0);
        for mark in marks(&span, 1.0) {
            let value = value_at(mark, &span);
            assert!((snap_on_click(value, &span, 1.0) - value).abs() < 1e-4);
        }
    }

    #[test]
    fn the_band_does_not_stretch_inside_the_dead_zone() {
        assert_eq!(rubber_stretch(0.0, 1.0), 0.0);
        assert_eq!(rubber_stretch(DEAD_ZONE, 1.0), 0.0);
    }

    #[test]
    fn the_band_goes_stiff_as_it_reaches_its_limit() {
        let half = rubber_stretch(DEAD_ZONE + MAX_CURSOR_RANGE / 2.0, 1.0);
        let full = rubber_stretch(DEAD_ZONE + MAX_CURSOR_RANGE, 1.0);
        assert!((full - MAX_STRETCH).abs() < 1e-4);
        // Square-rooted: half the travel is already most of the movement.
        assert!(half > full * 0.7, "{half} should be most of {full}");
    }

    #[test]
    fn the_band_stretches_the_way_the_pointer_went() {
        assert!(rubber_stretch(300.0, -1.0) < 0.0);
        assert!(rubber_stretch(300.0, 1.0) > 0.0);
    }

    #[test]
    fn the_band_never_stretches_past_its_limit() {
        assert!(rubber_stretch(100_000.0, 1.0) <= MAX_STRETCH);
    }

    #[test]
    fn the_handle_dodges_the_label_and_the_value_but_not_the_middle() {
        let track = 200.0;
        assert!(dodges(0.02, track, 40.0, 30.0));
        assert!(dodges(0.98, track, 40.0, 30.0));
        assert!(!dodges(0.5, track, 40.0, 30.0));
    }

    #[test]
    fn a_track_with_no_width_does_not_report_a_dodge() {
        assert!(!dodges(0.5, 0.0, 40.0, 30.0));
    }

    #[test]
    fn the_handle_is_invisible_until_the_pointer_arrives() {
        assert_eq!(handle_opacity(false, false, false), 0.0);
        assert_eq!(handle_opacity(false, false, true), 0.0);
    }

    #[test]
    fn the_handle_is_brightest_while_dragging_and_faintest_while_dodging() {
        let hover = handle_opacity(true, false, false);
        let drag = handle_opacity(true, true, false);
        let dodge = handle_opacity(true, true, true);
        assert!(dodge < hover && hover < drag);
    }

    #[test]
    fn rounding_to_a_step_lands_on_a_multiple_of_it() {
        assert_eq!(round_to_step(0.47, 0.1), 0.5);
        assert_eq!(round_to_step(7.0, 2.0), 8.0);
        // A step of zero is a caller mistake, not a panic.
        assert_eq!(round_to_step(0.47, 0.0), 0.47);
    }
}
