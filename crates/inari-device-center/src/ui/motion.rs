//! Motion tokens.
//!
//! Two rules hold the whole system together. Motion never gates content: every
//! label and control is present and readable on the first frame, and animation
//! only moves things already on screen. And nothing loops unless it reports
//! live state — a spinner that keeps turning after the work finished is a lie
//! about the system.

use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

use gpui::{Animation, ease_in_out, linear, pulsating_between};

/// A state change the user caused and is watching: a selection moving, a panel
/// swapping. Long enough to read as motion, short enough to feel immediate.
pub const SWAP: Duration = Duration::from_millis(180);

/// Ambient motion that reports live state, such as the connection pulse. Slow
/// on purpose: at this rate it reads as breathing rather than as a widget
/// demanding attention.
pub const AMBIENT: Duration = Duration::from_millis(2600);

static REDUCED: AtomicBool = AtomicBool::new(false);

/// Whether non-essential motion may run.
///
/// GPUI 0.2.2 exposes no OS "Reduce motion" signal, so this is driven by
/// `INARI_REDUCED_MOTION` and the in-app preference rather than detected.
/// State that motion carries always has a static form too, so turning this off
/// removes movement and never removes information.
pub fn enabled() -> bool {
    !REDUCED.load(Ordering::Relaxed)
}

pub fn reduced() -> bool {
    REDUCED.load(Ordering::Relaxed)
}

pub fn set_reduced(reduced: bool) {
    REDUCED.store(reduced, Ordering::Relaxed);
}

pub fn init_from_environment() {
    if std::env::var_os("INARI_REDUCED_MOTION").is_some() {
        set_reduced(true);
    }
}

/// The connection pulse: a slow opacity breath between `min` and `max`.
pub fn pulse(min: f32, max: f32) -> Animation {
    Animation::new(AMBIENT)
        .repeat()
        .with_easing(pulsating_between(min, max))
}

/// A one-shot ease for an element entering or settling in place.
pub fn settle() -> Animation {
    Animation::new(SWAP).with_easing(ease_in_out)
}

/// One pass of the alert cascade, crest to crest.
pub const CASCADE: Duration = Duration::from_millis(1900);

/// The cascade's travelling crest.
///
/// The one loop in the app attached to a state that is not itself changing, so
/// it is the documented exception to the rule above rather than a quiet breach
/// of it. It marks the alert that blocks device work, it is the only thing on
/// screen doing so, and the alert reads identically with the motion off.
pub fn cascade() -> Animation {
    Animation::new(CASCADE)
        .repeat()
        .with_easing(linear)
}

/// Where cell `index` sits in the wave at `delta`, in turns.
///
/// Each cell runs the same repeating animation and reads its own offset from
/// this, so the whole grid stays phase-locked without a shared clock.
pub fn staggered_phase(delta: f32, index: usize, stagger: f32) -> f32 {
    (delta - index as f32 * stagger).rem_euclid(1.0)
}

/// A cosine crest: 0 at phase 0, 1 at the half turn, 0 again at 1.
pub fn pulse_wave(phase: f32) -> f32 {
    0.5 - 0.5 * (phase * std::f32::consts::TAU).cos()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_wave_travels_and_wraps() {
        // Cell 0 leads; later cells reach the same phase later in the turn.
        assert!((staggered_phase(0.5, 0, 0.1) - 0.5).abs() < f32::EPSILON);
        assert!((staggered_phase(0.5, 2, 0.1) - 0.3).abs() < 1e-6);
        // A cell whose offset runs past the start wraps rather than going
        // negative, which is what keeps the grid phase-locked.
        assert!(staggered_phase(0.0, 3, 0.1) > 0.0);
    }

    #[test]
    fn the_crest_peaks_once_per_turn() {
        assert!(pulse_wave(0.0).abs() < 1e-6);
        assert!((pulse_wave(0.5) - 1.0).abs() < 1e-6);
        assert!(pulse_wave(1.0).abs() < 1e-5);
    }
}
