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

/// One turn of a loader. Fast enough to read as working, slow enough that it
/// does not buzz beside the sentence explaining what it is working on.
pub const SPIN: Duration = Duration::from_millis(1100);

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

/// One continuous turn, for a loader that is reporting real work.
///
/// Linear on purpose: an eased rotation appears to stall twice per turn, which
/// reads as the work stalling.
pub fn spin() -> Animation {
    Animation::new(SPIN)
        .repeat()
        .with_easing(linear)
}
