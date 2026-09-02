//! The controls the knob panel is made of.
//!
//! Shaped after DialKit (joshpuckett/dialkit), read at `1d0ca134`: its
//! `src/components/Slider.tsx` for the interaction and `src/styles/theme.css`
//! for the geometry. The one idea worth copying above all others is the row:
//!
//! > Every control is a single 36px row with an 8px radius on a five-percent
//! > white surface. The label sits *inside* the row on the left, the value
//! > *inside* it on the right, and a slider is that same row with a fill and a
//! > handle drawn behind them.
//!
//! That is why a DialKit panel reads as one instrument rather than as a form.
//! A label above its control, which is what a settings screen does, doubles the
//! vertical space and makes twelve knobs unreadable.
//!
//! The arithmetic lives here as free functions, apart from the elements, because
//! it is the part that can be wrong in ways a screenshot will not show: which
//! step a click lands on, how far the band stretches, how many decimals a value
//! prints. Those get tests. The painting does not.

use std::ops::RangeInclusive;

/// One control row. DialKit's `--dial-row-height`.
pub const ROW_HEIGHT: f32 = 36.0;
/// `--dial-radius`.
pub const ROW_RADIUS: f32 = 8.0;
/// The inset the label and the value sit at.
pub const ROW_INSET: f32 = 10.0;
/// Every control prints at one size. DialKit sets 13px on all of them.
pub const TEXT_SIZE: f32 = 13.0;

/// The handle: 3px wide, 20px tall, fully round.
pub const HANDLE_WIDTH: f32 = 3.0;
pub const HANDLE_HEIGHT: f32 = 20.0;
/// At rest the handle is a quarter of its width, so it reads as a tick rather
/// than as a grip until the pointer arrives.
pub const HANDLE_RESTING_SCALE: f32 = 0.25;

/// A hash mark: 1px wide, 8px tall.
pub const MARK_WIDTH: f32 = 1.0;
pub const MARK_HEIGHT: f32 = 8.0;

/// Pointer travel that separates a click from a drag.
pub const CLICK_THRESHOLD: f32 = 3.0;
/// How far past the end the pointer travels before the band starts to stretch.
const DEAD_ZONE: f32 = 32.0;
/// The overshoot at which the band reaches its full stretch.
const MAX_CURSOR_RANGE: f32 = 200.0;
/// The furthest the band stretches.
const MAX_STRETCH: f32 = 8.0;
/// Clearance between the handle and the text it would otherwise sit under.
const HANDLE_BUFFER: f32 = 8.0;

/// A span with at most this many steps is discrete: its marks are its steps and
/// a click lands on one of them. Above it the slider reads as continuous and a
/// click is magnetic to the nearest tenth instead.
const DISCRETE_STEPS: f32 = 10.0;

/// How many decimals a value prints, from the size of its step.
///
/// A step of 1 prints no decimals, 0.1 prints one, 0.01 prints two. Printing
/// more than the step can reach is noise: a slider that moves in tenths has no
/// business showing hundredths.
pub fn decimals_for_step(step: f32) -> usize {
    if step <= 0.0 || step >= 1.0 {
        return 0;
    }
    // `0.1` is not exact in binary, so walk the step up by tens and stop when
    // it reaches a whole number rather than trusting `log10`.
    let mut decimals = 0;
    let mut scaled = step;
    while scaled < 1.0 && decimals < 6 {
        scaled *= 10.0;
        decimals += 1;
    }
    decimals
}

/// Round `value` to the nearest multiple of `step`.
pub fn round_to_step(value: f32, step: f32) -> f32 {
    if step <= 0.0 {
        return value;
    }
    (value / step).round() * step
}

/// Where `value` sits in `span`, as 0..1.
pub fn fraction(value: f32, span: &RangeInclusive<f32>) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    if hi <= lo {
        return 0.0;
    }
    ((value - lo) / (hi - lo)).clamp(0.0, 1.0)
}

/// The value at `fraction` through `span`.
pub fn value_at(fraction: f32, span: &RangeInclusive<f32>) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    (lo + fraction.clamp(0.0, 1.0) * (hi - lo)).clamp(lo, hi)
}

/// The number of steps `span` holds.
pub fn steps(span: &RangeInclusive<f32>, step: f32) -> f32 {
    if step <= 0.0 {
        return f32::INFINITY;
    }
    (*span.end() - *span.start()) / step
}

/// Whether the span is coarse enough that every step gets its own mark.
pub fn is_discrete(span: &RangeInclusive<f32>, step: f32) -> bool {
    steps(span, step) <= DISCRETE_STEPS
}

/// Where a click lands.
///
/// A click is not a drag: it is a request for a round number. On a coarse span
/// it lands on the nearest step; on a fine one it is magnetic to the nearest
/// tenth of the span, so clicking near the middle of a 0..1 slider gives 0.5
/// rather than 0.4913. Dragging is exact — only the click is opinionated.
pub fn snap_on_click(value: f32, span: &RangeInclusive<f32>, step: f32) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    if is_discrete(span, step) {
        (lo + ((value - lo) / step).round() * step).clamp(lo, hi)
    } else {
        snap_to_decile(value, span)
    }
}

/// The nearest tenth of the span.
pub fn snap_to_decile(value: f32, span: &RangeInclusive<f32>) -> f32 {
    let (lo, hi) = (*span.start(), *span.end());
    if hi <= lo {
        return lo;
    }
    let decile = (hi - lo) / 10.0;
    (lo + ((value - lo) / decile).round() * decile).clamp(lo, hi)
}

/// The marks drawn behind the track, as fractions of its width.
///
/// A coarse span marks every step; a fine one marks the tenths a click is
/// magnetic to, so the marks and the snapping tell the same story.
pub fn marks(span: &RangeInclusive<f32>, step: f32) -> Vec<f32> {
    if is_discrete(span, step) {
        let count = steps(span, step).round() as usize;
        (1..count)
            .map(|index| index as f32 / count as f32)
            .collect()
    } else {
        (1..10)
            .map(|tenth| tenth as f32 / 10.0)
            .collect()
    }
}

/// How far the whole track slides when the pointer is dragged past its end.
///
/// Square-rooted, so the first pixels past the dead zone give most of the
/// movement and the band goes stiff as it approaches its limit — the shape
/// every rubber band on every platform has. `sign` is -1 past the left end and
/// +1 past the right.
pub fn rubber_stretch(distance_past: f32, sign: f32) -> f32 {
    let overflow = (distance_past - DEAD_ZONE).max(0.0);
    sign * MAX_STRETCH * (overflow / MAX_CURSOR_RANGE).min(1.0).sqrt()
}

/// Whether the handle would sit under the label or the value.
///
/// Both are painted inside the track, so at the ends the handle collides with
/// them. It gets out of the way rather than crossing them.
pub fn dodges(fraction: f32, track: f32, label_width: f32, value_width: f32) -> bool {
    if track <= 0.0 {
        return false;
    }
    let left = (ROW_INSET + label_width + HANDLE_BUFFER) / track;
    let right = (track - ROW_INSET - value_width - HANDLE_BUFFER) / track;
    fraction < left || fraction > right
}

/// The handle's opacity: invisible at rest, half-lit under the pointer, nearly
/// solid while dragging, and a ghost when it is dodging text.
pub fn handle_opacity(active: bool, dragging: bool, dodging: bool) -> f32 {
    if !active {
        0.0
    } else if dodging {
        0.1
    } else if dragging {
        0.9
    } else {
        0.5
    }
}

/// Print `value` at the precision its step can reach.
pub fn format_value(value: f32, step: f32) -> String {
    format!("{value:.*}", decimals_for_step(step))
}


// ---- the elements ----

use std::{cell::RefCell, collections::HashMap, rc::Rc, time::{Duration, Instant}};

use gpui::{
    App, Bounds, Hsla, InteractiveElement as _, IntoElement, MouseButton, ParentElement as _,
    Pixels, Point, RenderOnce, SharedString, StatefulInteractiveElement as _, Styled as _, Window,
    canvas, deferred, div, prelude::FluentBuilder as _, px, relative,
};

use gpui_component::Sizable as _;

use crate::ui::{
    motion::{self, CubicBezier},
    theme::{ActiveTheme as _, Theme},
};

/// What a control does when it is moved.
///
/// Every control takes one of these rather than an event enum, because a knob
/// has exactly one thing to say and the caller always knows which knob it built.
type Change<T> = Rc<dyn Fn(T, &mut Window, &mut App)>;
/// What an action does when it is pressed.
type Press = Rc<dyn Fn(&mut Window, &mut App)>;

/// How long a click's snap takes to travel, and on what curve.
///
/// DialKit springs (stiffness 300, damping 25, mass 0.8). GPUI 0.2.2 carries no
/// spring, so this is the nearest curve in the vocabulary the rest of the
/// application already speaks: ease-out-expo, which leaves fast and settles
/// long, the way an over-damped spring does.
const SNAP: Duration = Duration::from_millis(280);
const EASE_SNAP: CubicBezier = CubicBezier::new(0.16, 1.0, 0.3, 1.0);

thread_local! {
    /// Where each track was laid out. A pointer handler is given a position and
    /// nothing else, so the geometry has to be recorded as it is painted.
    static TRACKS: RefCell<HashMap<SharedString, Bounds<Pixels>>> =
        RefCell::new(HashMap::new());
    /// The measured width of each row's label and value, so the handle knows
    /// what it has to dodge. Measured rather than guessed: a label's width is a
    /// property of the face and the string, and estimating it puts the handle
    /// in the wrong place for exactly the strings nobody tested.
    static TEXT: RefCell<HashMap<SharedString, (f32, f32)>> = RefCell::new(HashMap::new());
    /// The slider under the pointer, if one is being pressed.
    static GRIP: RefCell<Option<Grip>> = const { RefCell::new(None) };
    /// Snaps in flight, keyed by slider.
    static SNAPS: RefCell<HashMap<SharedString, Travel>> = RefCell::new(HashMap::new());
}

/// A press in progress.
struct Grip {
    key: SharedString,
    /// Where the press landed, so a click can be told from a drag.
    from: Point<Pixels>,
    /// Set once the pointer has travelled past the click threshold.
    dragging: bool,
    span: std::ops::RangeInclusive<f32>,
    step: f32,
    /// How far the band is stretched past an end.
    stretch: f32,
    change: Change<f32>,
}

/// A fill travelling to where a click asked for.
#[derive(Clone, Copy)]
struct Travel {
    from: f32,
    to: f32,
    started: Instant,
}

impl Travel {
    fn at(&self, now: Instant) -> f32 {
        let elapsed = now.duration_since(self.started).as_secs_f32();
        let progress = (elapsed / SNAP.as_secs_f32()).clamp(0.0, 1.0);
        self.from + (self.to - self.from) * EASE_SNAP.ease(progress)
    }

    fn done(&self, now: Instant) -> bool {
        now.duration_since(self.started) >= SNAP
    }
}

/// Whether any slider is still travelling, so the panel keeps asking for frames.
pub fn animating() -> bool {
    let now = Instant::now();
    SNAPS.with(|snaps| {
        let mut snaps = snaps.borrow_mut();
        snaps.retain(|_, travel| !travel.done(now));
        !snaps.is_empty()
    }) || GRIP.with(|grip| grip.borrow().is_some())
}

/// Forget every measurement. Called when the panel changes story, so a stale
/// track from a slider that no longer exists cannot answer for a new one.
pub fn forget() {
    TRACKS.with(|tracks| tracks.borrow_mut().clear());
    TEXT.with(|text| text.borrow_mut().clear());
    SNAPS.with(|snaps| snaps.borrow_mut().clear());
}

fn measured(key: &SharedString) -> (f32, f32) {
    TEXT.with(|text| {
        text.borrow()
            .get(key)
            .copied()
            .unwrap_or((0.0, 0.0))
    })
}

/// The row every control is built on: 36px, an 8px radius, and a faint surface.
///
/// Nothing here takes a label. A DialKit row puts its label *inside* itself, so
/// the label is the control's business and not the row's.
pub fn row(theme: &Theme) -> gpui::Div {
    div()
        .relative()
        .w_full()
        .h(px(ROW_HEIGHT))
        .rounded(px(ROW_RADIUS))
        .bg(theme.surface_raised)
        .overflow_hidden()
}

/// One label, at the inset and weight every control shares.
fn label_text(theme: &Theme, label: SharedString) -> impl IntoElement {
    div()
        .text_size(px(TEXT_SIZE))
        .font_weight(gpui::FontWeight::MEDIUM)
        .text_color(theme.text_secondary)
        .child(label)
}

/// A canvas that reports the bounds of whatever it is placed inside.
fn measure(record: impl 'static + Fn(Bounds<Pixels>)) -> impl IntoElement {
    div()
        .absolute()
        .size_full()
        .child(canvas(move |bounds, _, _| record(bounds), |_, _, _, _| {}))
}

/// The slider.
///
/// Modelled on DialKit's, described in `docs/device-center-dev-environment.md`
/// §6.2. The behaviour that matters and is easy to miss: a drag tracks the
/// pointer exactly, but a *click* asks for a round number and travels there, so
/// clicking the middle of a 0..1 slider gives 0.5 rather than 0.4913.
#[derive(IntoElement)]
pub struct Slider {
    key: SharedString,
    label: SharedString,
    value: f32,
    span: std::ops::RangeInclusive<f32>,
    step: f32,
    change: Change<f32>,
}

impl Slider {
    pub fn new(
        key: impl Into<SharedString>,
        label: impl Into<SharedString>,
        value: f32,
        span: std::ops::RangeInclusive<f32>,
        step: f32,
        change: impl 'static + Fn(f32, &mut Window, &mut App),
    ) -> Self {
        Self {
            key: key.into(),
            label: label.into(),
            value,
            span,
            step,
            change: Rc::new(change),
        }
    }
}

impl RenderOnce for Slider {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let theme = cx.inari().clone();
        let key = self.key.clone();
        let hover_key = SharedString::from(format!("{key}-hover"));

        let held = GRIP.with(|grip| {
            grip.borrow()
                .as_ref()
                .is_some_and(|held| held.key == key)
        });
        let dragging = GRIP.with(|grip| {
            grip.borrow()
                .as_ref()
                .is_some_and(|held| held.key == key && held.dragging)
        });
        let stretch = GRIP.with(|grip| {
            grip.borrow()
                .as_ref()
                .filter(|held| held.key == key)
                .map_or(0.0, |held| held.stretch)
        });

        // The fill follows the value, except while a click's snap is travelling
        // — then it follows the curve, because the point of the snap is that you
        // watch it arrive.
        let resting = fraction(self.value, &self.span);
        let now = Instant::now();
        let travelling = SNAPS.with(|snaps| snaps.borrow().get(&key).map(|t| t.at(now)));
        let filled = travelling.unwrap_or(resting);

        let lit = motion::fade_fraction(hover_key.clone());
        let active = held || lit > 0.01;
        let (label_width, value_width) = measured(&key);
        let track_width = TRACKS.with(|tracks| {
            tracks
                .borrow()
                .get(&key)
                .map_or(0.0, |bounds| f32::from(bounds.size.width))
        });
        let dodging = dodges(filled, track_width, label_width, value_width);

        let marks: Vec<gpui::AnyElement> = marks(&self.span, self.step)
            .into_iter()
            .map(|at| {
                div()
                    .absolute()
                    .left(relative(at))
                    .top(px((ROW_HEIGHT - MARK_HEIGHT) / 2.0))
                    .w(px(MARK_WIDTH))
                    .h(px(MARK_HEIGHT))
                    .rounded_full()
                    .bg(Hsla { a: theme.hairline_strong.a * lit, ..theme.hairline_strong })
                    .into_any_element()
            })
            .collect();

        let record_track = {
            let key = key.clone();
            move |bounds: Bounds<Pixels>| {
                TRACKS.with(|tracks| {
                    tracks
                        .borrow_mut()
                        .insert(key.clone(), bounds)
                });
            }
        };
        let record_label = {
            let key = key.clone();
            move |bounds: Bounds<Pixels>| {
                TEXT.with(|text| {
                    let mut text = text.borrow_mut();
                    let entry = text.entry(key.clone()).or_insert((0.0, 0.0));
                    entry.0 = f32::from(bounds.size.width);
                });
            }
        };
        let record_value = {
            let key = key.clone();
            move |bounds: Bounds<Pixels>| {
                TEXT.with(|text| {
                    let mut text = text.borrow_mut();
                    let entry = text.entry(key.clone()).or_insert((0.0, 0.0));
                    entry.1 = f32::from(bounds.size.width);
                });
            }
        };

        let press = {
            let key = key.clone();
            let span = self.span.clone();
            let change = self.change.clone();
            let step = self.step;
            move |event: &gpui::MouseDownEvent, _: &mut Window, _: &mut App| {
                GRIP.with(|grip| {
                    *grip.borrow_mut() = Some(Grip {
                        key: key.clone(),
                        from: event.position,
                        dragging: false,
                        span: span.clone(),
                        step,
                        stretch: 0.0,
                        change: change.clone(),
                    })
                });
            }
        };

        div()
            .id(key.clone())
            .relative()
            .w_full()
            .h(px(ROW_HEIGHT))
            .cursor(gpui::CursorStyle::PointingHand)
            .on_hover(move |hovered, window, _| {
                if motion::hover_set(hover_key.clone(), *hovered) {
                    window.refresh();
                }
            })
            .on_mouse_down(MouseButton::Left, press)
            // Measured on the row, not on the track inside it. An absolutely
            // positioned child resolves `size_full` against its containing
            // block, and the track is itself absolute — so measuring in there
            // answered for a box that was neither the row nor the track, and a
            // click landed a third of the way from where it was aimed.
            .child(measure(record_track))
            .child(
                // The track slides when the pointer is dragged past an end, and
                // the row clips it, so the gesture reads as a band being pulled
                // rather than as a value that stopped responding.
                div()
                    .absolute()
                    .top_0()
                    .bottom_0()
                    .left(px(stretch))
                    .right(px(-stretch))
                    .rounded(px(ROW_RADIUS))
                    .bg(theme.surface_raised)
                    .overflow_hidden()
                    .child(
                        div()
                            .absolute()
                            .top_0()
                            .bottom_0()
                            .left_0()
                            .w(relative(filled))
                            .bg(if active { theme.hairline_strong } else { theme.hairline }),
                    )
                    .children(marks)
                    .child(
                        div()
                            .absolute()
                            .left(relative(filled))
                            .ml(px(-HANDLE_WIDTH / 2.0))
                            .top(px((ROW_HEIGHT - HANDLE_HEIGHT) / 2.0))
                            .w(px(HANDLE_WIDTH))
                            .h(px(HANDLE_HEIGHT))
                            .rounded_full()
                            .bg(Hsla {
                                a: handle_opacity(active, dragging, dodging),
                                ..theme.text
                            })
                            // At rest the handle is a quarter of its height, so
                            // it reads as a tick until the pointer arrives.
                            .when(!active, |handle| {
                                handle
                                    .h(px(HANDLE_HEIGHT * HANDLE_RESTING_SCALE))
                                    .top(px((ROW_HEIGHT - HANDLE_HEIGHT * HANDLE_RESTING_SCALE) / 2.0))
                            }),
                    )
                    .child(
                        div()
                            .absolute()
                            .left(px(ROW_INSET))
                            .top_0()
                            .bottom_0()
                            .flex()
                            .items_center()
                            .child(
                                div()
                                    .relative()
                                    .child(label_text(&theme, self.label.clone()))
                                    .child(measure(record_label)),
                            ),
                    )
                    .child(
                        div()
                            .absolute()
                            .right(px(ROW_INSET))
                            .top_0()
                            .bottom_0()
                            .flex()
                            .items_center()
                            .child(
                                div()
                                    .relative()
                                    .child(
                                        div()
                                            .text_size(px(TEXT_SIZE))
                                            .font_family(theme.font_mono.clone())
                                            .text_color(if active {
                                                theme.text
                                            } else {
                                                theme.text_secondary
                                            })
                                            .child(format_value(self.value, self.step)),
                                    )
                                    .child(measure(record_value)),
                            ),
                    ),
            )
    }
}

/// The sheet that follows a press once it leaves the track it started on.
///
/// A pointer handler only fires over its own element, and the whole point of
/// the rubber band is travel *past* the track. The panel mounts this while a
/// press is live; it is transparent and occludes, so nothing under it reacts to
/// a pointer that is busy dragging a slider.
pub fn capture_sheet() -> Option<impl IntoElement> {
    let live = GRIP.with(|grip| grip.borrow().is_some());
    if !live {
        return None;
    }
    Some(deferred(
        div()
            .absolute()
            .size_full()
            .occlude()
            .on_mouse_move(|event, window, cx| drag(event.position, window, cx))
            .on_mouse_up(MouseButton::Left, |event, window, cx| {
                release(event.position, window, cx)
            }),
    ))
}

fn drag(position: Point<Pixels>, window: &mut Window, cx: &mut App) {
    let held = GRIP.with(|grip| {
        let mut grip = grip.borrow_mut();
        let held = grip.as_mut()?;
        let travelled = (position - held.from).magnitude() as f32;
        if !held.dragging && travelled > CLICK_THRESHOLD {
            held.dragging = true;
        }
        if !held.dragging {
            return None;
        }
        let track = TRACKS.with(|tracks| tracks.borrow().get(&held.key).copied())?;
        held.stretch = if position.x < track.origin.x {
            rubber_stretch(f32::from(track.origin.x - position.x), -1.0)
        } else if position.x > track.origin.x + track.size.width {
            rubber_stretch(f32::from(position.x - (track.origin.x + track.size.width)), 1.0)
        } else {
            0.0
        };
        let across = if track.size.width > px(0.0) {
            f32::from(position.x - track.origin.x) / f32::from(track.size.width)
        } else {
            0.0
        };
        // A drag is exact: it reports where the pointer is, rounded only to the
        // step the caller asked for.
        let value = round_to_step(value_at(across, &held.span), held.step);
        SNAPS.with(|snaps| snaps.borrow_mut().remove(&held.key));
        Some((held.change.clone(), value))
    });
    if let Some((change, value)) = held {
        change(value, window, cx);
        window.refresh();
    }
}

fn release(position: Point<Pixels>, window: &mut Window, cx: &mut App) {
    let held = GRIP.with(|grip| grip.borrow_mut().take());
    let Some(held) = held else {
        return;
    };
    if !held.dragging {
        // A click, not a drag: ask for a round number and travel there.
        if let Some(track) = TRACKS.with(|tracks| tracks.borrow().get(&held.key).copied())
            && track.size.width > px(0.0)
        {
            let across = f32::from(position.x - track.origin.x) / f32::from(track.size.width);
            let landed = snap_on_click(value_at(across, &held.span), &held.span, held.step);
            SNAPS.with(|snaps| {
                snaps.borrow_mut().insert(
                    held.key.clone(),
                    Travel {
                        from: across.clamp(0.0, 1.0),
                        to: fraction(landed, &held.span),
                        started: Instant::now(),
                    },
                )
            });
            (held.change)(round_to_step(landed, held.step), window, cx);
        }
    }
    window.refresh();
}

/// A switch, at DialKit's proportions: a 36 × 20 track with a 16px thumb.
#[derive(IntoElement)]
pub struct Toggle {
    key: SharedString,
    label: SharedString,
    checked: bool,
    change: Change<bool>,
}

impl Toggle {
    pub fn new(
        key: impl Into<SharedString>,
        label: impl Into<SharedString>,
        checked: bool,
        change: impl 'static + Fn(bool, &mut Window, &mut App),
    ) -> Self {
        Self {
            key: key.into(),
            label: label.into(),
            checked,
            change: Rc::new(change),
        }
    }
}

impl RenderOnce for Toggle {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let theme = cx.inari().clone();
        let checked = self.checked;
        let change = self.change.clone();
        let hover_key = SharedString::from(format!("{}-hover", self.key));
        let wash = motion::hover_blend(hover_key.clone(), theme.wash_hover);

        row(&theme)
            .id(self.key.clone())
            .flex()
            .items_center()
            .justify_between()
            .px(px(ROW_INSET + 2.0))
            .cursor(gpui::CursorStyle::PointingHand)
            .bg(crate::ui::theme::flatten(wash, theme.surface_raised))
            .on_hover(move |hovered, window, _| {
                if motion::hover_set(hover_key.clone(), *hovered) {
                    window.refresh();
                }
            })
            .on_click(move |_, window, cx| change(!checked, window, cx))
            .child(
                div()
                    .text_size(px(TEXT_SIZE))
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .text_color(if checked { theme.text } else { theme.text_secondary })
                    .child(self.label.clone()),
            )
            .child(
                div()
                    .relative()
                    .w(px(36.0))
                    .h(px(20.0))
                    .rounded(px(10.0))
                    .bg(if checked { theme.accent } else { theme.hairline_strong })
                    .child(
                        div()
                            .absolute()
                            .top(px(2.0))
                            .left(px(if checked { 18.0 } else { 2.0 }))
                            .size(px(16.0))
                            .rounded_full()
                            .bg(if checked { theme.text_on_accent } else { theme.text }),
                    ),
            )
    }
}

/// A row whose label sits inside it and whose control sits on the right.
///
/// DialKit's `.dialkit-labeled-control`: the shape a select, a text field or a
/// segmented control takes, so every row in the panel keeps one silhouette.
pub fn labelled(
    theme: &Theme,
    label: impl Into<SharedString>,
    control: impl IntoElement,
) -> impl IntoElement {
    row(theme)
        .flex()
        .items_center()
        .justify_between()
        .gap(px(12.0))
        .pl(px(ROW_INSET + 2.0))
        .pr(px(ROW_INSET))
        .child(label_text(theme, label.into()))
        .child(control)
}

/// The segmented control, sized to sit inside a row.
#[derive(IntoElement)]
pub struct Segmented {
    key: SharedString,
    options: Vec<SharedString>,
    selected: usize,
    change: Change<usize>,
}

impl Segmented {
    pub fn new(
        key: impl Into<SharedString>,
        options: Vec<SharedString>,
        selected: usize,
        change: impl 'static + Fn(usize, &mut Window, &mut App),
    ) -> Self {
        Self {
            key: key.into(),
            options,
            selected,
            change: Rc::new(change),
        }
    }
}

impl RenderOnce for Segmented {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let theme = cx.inari().clone();
        let key = self.key.clone();
        let selected = self.selected;
        div()
            .flex()
            .flex_none()
            .items_center()
            .gap(px(2.0))
            .children(
                self.options
                    .into_iter()
                    .enumerate()
                    .map(|(index, option)| {
                        let change = self.change.clone();
                        let chosen = index == selected;
                        div()
                            .id(SharedString::from(format!("{key}-{index}")))
                            .flex()
                            .items_center()
                            .justify_center()
                            .px(px(8.0))
                            .h(px(24.0))
                            .rounded(px(6.0))
                            .text_size(px(TEXT_SIZE))
                            .font_weight(gpui::FontWeight::MEDIUM)
                            .when(chosen, |chip| {
                                chip.bg(theme.surface_overlay)
                                    .text_color(theme.text)
                            })
                            .when(!chosen, |chip| {
                                chip.text_color(theme.text_tertiary)
                                    .hover(|style| style.bg(theme.wash_hover))
                            })
                            .child(option)
                            .on_click(move |_, window, cx| change(index, window, cx))
                    }),
            )
    }
}


/// A text field inside a row.
///
/// The chrome follows `ui/field.rs`: the edge rests on the hairline and warms
/// to the accent while the field is live, over the same 150 ms every other wash
/// in the application uses, with a soft accent ring at full focus. Editing
/// itself belongs to GPUI Component's editor with its own appearance off — this
/// owns only the chrome, the way the enrollment field does.
///
/// The caller reports focus against `{key}-focus` from the editor's own Focus
/// and Blur events, because a field cannot see its own focus from here.
pub fn text_row(
    theme: &Theme,
    key: impl Into<SharedString>,
    label: impl Into<SharedString>,
    input: &gpui::Entity<gpui_component::input::InputState>,
) -> impl IntoElement {
    use gpui::BoxShadow;

    let key = key.into();
    let focus_key = SharedString::from(format!("{key}-focus"));
    let hover_key = SharedString::from(format!("{key}-hover"));
    let focus = motion::fade_fraction(focus_key);
    let hover = motion::fade_fraction(hover_key.clone());

    let border = crate::ui::theme::mix(
        theme.hairline,
        Hsla { a: 0.55, ..theme.accent },
        focus,
    );
    let fill = crate::ui::theme::flatten(
        Hsla { a: theme.wash_hover.a * hover, ..theme.wash_hover },
        theme.surface_raised,
    );
    // Presence without glow: 3px of accent spread, no blur. A blurred halo
    // behind a translucent fill reads through it as an inner smudge.
    let ring = (focus > 0.004).then(|| BoxShadow {
        color: Hsla { a: 0.16 * focus, ..theme.accent },
        offset: gpui::point(px(0.0), px(0.0)),
        blur_radius: px(0.0),
        spread_radius: px(3.0),
    });

    row(theme)
        .id(key)
        .flex()
        .items_center()
        .justify_between()
        .gap(px(12.0))
        .pl(px(ROW_INSET + 2.0))
        .pr(px(ROW_INSET))
        .bg(fill)
        .border_1()
        .border_color(border)
        .shadow(ring.into_iter().collect::<Vec<_>>())
        .cursor(gpui::CursorStyle::IBeam)
        .on_hover(move |hovered, window, _| {
            if motion::hover_set(hover_key.clone(), *hovered) {
                window.refresh();
            }
        })
        .child(label_text(theme, label.into()))
        .child(
            div()
                .flex_1()
                .min_w(px(0.0))
                .text_size(px(TEXT_SIZE))
                .child(
                    gpui_component::input::Input::new(input)
                        .appearance(false)
                        .small(),
                ),
        )
}

/// A full-width action, at DialKit's `.dialkit-button` proportions.
#[derive(IntoElement)]
pub struct Action {
    key: SharedString,
    label: SharedString,
    press: Press,
}

impl Action {
    pub fn new(
        key: impl Into<SharedString>,
        label: impl Into<SharedString>,
        press: impl 'static + Fn(&mut Window, &mut App),
    ) -> Self {
        Self {
            key: key.into(),
            label: label.into(),
            press: Rc::new(press),
        }
    }
}

impl RenderOnce for Action {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let theme = cx.inari().clone();
        let hover_key = SharedString::from(format!("{}-hover", self.key));
        let wash = motion::hover_blend(hover_key.clone(), theme.wash_hover);
        let press = self.press.clone();

        row(&theme)
            .id(self.key.clone())
            .flex()
            .items_center()
            .justify_center()
            .cursor(gpui::CursorStyle::PointingHand)
            .bg(crate::ui::theme::flatten(wash, theme.surface_raised))
            .on_hover(move |hovered, window, _| {
                if motion::hover_set(hover_key.clone(), *hovered) {
                    window.refresh();
                }
            })
            .on_click(move |_, window, cx| press(window, cx))
            .child(
                div()
                    .text_size(px(TEXT_SIZE))
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .text_color(theme.text_secondary)
                    .child(self.label.clone()),
            )
    }
}

/// A stepper for a whole number, sized to sit inside a row.
pub fn stepper(
    theme: &Theme,
    key: impl Into<SharedString>,
    value: usize,
    span: std::ops::RangeInclusive<usize>,
    change: impl 'static + Fn(usize, &mut Window, &mut App),
) -> impl IntoElement {
    let key = key.into();
    let change: Change<usize> = Rc::new(change);
    let (lo, hi) = (*span.start(), *span.end());

    let arrow = |suffix: &'static str, glyph: gpui_component::IconName, to: Option<usize>| {
        let change = change.clone();
        div()
            .id(SharedString::from(format!("{key}-{suffix}")))
            .flex()
            .items_center()
            .justify_center()
            .size(px(20.0))
            .rounded(px(6.0))
            .text_color(if to.is_some() { theme.text_secondary } else { theme.text_tertiary })
            .when(to.is_some(), |arrow| {
                arrow
                    .cursor(gpui::CursorStyle::PointingHand)
                    .hover(|style| style.bg(theme.wash_hover))
            })
            .child(
                gpui_component::Icon::from(glyph)
                    .size(px(12.0))
                    .flex_none(),
            )
            .on_click(move |_, window, cx| {
                if let Some(to) = to {
                    change(to, window, cx);
                }
            })
    };

    div()
        .flex()
        .flex_none()
        .items_center()
        .gap(px(2.0))
        .child(arrow(
            "down",
            gpui_component::IconName::Minus,
            (value > lo).then(|| value - 1),
        ))
        .child(
            div()
                .w(px(24.0))
                .flex()
                .justify_center()
                .text_size(px(TEXT_SIZE))
                .font_family(theme.font_mono.clone())
                .text_color(theme.text)
                .child(value.to_string()),
        )
        .child(arrow(
            "up",
            gpui_component::IconName::Plus,
            (value < hi).then(|| value + 1),
        ))
}

/// A section heading between runs of rows.
pub fn heading(theme: &Theme, title: impl Into<SharedString>) -> impl IntoElement {
    div()
        .w_full()
        .pt(px(Theme::SPACE_SM))
        .pb(px(2.0))
        .pl(px(ROW_INSET + 2.0))
        .text_size(px(11.0))
        .font_weight(gpui::FontWeight::MEDIUM)
        .text_color(theme.text_tertiary)
        .child(title.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn span(lo: f32, hi: f32) -> RangeInclusive<f32> {
        lo..=hi
    }

    #[test]
    fn decimals_follow_the_step() {
        assert_eq!(decimals_for_step(1.0), 0);
        assert_eq!(decimals_for_step(2.0), 0);
        assert_eq!(decimals_for_step(0.1), 1);
        assert_eq!(decimals_for_step(0.05), 2);
        assert_eq!(decimals_for_step(0.001), 3);
    }

    #[test]
    fn a_value_prints_no_further_than_its_step_can_reach() {
        assert_eq!(format_value(0.4913, 0.1), "0.5");
        assert_eq!(format_value(12.0, 1.0), "12");
    }

    #[test]
    fn a_fraction_and_a_value_are_inverses() {
        let span = span(8.0, 64.0);
        for step in 0..=10 {
            let f = step as f32 / 10.0;
            assert!((fraction(value_at(f, &span), &span) - f).abs() < 1e-5);
        }
    }

    #[test]
    fn a_value_outside_the_span_reads_as_an_end() {
        let span = span(0.0, 1.0);
        assert_eq!(fraction(-4.0, &span), 0.0);
        assert_eq!(fraction(9.0, &span), 1.0);
    }

    #[test]
    fn a_span_with_no_width_does_not_divide_by_zero() {
        let span = span(5.0, 5.0);
        assert_eq!(fraction(5.0, &span), 0.0);
        assert_eq!(snap_to_decile(5.0, &span), 5.0);
    }

    #[test]
    fn a_coarse_span_is_discrete_and_a_fine_one_is_not() {
        assert!(is_discrete(&span(0.0, 10.0), 2.0));
        assert!(is_discrete(&span(0.0, 3.0), 1.0));
        assert!(!is_discrete(&span(0.0, 1.0), 0.01));
    }

    #[test]
    fn a_click_on_a_coarse_span_lands_on_a_step() {
        let span = span(0.0, 3.0);
        assert_eq!(snap_on_click(1.4, &span, 1.0), 1.0);
        assert_eq!(snap_on_click(1.6, &span, 1.0), 2.0);
    }

    #[test]
    fn a_click_on_a_fine_span_is_magnetic_to_the_nearest_tenth() {
        let span = span(0.0, 1.0);
        assert!((snap_on_click(0.4913, &span, 0.01) - 0.5).abs() < 1e-5);
        assert!((snap_on_click(0.0312, &span, 0.01) - 0.0).abs() < 1e-5);
    }

    #[test]
    fn snapping_never_leaves_the_span() {
        let span = span(8.0, 64.0);
        assert_eq!(snap_on_click(1000.0, &span, 0.5), 64.0);
        assert_eq!(snap_on_click(-1000.0, &span, 0.5), 8.0);
    }

    #[test]
    fn a_coarse_span_marks_every_step_and_a_fine_one_marks_the_tenths() {
        assert_eq!(marks(&span(0.0, 3.0), 1.0), vec![1.0 / 3.0, 2.0 / 3.0]);
        assert_eq!(marks(&span(0.0, 1.0), 0.01).len(), 9);
    }

    #[test]
    fn the_marks_agree_with_where_a_click_lands() {
        // A mark a click cannot reach is a lie about the control.
        let span = span(0.0, 3.0);
        for mark in marks(&span, 1.0) {
            let value = value_at(mark, &span);
            assert!((snap_on_click(value, &span, 1.0) - value).abs() < 1e-4);
        }
    }

    #[test]
    fn the_band_does_not_stretch_inside_the_dead_zone() {
        assert_eq!(rubber_stretch(0.0, 1.0), 0.0);
        assert_eq!(rubber_stretch(DEAD_ZONE, 1.0), 0.0);
    }

    #[test]
    fn the_band_goes_stiff_as_it_reaches_its_limit() {
        let half = rubber_stretch(DEAD_ZONE + MAX_CURSOR_RANGE / 2.0, 1.0);
        let full = rubber_stretch(DEAD_ZONE + MAX_CURSOR_RANGE, 1.0);
        assert!((full - MAX_STRETCH).abs() < 1e-4);
        // Square-rooted: half the travel is already most of the movement.
        assert!(half > full * 0.7, "{half} should be most of {full}");
    }

    #[test]
    fn the_band_stretches_the_way_the_pointer_went() {
        assert!(rubber_stretch(300.0, -1.0) < 0.0);
        assert!(rubber_stretch(300.0, 1.0) > 0.0);
    }

    #[test]
    fn the_band_never_stretches_past_its_limit() {
        assert!(rubber_stretch(100_000.0, 1.0) <= MAX_STRETCH);
    }

    #[test]
    fn the_handle_dodges_the_label_and_the_value_but_not_the_middle() {
        let track = 200.0;
        assert!(dodges(0.02, track, 40.0, 30.0));
        assert!(dodges(0.98, track, 40.0, 30.0));
        assert!(!dodges(0.5, track, 40.0, 30.0));
    }

    #[test]
    fn a_track_with_no_width_does_not_report_a_dodge() {
        assert!(!dodges(0.5, 0.0, 40.0, 30.0));
    }

    #[test]
    fn the_handle_is_invisible_until_the_pointer_arrives() {
        assert_eq!(handle_opacity(false, false, false), 0.0);
        assert_eq!(handle_opacity(false, false, true), 0.0);
    }

    #[test]
    fn the_handle_is_brightest_while_dragging_and_faintest_while_dodging() {
        let hover = handle_opacity(true, false, false);
        let drag = handle_opacity(true, true, false);
        let dodge = handle_opacity(true, true, true);
        assert!(dodge < hover && hover < drag);
    }

    #[test]
    fn rounding_to_a_step_lands_on_a_multiple_of_it() {
        assert_eq!(round_to_step(0.47, 0.1), 0.5);
        assert_eq!(round_to_step(7.0, 2.0), 8.0);
        // A step of zero is a caller mistake, not a panic.
        assert_eq!(round_to_step(0.47, 0.0), 0.47);
    }
}
