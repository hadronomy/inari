//! The pixel wall, and the clocks it needs.
//!
//! State lives in a thread-local keyed by element id, the way the hover fades in
//! [`motion`] do, so the wall needs no entity and drops into any page.

use std::{cell::RefCell, collections::HashMap, time::Instant};

use gpui::{
    App, InteractiveElement as _, IntoElement, MouseMoveEvent, PaintEffect, ParentElement as _,
    Pixels, Point, RenderOnce, SharedString, StatefulInteractiveElement as _, Styled, Window,
    canvas, div, px,
};

use super::{
    effect::PixelBloom,
    motion,
    theme::{ActiveTheme as _, Theme},
};

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
}

pub fn wall(id: impl Into<SharedString>) -> PixelWall {
    PixelWall { id: id.into() }
}

impl RenderOnce for PixelWall {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        // Copied out rather than captured: the closures outlive the borrow.
        let theme = cx.inari();
        let (near, far) = (theme.accent, theme.text_tertiary);

        let key = self.id.clone();
        let mover = key.clone();
        let hoverer = key.clone();

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
                                cell: 9.0,
                                origin_x: f32::from(origin.x - bounds.origin.x),
                                origin_y: f32::from(origin.y - bounds.origin.y),
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
