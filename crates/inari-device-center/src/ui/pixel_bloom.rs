//! The pixel wall, and the clocks it needs.
//!
//! State lives in a thread-local keyed by element id, the way the hover fades in
//! [`motion`] do, so the wall needs no entity and drops into any page.

use std::{cell::RefCell, collections::HashMap, time::Instant};

use gpui::{
    App, AppContext as _, Entity, InteractiveElement as _, IntoElement, MouseMoveEvent,
    PaintEffect, ParentElement as _, Pixels, Point, RenderOnce, SharedString,
    StatefulInteractiveElement as _, Styled, Window, canvas, div, px,
};
use gpui_component::StyledExt as _;
use gpui_component::slider::{Slider, SliderState, SliderValue};

use super::{
    content::Typography as _,
    effect::PixelBloom,
    motion,
    theme::{ActiveTheme as _, Theme},
};

/// What the wall looks like, separated from what it is doing, so a dev page can
/// turn the knobs while the animation runs.
#[derive(Copy, Clone, Debug, PartialEq)]
pub struct Tuning {
    /// Bloom from where the pointer entered, rather than always from the centre.
    pub from_pointer: bool,
    /// Grid spacing in logical pixels.
    pub gap: f32,
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
    direction: f32,
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
            direction: 1.0,
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
        let direction = if hovered { 1.0 } else { -1.0 };
        if wall.direction == direction {
            return false;
        }
        // The bloom starts where the pointer came in, and unwinds towards where
        // it was when it left.
        wall.origin = wall.pointer;
        wall.turned = Instant::now();
        wall.direction = direction;
        true
    })
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
                        let (time, origin, age, direction) = with_wall(key.clone(), |wall| {
                            (
                                wall.opened.elapsed().as_secs_f32(),
                                wall.origin
                                    .unwrap_or_else(|| bounds.center()),
                                wall.turned.elapsed().as_secs_f32(),
                                wall.direction,
                            )
                        });

                        window.paint_effect(PaintEffect::new(
                            bounds,
                            &PixelBloom {
                                // A still wall under reduced motion: no breath,
                                // and a bloom already at its destination.
                                time: if reduced { 0.0 } else { time },
                                gap: tuning.gap,
                                origin_x: f32::from(origin.x - bounds.origin.x),
                                origin_y: f32::from(origin.y - bounds.origin.y),
                                dot_size: tuning.dot_size,
                                spread: tuning.spread,
                                shimmer: if reduced { 0.0 } else { tuning.shimmer },
                                glow: tuning.glow,
                                age: if reduced { f32::MAX } else { age },
                                direction,
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

/// The sliders and the switch that drive a wall's [`Tuning`].
///
/// Owned by whatever page shows the wall, because a slider needs entity state
/// and the wall itself deliberately does not.
pub struct WallControls {
    from_pointer: bool,
    gap: Entity<SliderState>,
    dot_size: Entity<SliderState>,
    spread: Entity<SliderState>,
    shimmer: Entity<SliderState>,
    glow: Entity<SliderState>,
}

impl WallControls {
    pub fn new(cx: &mut App) -> Self {
        let resting = Tuning::default();
        let slider = |min: f32, max: f32, step: f32, value: f32, cx: &mut App| {
            cx.new(|_| {
                SliderState::new()
                    .min(min)
                    .max(max)
                    .step(step)
                    .default_value(value)
            })
        };
        Self {
            from_pointer: resting.from_pointer,
            gap: slider(4.0, 24.0, 1.0, resting.gap, cx),
            dot_size: slider(0.1, 0.9, 0.02, resting.dot_size, cx),
            spread: slider(200.0, 4000.0, 50.0, resting.spread, cx),
            shimmer: slider(0.0, 8.0, 0.1, resting.shimmer, cx),
            glow: slider(0.0, 1.0, 0.05, resting.glow, cx),
        }
    }

    /// The wall's settings as the sliders currently stand.
    pub fn tuning(&self, cx: &App) -> Tuning {
        let read = |state: &Entity<SliderState>| match state.read(cx).value() {
            SliderValue::Single(value) => value,
            // A range slider cannot be built here, so its start is as good an
            // answer as any and better than refusing to draw.
            SliderValue::Range(start, _) => start,
        };
        Tuning {
            from_pointer: self.from_pointer,
            gap: read(&self.gap),
            dot_size: read(&self.dot_size),
            spread: read(&self.spread),
            shimmer: read(&self.shimmer),
            glow: read(&self.glow),
        }
    }

    pub fn toggle_origin(&mut self) {
        self.from_pointer = !self.from_pointer;
    }

    /// Whether the bloom starts at the pointer rather than the centre.
    pub fn blooms_from_pointer(&self) -> bool {
        self.from_pointer
    }

    pub fn render(&self, cx: &App) -> impl IntoElement {
        let theme = cx.inari();
        let tuning = self.tuning(cx);
        let row = |name: &'static str, reading: String, slider: Slider| {
            div()
                .h_flex()
                .items_center()
                .gap(px(Theme::SPACE_MD))
                .child(
                    div()
                        .w(px(76.0))
                        .text_caption()
                        .text_color(theme.text_secondary)
                        .child(name),
                )
                .child(div().flex_1().child(slider))
                .child(
                    div()
                        .w(px(56.0))
                        .text_technical()
                        .text_color(theme.text_tertiary)
                        .child(reading),
                )
        };

        div()
            .v_flex()
            .gap(px(Theme::SPACE_SM))
            .w_full()
            .child(row("Gap", format!("{:.0}px", tuning.gap), Slider::new(&self.gap)))
            .child(row("Dot size", format!("{:.2}", tuning.dot_size), Slider::new(&self.dot_size)))
            .child(row("Spread", format!("{:.0}", tuning.spread), Slider::new(&self.spread)))
            .child(row("Shimmer", format!("{:.1}", tuning.shimmer), Slider::new(&self.shimmer)))
            .child(row("Glow", format!("{:.2}", tuning.glow), Slider::new(&self.glow)))
    }
}
