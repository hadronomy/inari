//! The pixel wall, and the two clocks it needs.
//!
//! State lives in a thread-local keyed by element id, the way the hover fades in
//! [`motion`] do, so the wall needs no entity and can be dropped into any
//! preview page.

use std::{cell::RefCell, collections::HashMap, time::Instant};

use gpui::{
    App, InteractiveElement as _, IntoElement, MouseButton, MouseDownEvent, PaintEffect,
    ParentElement as _, Pixels, Point, RenderOnce, SharedString, Styled, Window, canvas, div, px,
};

use super::{
    effect::Ripple,
    motion,
    theme::{ActiveTheme as _, Theme},
};

/// When a wall first appeared, and where it was last struck.
struct Wall {
    opened: Instant,
    strike: Option<(Point<Pixels>, Instant)>,
}

thread_local! {
    static WALLS: RefCell<HashMap<SharedString, Wall>> = RefCell::new(HashMap::new());
}

fn strike(key: SharedString, position: Point<Pixels>) {
    WALLS.with(|walls| {
        if let Some(wall) = walls.borrow_mut().get_mut(&key) {
            wall.strike = Some((position, Instant::now()));
        }
    });
}

/// Seconds since the wall opened, and since it was last struck.
///
/// Registers the wall on first sight, so the caller never has to.
fn clocks(key: SharedString) -> (f32, Option<(Point<Pixels>, f32)>) {
    WALLS.with(|walls| {
        let mut walls = walls.borrow_mut();
        let wall = walls
            .entry(key)
            .or_insert_with(|| Wall { opened: Instant::now(), strike: None });
        let age = wall
            .strike
            .map(|(position, at)| (position, at.elapsed().as_secs_f32()));
        (wall.opened.elapsed().as_secs_f32(), age)
    })
}

/// A wall of pixel cells that carries a shock outward from where it is clicked.
#[derive(IntoElement)]
pub struct RippleWall {
    id: SharedString,
}

pub fn wall(id: impl Into<SharedString>) -> RippleWall {
    RippleWall { id: id.into() }
}

impl RenderOnce for RippleWall {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        // Copied out rather than captured: the closure outlives the borrow.
        let accent = cx.inari().accent;
        let key = self.id.clone();
        let recorder = key.clone();

        div()
            .id(self.id)
            .size_full()
            .cursor_pointer()
            .on_mouse_down(
                MouseButton::Left,
                move |event: &MouseDownEvent, window: &mut Window, _| {
                    strike(recorder.clone(), event.position);
                    window.refresh();
                },
            )
            .child(
                canvas(
                    |_, _, _| (),
                    move |bounds, _, window: &mut Window, _| {
                        let (time, last) = clocks(key.clone());
                        // A strike is recorded in window coordinates; the shader
                        // measures from the wall's own corner.
                        let (strike_x, strike_y, age) = match last {
                            Some((position, age)) => (
                                f32::from(position.x - bounds.origin.x),
                                f32::from(position.y - bounds.origin.y),
                                age,
                            ),
                            None => (0.0, 0.0, -1.0),
                        };
                        window.paint_effect(PaintEffect::new(
                            bounds,
                            &Ripple {
                                // A still wall under reduced motion: no shimmer,
                                // and a strike that has already settled.
                                time: if motion::reduced() { 0.0 } else { time },
                                cell: 9.0,
                                strike_x,
                                strike_y,
                                age: if motion::reduced() { -1.0 } else { age },
                                tint: accent,
                            },
                        ));

                        // The wall shimmers at rest and rings after a strike, so
                        // it asks for the next frame until motion is switched
                        // off entirely.
                        if !motion::reduced() {
                            window.request_animation_frame();
                        }
                    },
                )
                .size_full(),
            )
            .rounded(px(Theme::RADIUS_CARD))
    }
}
