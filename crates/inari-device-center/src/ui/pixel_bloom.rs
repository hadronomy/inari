//! The pixel wall, and the clocks it needs.
//!
//! State lives in a thread-local keyed by element id, the way the hover fades in
//! [`motion`] do, so the wall needs no entity and drops into any page.

use std::{cell::RefCell, collections::HashMap, time::Instant};

use gpui::{
    App, InteractiveElement as _, IntoElement, MouseMoveEvent, PaintEffect,
    ParentElement as _, Pixels, Point, RenderOnce, SharedString,
    StatefulInteractiveElement as _, Styled, Window, canvas, div, px,
};

use super::{
    effect::{PixelBloom, Pointer},
    motion,
    theme::{ActiveTheme as _, Theme},
};

/// What the wall looks like, separated from what it is doing, so a dev page can
/// turn the knobs while the animation runs.
#[derive(Copy, Clone, Debug, PartialEq)]
pub struct Tuning {
    /// Bloom from where the pointer entered, rather than always from the centre.
    pub from_pointer: bool,
    /// Grid spacing.
    pub gap: Pixels,
    /// The largest a dot grows, as a fraction of its cell.
    pub dot_size: f32,
    /// Device pixels the bloom front travels per second.
    pub spread: f32,
    /// How fast an arrived dot oscillates its size.
    pub shimmer: f32,
    /// Strength of the halo outside each dot.
    pub glow: f32,
}

impl Default for Tuning {
    fn default() -> Self {
        let resting = PixelBloom::default();
        Self {
            from_pointer: true,
            gap: resting.gap,
            dot_size: resting.dot_size,
            spread: resting.spread,
            shimmer: resting.shimmer,
            glow: resting.glow,
        }
    }
}

struct Wall {
    /// For the idle breath of lit cells.
    opened: Instant,
    /// Where the pointer was when it last entered, which is where the bloom
    /// starts. Held in window coordinates; the canvas knows the bounds. `None`
    /// means the wall's centre.
    origin: Option<Point<Pixels>>,
    /// The pointer's latest position, so an entry has an origin to freeze.
    /// `None` until the pointer has been over the wall, in which case the bloom
    /// starts from the wall's own centre.
    pointer: Option<Point<Pixels>>,
    /// When the pointer last entered or left.
    turned: Instant,
    /// `1` inside, `-1` after leaving, `0` before the wall has been pointed at.
    state: Pointer,
}

impl Default for Wall {
    fn default() -> Self {
        Self {
            opened: Instant::now(),
            origin: None,
            pointer: None,
            turned: Instant::now(),
            // The wall introduces itself: it blooms in from its centre when it
            // first appears, rather than waiting to be pointed at.
            state: Pointer::Inside,
        }
    }
}

thread_local! {
    static WALLS: RefCell<HashMap<SharedString, Wall>> = RefCell::new(HashMap::new());
}

fn with_wall<R>(key: SharedString, act: impl FnOnce(&mut Wall) -> R) -> R {
    WALLS.with(|walls| {
        act(walls
            .borrow_mut()
            .entry(key)
            .or_default())
    })
}

fn track(key: SharedString, position: Point<Pixels>) {
    with_wall(key, |wall| wall.pointer = Some(position));
}

/// Returns whether anything changed, so the caller only redraws when it did.
fn turn(key: SharedString, hovered: bool) -> bool {
    with_wall(key, |wall| {
        let state = if hovered { Pointer::Inside } else { Pointer::Left };
        if wall.state == state {
            return false;
        }
        // The bloom starts where the pointer came in, and unwinds towards where
        // it was when it left.
        wall.origin = wall.pointer;
        wall.turned = Instant::now();
        wall.state = state;
        true
    })
}

/// Bloom the wall again from wherever its origin now is.
///
/// A tuning control that changes the origin has nothing to show until the next
/// bloom, because the origin only sets each dot's delay while one is running.
pub fn restart(key: impl Into<SharedString>) {
    with_wall(key.into(), |wall| {
        wall.turned = Instant::now();
        wall.state = Pointer::Inside;
    });
}

/// A wall of pixel cells that blooms outward from wherever the pointer enters.
#[derive(IntoElement)]
pub struct PixelWall {
    id: SharedString,
    tuning: Tuning,
}

pub fn wall(id: impl Into<SharedString>) -> PixelWall {
    PixelWall { id: id.into(), tuning: Tuning::default() }
}

impl PixelWall {
    pub fn tuning(mut self, tuning: Tuning) -> Self {
        self.tuning = tuning;
        self
    }
}

impl RenderOnce for PixelWall {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        // Copied out rather than captured: the closures outlive the borrow.
        let theme = cx.inari();
        let (near, far) = (theme.accent, theme.text_tertiary);

        let key = self.id.clone();
        let mover = key.clone();
        let hoverer = key.clone();
        let tuning = self.tuning;

        div()
            .id(self.id)
            .size_full()
            .rounded(px(Theme::RADIUS_CARD))
            .on_mouse_move(move |event: &MouseMoveEvent, _, _| {
                track(mover.clone(), event.position);
            })
            .on_hover(move |hovered: &bool, window: &mut Window, _| {
                if turn(hoverer.clone(), *hovered) {
                    window.refresh();
                }
            })
            .child(
                canvas(
                    |_, _, _| (),
                    move |bounds, _, window: &mut Window, _| {
                        let reduced = motion::reduced();
                        let (time, origin, age, state) = with_wall(key.clone(), |wall| {
                            (
                                wall.opened.elapsed().as_secs_f32(),
                                wall.origin
                                    .unwrap_or_else(|| bounds.center()),
                                wall.turned.elapsed().as_secs_f32(),
                                wall.state,
                            )
                        });

                        window.paint_effect(PaintEffect::new(
                            bounds,
                            &PixelBloom {
                                // A still wall under reduced motion: no breath,
                                // and a bloom already at its destination.
                                time: if reduced { 0.0 } else { time },
                                gap: tuning.gap,
                                origin: origin - bounds.origin,
                                dot_size: tuning.dot_size,
                                spread: tuning.spread,
                                shimmer: if reduced { 0.0 } else { tuning.shimmer },
                                glow: tuning.glow,
                                age: if reduced { f32::MAX } else { age },
                                pointer: state,
                                near,
                                far,
                            },
                        ));

                        // Lit cells breathe and the bloom is still travelling,
                        // so the wall asks for the next frame until motion is
                        // switched off entirely.
                        if !reduced {
                            window.request_animation_frame();
                        }
                    },
                )
                .size_full(),
            )
    }
}

crate::story! {
    id: "effect.pixel-wall",
    name: "Pixel wall",
    scope: crate::dev::Scope::Effects,
    about: "Point at it. Every number the shader takes is a knob, so tuning is \
            a drag rather than a rebuild.",
    render: |dial, _window, _cx| {
        use gpui::{ParentElement as _, Styled as _};

        let tuning = Tuning {
            from_pointer: dial.flag("From the pointer", true),
            gap: px(dial.range("Gap", 8.0, 4.0..=24.0)),
            dot_size: dial.range("Dot size", Tuning::default().dot_size, 0.1..=0.9),
            spread: dial.range("Spread", Tuning::default().spread, 200.0..=4000.0),
            shimmer: dial.range("Shimmer", Tuning::default().shimmer, 0.0..=8.0),
            glow: dial.range("Glow", Tuning::default().glow, 0.0..=1.0),
        };
        if dial.press("Replay") {
            restart("story-pixel-wall");
        }

        div()
            .h(px(380.0))
            .w_full()
            .child(wall("story-pixel-wall").tuning(tuning))
            .into_any_element()
    },
}
