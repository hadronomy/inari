//! Motion tokens.
//!
//! Two rules hold the whole system together. Motion never gates content: every
//! label and control is present and readable on the first frame, and animation
//! only moves things already on screen. And nothing loops unless it reports
//! live state — a spinner that keeps turning after the work finished is a lie
//! about the system.

use std::{
    cell::RefCell,
    collections::HashMap,
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

use gpui::{Animation, Hsla, SharedString, ease_in_out, linear, pulsating_between};

/// A state change the user caused and is watching: a selection moving, a panel
/// swapping. Long enough to read as motion, short enough to feel immediate.
pub const SWAP: Duration = Duration::from_millis(180);

/// Ambient motion that reports live state, such as the connection pulse. Slow
/// on purpose: at this rate it reads as breathing rather than as a widget
/// demanding attention.
pub const AMBIENT: Duration = Duration::from_millis(2600);

/// How long a hover wash takes to fade in or out. The duration of CSS
/// `transition-colors` on a Tailwind default, which is the feel every desktop
/// and web interface the operator already uses has trained into them.
pub const HOVER: Duration = Duration::from_millis(150);

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
///
/// Cost note: GPUI 0.2.2 has no per-animation frame cap, so a repeating
/// animation re-renders the whole window at the display's refresh rate for as
/// long as it runs. Comet solved that with a shared ~30fps pulse clock; this
/// app carries the alert cascade, the slow pulse, and the gate's live wire —
/// each reporting state the operator is meant to read, which a GPU from the
/// last decade composites for free. Revisit only if another loop earns its
/// place less clearly than these.
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

// ---- hover fades ----
//
// A hover wash that snaps is the tell that separates a GPUI app from the web
// and desktop software around it: style-level `.hover()` has no transition, so
// the wash lands at full strength in one frame. This module keeps a per-element
// fade and hands out the blended color, which rebuilds CSS
// `transition-colors` with no extra event loop. Comet's motion module carries
// the same recipe (a keyed fade store, a 150ms ease, re-anchoring mid-flight).
//
// One property matters more than it looks: the value is a pure function of
// wall-clock time, never an increment applied per frame. A dropped or delayed
// frame then lands on the exact right position instead of losing motion,
// which is what keeps fast pointer travel from reading as a stuttering trail.

struct HoverFade {
    /// The washed fraction when this leg began.
    from: f32,
    target: f32,
    /// When this leg began. A leg is one unbroken run toward a target;
    /// reversing the pointer starts a new leg from wherever the old one was.
    started: Instant,
}

impl HoverFade {
    /// The washed fraction at `now`. Exactly `target` once the leg has run its
    /// duration, so a fade finishes on the clock rather than asymptotically.
    fn value(&self, now: Instant) -> f32 {
        let progress = (now - self.started).as_secs_f32() / HOVER.as_secs_f32();
        if progress >= 1.0 {
            self.target
        } else {
            self.from + (self.target - self.from) * ease_in_out(progress)
        }
    }

    fn settled(&self, now: Instant) -> bool {
        self.from == self.target || (now - self.started) >= HOVER
    }
}

thread_local! {
    static HOVER_FADES: RefCell<HashMap<SharedString, HoverFade>> =
        RefCell::new(HashMap::default());
}

/// Record where the pointer went for `key`. Returns whether a fade started or
/// reversed, so the caller can repaint and resume the frame loop.
///
/// Callers schedule that repaint with [`gpui::Window::refresh`] — never with
/// `request_animation_frame`, which reads the current view and so only exists
/// mid-paint. A hover listener runs in event dispatch, where that call panics.
/// The render-phase loops on the views hosting fading elements keep the
/// started fade walking.
pub fn hover_set(key: impl Into<SharedString>, hovered: bool) -> bool {
    let key = key.into();
    let target = if hovered { 1.0 } else { 0.0 };
    HOVER_FADES.with(|fades| {
        let mut fades = fades.borrow_mut();
        match fades.get_mut(&key) {
            Some(fade) if fade.target == target => false,
            Some(fade) => {
                // Re-anchor: the new leg continues from wherever the pointer
                // left the old one, so reversing mid-flight never jumps.
                fade.from = fade.value(Instant::now());
                fade.target = target;
                fade.started = Instant::now();
                true
            },
            None => {
                fades.insert(key, HoverFade { from: 0.0, target, started: Instant::now() });
                target > 0.0
            },
        }
    })
}

/// The wash for `key` at this frame, eased toward wherever the pointer last
/// went.
///
/// The color itself never changes — only its alpha — which is premultiplied
/// blending by construction: a wash fading in from transparent brightens
/// without passing through grey. An element with no recorded fade simply gets
/// the color at rest, so first paint is free.
pub fn hover_blend(key: impl Into<SharedString>, wash: Hsla) -> Hsla {
    let alpha = wash.a * fade_fraction(key);
    Hsla { a: alpha, ..wash }
}

/// The eased 0..1 fraction for `key`, advanced toward wherever the pointer or
/// focus last left it.
///
/// [`hover_blend`] is this applied to a color's alpha; call this directly when
/// one fade drives more than one color, such as a field whose border and ring
/// rise together on focus. An entry nobody drives retires once it settles, so
/// the store holds only fades that can still move.
pub fn fade_fraction(key: impl Into<SharedString>) -> f32 {
    let key = key.into();
    HOVER_FADES.with(|fades| {
        let mut fades = fades.borrow_mut();
        let Some(fade) = fades.get_mut(&key) else {
            return 0.0;
        };
        let now = Instant::now();
        let washed = fade.value(now);
        if fade.settled(now) && fade.target == 0.0 {
            fades.remove(&key);
        }
        washed
    })
}

/// The target `key` was last pointed at, without advancing anything.
///
/// Lets a render keep a fade aimed at a prop that has no event of its own: a
/// field whose invalid state arrives with data, not with a pointer. Compare,
/// and call [`hover_set`] when the two disagree.
pub fn fade_target(key: impl Into<SharedString>) -> f32 {
    let key = key.into();
    HOVER_FADES.with(|fades| {
        fades
            .borrow()
            .get(&key)
            .map(|fade| fade.target)
            .unwrap_or(0.0)
    })
}

/// Whether any wash is mid-flight and the window owes itself another frame.
///
/// The root view calls this once per render and requests the next frame while
/// it is true, which is what walks the fades forward. A window with nothing
/// hovered schedules nothing at all.
pub fn hover_fades_live() -> bool {
    let now = Instant::now();
    HOVER_FADES.with(|fades| {
        fades
            .borrow()
            .values()
            .any(|fade| !fade.settled(now))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpui::hsla;

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

    #[test]
    fn a_hover_wash_blends_from_transparent_toward_its_target() {
        let wash = hsla(0.0, 0.0, 1.0, 0.06);
        let key = "test-fade-in";
        assert!(hover_set(key, true));
        // Whatever time has passed, the blend is the wash's own hue at no more
        // than its full strength.
        let blend = hover_blend(key, wash);
        assert_eq!(blend.h, wash.h);
        assert!((0.0..=wash.a).contains(&blend.a), "{}", blend.a);
        // Reading again without letting time pass never overshoots.
        let again = hover_blend(key, wash);
        assert!(again.a <= wash.a);
    }

    #[test]
    fn an_unhovered_element_blends_to_transparent_and_stops_being_tracked() {
        let wash = hsla(0.0, 0.0, 1.0, 0.06);
        let key = "test-fade-rest";
        assert_eq!(hover_blend(key, wash).a, 0.0);
        hover_set(key, true);
        hover_set(key, false);
        // Pretend the leave happened longer ago than the whole fade, so the
        // next read settles it.
        HOVER_FADES.with(|fades| {
            fades
                .borrow_mut()
                .get_mut(key)
                .unwrap()
                .started = Instant::now() - HOVER - Duration::from_secs(1);
        });
        let _ = hover_blend(key, wash);
        let settled = HOVER_FADES.with(|fades| !fades.borrow().contains_key(key));
        assert!(settled);
    }

    #[test]
    fn a_fade_finishes_exactly_on_its_own_clock() {
        let wash = hsla(0.0, 0.0, 1.0, 0.06);
        let key = "test-fade-clock";
        hover_set(key, true);
        // Midway through the fade the blend sits between rest and full, and
        // once the duration has passed it is exactly the target — no
        // asymptotic tail that a fast pointer reads as a stutter.
        HOVER_FADES.with(|fades| {
            fades
                .borrow_mut()
                .get_mut(key)
                .unwrap()
                .started = Instant::now() - HOVER / 2;
        });
        let midway = hover_blend(key, wash);
        assert!(midway.a > 0.0 && midway.a < wash.a, "{}", midway.a);
        HOVER_FADES.with(|fades| {
            fades
                .borrow_mut()
                .get_mut(key)
                .unwrap()
                .started = Instant::now() - HOVER - Duration::from_secs(1);
        });
        let done = hover_blend(key, wash);
        assert_eq!(done.a, wash.a);
    }

    #[test]
    fn reversing_a_fade_mid_flight_reanchors_without_a_jump() {
        let wash = hsla(0.0, 0.0, 1.0, 0.06);
        let key = "test-fade-reverse";
        hover_set(key, true);
        // Halfway in, the wash is visibly present.
        HOVER_FADES.with(|fades| {
            fades
                .borrow_mut()
                .get_mut(key)
                .unwrap()
                .started = Instant::now() - HOVER / 2;
        });
        let rising = hover_blend(key, wash);
        assert!(rising.a > 0.0, "{}", rising.a);
        // Leaving at that instant continues from the same place instead of
        // snapping back to rest.
        hover_set(key, false);
        let falling = hover_blend(key, wash);
        assert!(falling.a >= rising.a - f32::EPSILON, "{} then {}", rising.a, falling.a);
    }
}
