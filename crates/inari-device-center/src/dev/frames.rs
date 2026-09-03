//! How often the window is rebuilding itself.
//!
//! This is deliberately not called FPS. GPUI redraws when something asks it to,
//! so an idle window that redraws twice a second is healthy, not slow. What
//! actually goes wrong is the opposite: one repeating animation that keeps
//! asking, and pins a window at the display's refresh rate for as long as it is
//! open. Zeron measured 36% CPU on an M-series laptop from a single spinner
//! doing exactly that.
//!
//! So the readout answers the question that catches it: how many renders
//! happened in the last second, and how long the longest gap was.

use std::{
    collections::VecDeque,
    time::{Duration, Instant},
};

use gpui::{App, BorrowAppContext as _, Global, Window};

use super::chart::Sample;

/// A second of history at 120Hz, which is as far back as a cadence problem
/// needs to be visible.
const WINDOW: usize = 120;

#[derive(Default)]
pub struct Frames {
    last: Option<Instant>,
    /// Gaps between consecutive renders, newest last.
    gaps: VecDeque<Duration>,
    /// What each of those frames cost, newest last.
    samples: VecDeque<Sample>,
}

impl Global for Frames {}

/// Record one root render. Called from the floating layer, which every root
/// mounts, so no ordinary component has to know this exists.
pub fn tick(window: &Window, cx: &mut App) {
    // The stats are the *previous* frame's — this one is still being built.
    // That is what makes them worth reading: a frame cannot report its own cost
    // while it is paying it.
    let stats = window.frame_stats();
    let sample = Sample { build: stats.build, paint: stats.paint, total: stats.total };
    if !cx.has_global::<Frames>() {
        cx.set_global(Frames::default());
    }
    cx.update_global(|frames: &mut Frames, _| {
        let now = Instant::now();
        if let Some(last) = frames.last {
            frames.gaps.push_back(now - last);
            if frames.gaps.len() > WINDOW {
                frames.gaps.pop_front();
            }
            frames.samples.push_back(sample);
            if frames.samples.len() > WINDOW {
                frames.samples.pop_front();
            }
        }
        frames.last = Some(now);
    });
}

/// What the recent frames cost, oldest first.
pub fn samples(cx: &App) -> Vec<Sample> {
    cx.try_global::<Frames>()
        .map(|frames| frames.samples.iter().copied().collect())
        .unwrap_or_default()
}

/// What the readout shows.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Cadence {
    /// Renders in the last second.
    pub rate: usize,
    /// The gap before the most recent render.
    pub last: Duration,
    /// The longest gap in the window — where a stall would show.
    pub longest: Duration,
}

pub fn cadence(cx: &App) -> Cadence {
    cx.try_global::<Frames>()
        .map(|frames| measure(frames.gaps.iter().copied()))
        .unwrap_or_default()
}


fn measure(gaps: impl Iterator<Item = Duration>) -> Cadence {
    let gaps: Vec<Duration> = gaps.collect();
    let mut total = Duration::ZERO;
    let mut rate = 0;
    // Walk back from the newest until a second of history is spent: the rate is
    // how many renders fit inside it.
    for gap in gaps.iter().rev() {
        total += *gap;
        if total > Duration::from_secs(1) {
            break;
        }
        rate += 1;
    }
    Cadence {
        rate,
        last: gaps.last().copied().unwrap_or_default(),
        longest: gaps.iter().copied().max().unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ms(millis: u64) -> Duration {
        Duration::from_millis(millis)
    }

    #[test]
    fn an_idle_window_reports_a_low_rate() {
        assert_eq!(measure([ms(500), ms(500)].into_iter()).rate, 2);
    }

    #[test]
    fn a_pinned_window_reports_the_refresh_rate() {
        assert_eq!(measure(std::iter::repeat_n(ms(8), 120)).rate, 120);
    }

    #[test]
    fn a_stall_shows_up_as_the_longest_gap() {
        let mut gaps = vec![ms(16); 10];
        gaps.push(ms(400));
        let cadence = measure(gaps.into_iter());
        assert_eq!(cadence.longest, ms(400));
        assert_eq!(cadence.last, ms(400));
    }

    #[test]
    fn history_older_than_a_second_is_not_counted() {
        // Ten fast renders, then nothing for two seconds: the rate is what
        // happened recently, not what happened at all.
        let mut gaps = vec![ms(16); 10];
        gaps.push(ms(2000));
        assert_eq!(measure(gaps.into_iter()).rate, 0);
    }
}
