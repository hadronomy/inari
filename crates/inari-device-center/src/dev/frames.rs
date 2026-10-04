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
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

use gpui::{App, BorrowAppContext as _, Global, Window, WindowId};

use super::chart::Sample;

/// A second of history at 120Hz, which is as far back as a cadence problem
/// needs to be visible.
const WINDOW: usize = 120;

#[derive(Default)]
struct History {
    last: Option<Instant>,
    /// Gaps between consecutive renders, newest last.
    gaps: VecDeque<Duration>,
    /// What each of those frames cost, newest last.
    samples: VecDeque<Sample>,
}

impl History {
    fn record(&mut self, now: Instant, sample: Sample) {
        if let Some(last) = self.last {
            self.gaps.push_back(now - last);
            if self.gaps.len() > WINDOW {
                self.gaps.pop_front();
            }
            self.samples.push_back(sample);
            if self.samples.len() > WINDOW {
                self.samples.pop_front();
            }
        }
        self.last = Some(now);
    }
}

#[derive(Default)]
struct Frames(HashMap<WindowId, History>);

impl Global for Frames {}

/// Record one root render. Called from the floating layer, which every root
/// mounts, so no ordinary component has to know this exists.
pub fn tick(window: &Window, cx: &mut App) {
    // The stats are the *previous* frame's — this one is still being built.
    // That is what makes them worth reading: a frame cannot report its own cost
    // while it is paying it.
    let stats = window.frame_stats();
    let sample = Sample { build: stats.build, paint: stats.paint, total: stats.total };
    let window_id = window.window_handle().window_id();
    if !cx.has_global::<Frames>() {
        cx.set_global(Frames::default());
    }
    let windows = cx.windows();
    cx.update_global(|frames: &mut Frames, _| {
        frames.0.retain(|id, _| {
            windows
                .iter()
                .any(|window| window.window_id() == *id)
        });
        frames
            .0
            .entry(window_id)
            .or_default()
            .record(Instant::now(), sample);
    });
}

/// What the recent frames cost, oldest first.
pub fn samples(window: &Window, cx: &App) -> Vec<Sample> {
    cx.try_global::<Frames>()
        .and_then(|frames| {
            frames
                .0
                .get(&window.window_handle().window_id())
        })
        .map(|history| {
            history
                .samples
                .iter()
                .copied()
                .collect()
        })
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

pub fn cadence(window: &Window, cx: &App) -> Cadence {
    cx.try_global::<Frames>()
        .and_then(|frames| {
            frames
                .0
                .get(&window.window_handle().window_id())
        })
        .map(|history| measure(history.gaps.iter().copied()))
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
        longest: gaps
            .iter()
            .copied()
            .max()
            .unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use gpui::TestAppContext;

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

    #[gpui::test]
    fn two_windows_read_their_own_samples_and_render_gaps(cx: &mut TestAppContext) {
        let first = cx
            .add_empty_window()
            .update(|window, _| window.window_handle());
        let second = cx
            .add_empty_window()
            .update(|window, _| window.window_handle());
        let first_id = first.window_id();
        let second_id = second.window_id();
        let start = Instant::now();
        let first_sample = Sample { total: ms(3), ..Sample::default() };
        let second_sample = Sample { total: ms(30), ..Sample::default() };
        cx.update(|cx| {
            let mut frames = Frames::default();
            frames
                .0
                .entry(first_id)
                .or_default()
                .record(start, Sample::default());
            frames
                .0
                .entry(second_id)
                .or_default()
                .record(start + ms(5), Sample::default());
            frames
                .0
                .entry(first_id)
                .or_default()
                .record(start + ms(16), first_sample);
            frames
                .0
                .entry(second_id)
                .or_default()
                .record(start + ms(505), second_sample);
            cx.set_global(frames);
        });
        cx.update(|cx| {
            first
                .update(cx, |_, window, cx| {
                    assert_eq!(samples(window, cx), vec![first_sample]);
                    assert_eq!(cadence(window, cx).last, ms(16));
                })
                .unwrap();
            second
                .update(cx, |_, window, cx| {
                    assert_eq!(samples(window, cx), vec![second_sample]);
                    assert_eq!(cadence(window, cx).last, ms(500));
                })
                .unwrap();
        });
    }
}
