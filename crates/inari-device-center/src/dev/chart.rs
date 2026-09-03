//! The arithmetic behind the performance charts.
//!
//! Kept apart from the painting, because scaling an axis is the part that can be
//! wrong in a way a screenshot will not show: a chart with a bad ceiling looks
//! exactly like a chart with a good one, and quietly tells you the wrong story
//! about where the time went.

use std::time::Duration;

/// One frame, as the chart draws it.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Sample {
    /// Rendering views, laying out, and prepainting.
    pub build: Duration,
    /// Writing the scene.
    pub paint: Duration,
    /// All of `Window::draw`. Never less than the two above.
    pub total: Duration,
}

impl Sample {
    /// Everything `draw` spent outside the two phases: bookkeeping, the
    /// dispatch tree, the frame swap.
    pub fn rest(&self) -> Duration {
        self.total
            .saturating_sub(self.build)
            .saturating_sub(self.paint)
    }

    /// The three bands a stacked area draws, in milliseconds and cumulative.
    ///
    /// Cumulative because an area chart draws from a baseline, not from the
    /// band below it: to read as a stack the series have to be totals, and to
    /// be visible the largest has to be drawn first.
    pub fn bands(&self) -> (f64, f64, f64) {
        (
            millis(self.total),
            millis(self.build + self.paint),
            millis(self.build),
        )
    }
}

pub fn millis(duration: Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

/// A 60Hz frame. The chart's floor, so a window that is comfortably fast draws
/// a low flat line instead of magnifying its own noise into a mountain range.
pub const BUDGET: Duration = Duration::from_micros(16_667);

/// The value at `fraction` through a run, once sorted.
///
/// The median and the 95th say between them what an average cannot: whether a
/// window is slow, or fast with a stutter.
pub fn percentile(samples: &[Sample], fraction: f32) -> Duration {
    if samples.is_empty() {
        return Duration::ZERO;
    }
    let mut totals: Vec<Duration> = samples
        .iter()
        .map(|sample| sample.total)
        .collect();
    totals.sort_unstable();
    let last = totals.len() - 1;
    let index = (fraction.clamp(0.0, 1.0) * last as f32).round() as usize;
    totals[index.min(last)]
}

/// How many of these frames missed the budget.
pub fn over_budget(samples: &[Sample]) -> usize {
    samples
        .iter()
        .filter(|sample| sample.total > BUDGET)
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ms(millis: u64) -> Duration {
        Duration::from_millis(millis)
    }

    fn sample(build: u64, paint: u64, total: u64) -> Sample {
        Sample { build: ms(build), paint: ms(paint), total: ms(total) }
    }

    #[test]
    fn the_rest_is_what_the_two_phases_did_not_account_for() {
        assert_eq!(sample(4, 3, 10).rest(), ms(3));
    }

    #[test]
    fn a_total_smaller_than_its_phases_does_not_underflow() {
        // Clocks are not perfectly nested; this must not panic.
        assert_eq!(sample(9, 9, 10).rest(), Duration::ZERO);
    }










    #[test]
    fn the_bands_are_cumulative_so_a_stacked_area_reads_as_a_stack() {
        let (total, upper, lower) = sample(4, 3, 10).bands();
        assert_eq!((total, upper, lower), (10.0, 7.0, 4.0));
        assert!(lower <= upper && upper <= total);
    }

    #[test]
    fn the_median_and_the_ninety_fifth_tell_slow_apart_from_stuttering() {
        let mut stuttering: Vec<Sample> = (0..19).map(|_| sample(1, 1, 2)).collect();
        stuttering.push(sample(40, 20, 64));
        assert_eq!(percentile(&stuttering, 0.5), ms(2));
        assert_eq!(percentile(&stuttering, 1.0), ms(64));
    }

    #[test]
    fn a_percentile_of_nothing_is_nothing() {
        assert_eq!(percentile(&[], 0.5), Duration::ZERO);
    }

    #[test]
    fn frames_over_budget_are_counted_not_estimated() {
        let run = [sample(1, 1, 2), sample(10, 8, 20), sample(9, 8, 17)];
        assert_eq!(over_budget(&run), 2);
    }


}
