//! The floating layer: a launcher, and the overlay the Layout tool draws.
//!
//! GPUI has shipped a working element inspector all along — picking, ancestor
//! walking, live style editing — and `gpui_component::init` binds it to
//! `ctrl-shift-i` in this application already. Nothing pointed at it. A good
//! tool nobody can find is not a tool, so the launcher exists to be the thing
//! that points.
//!
//! Both parts paint through `deferred`, which draws after the whole tree with
//! no content mask (`elements/deferred.rs:65`), so the layer escapes every
//! rounded scroll container the application has and never joins its layout.

use std::{collections::HashMap, time::Instant};

use gpui::{
    AnyElement, App, BorrowAppContext as _, Bounds, DispatchPhase, Global, Hsla,
    InteractiveElement as _, IntoElement, MouseButton, MouseMoveEvent, MouseUpEvent,
    ParentElement as _, Pixels, Point, StatefulInteractiveElement as _, Styled as _, Window,
    WindowId, canvas, deferred, div, point, prelude::FluentBuilder as _, px,
};
use gpui_component::{
    Selectable as _, Sizable as _, StyledExt as _,
    button::{Button, ButtonVariants as _},
};

use crate::{
    dev::{
        element,
        panel::{self, Screen},
    },
    ui::{
        motion,
        theme::{ActiveTheme as _, Theme},
    },
};

const PILL_HEIGHT: f32 = 30.0;
/// Far enough from the window edge that it never fights a resize grip.
const MARGIN: f32 = 16.0;
const FADE_KEY: &str = "dev-bubble";

/// Every window's launcher.
///
/// Keyed by window, like the rest of the panel's state. Held as one value the
/// two launchers shared a position and a grab: pressing one moved the other,
/// and the offset a drag started from came from whichever window had painted
/// last.
#[derive(Default)]
struct Floats(HashMap<WindowId, Float>);

impl Global for Floats {}

/// Where one window's launcher sits, and the drag in progress.
#[derive(Clone, Copy, Default)]
struct Float {
    /// `None` until the launcher is first moved: it rests bottom-right, which
    /// follows the window rather than being pinned to a stale point.
    at: Option<Point<Pixels>>,
    /// Where the pill was last laid out.
    ///
    /// A mouse-down listener is handed a pointer position and nothing else, so
    /// without this a drag would begin with a grab offset of zero and the pill
    /// would jump to sit under the pointer. A `canvas` is the cheapest way to
    /// read an element's own bounds.
    painted: Bounds<Pixels>,
    /// The area the launcher may be dragged within — the root, which is
    /// narrower than the window while the dock is open.
    frame: Bounds<Pixels>,
    /// The grab point inside the pill, so it does not jump under the pointer.
    grab: Option<Point<Pixels>>,
}

fn float(window: &Window, cx: &App) -> Float {
    float_of(window.window_handle().window_id(), cx)
}

fn float_of(window_id: WindowId, cx: &App) -> Float {
    cx.try_global::<Floats>()
        .and_then(|floats| floats.0.get(&window_id).copied())
        .unwrap_or_default()
}

fn adjust_float(window_id: WindowId, cx: &mut App, change: impl FnOnce(&mut Float)) {
    if !cx.has_global::<Floats>() {
        cx.set_global(Floats::default());
    }
    cx.update_global(|floats: &mut Floats, _| {
        change(floats.0.entry(window_id).or_default())
    });
}

/// A canvas that reports the bounds of whatever it is placed inside.
fn measure(record: impl 'static + Fn(Bounds<Pixels>, &mut App)) -> impl IntoElement {
    // Styled on the canvas itself. A bare canvas lays out at its content size,
    // which is nothing, so it would report a box that is not its parent's.
    canvas(move |bounds, _, cx| record(bounds, cx), |_, _, _, _| {})
        .absolute()
        .size_full()
}

/// The whole floating layer for one root render.
pub fn render(window: &mut Window, cx: &mut App) -> AnyElement {
    let theme = cx.inari().clone();
    let deck = panel::deck(window, cx);

    let window_id = window.window_handle().window_id();
    let float = float(window, cx);
    let width = px(PILL_HEIGHT * (Screen::ALL.len() + 1) as f32 + 40.0);
    // Until it is dragged the launcher is anchored, not positioned: the root is
    // narrower while the dock is open, and an inset follows that where a
    // computed left/top would put the pill under the panel.
    let at = float.at;

    let mut layer = div()
        .absolute()
        .size_full()
        .child(measure(move |bounds, cx| {
            adjust_float(window_id, cx, |float| float.frame = bounds);
        }))
        .child(drag_listeners(window_id));

    // Always drawn. There is nothing to turn off: a selection with no box is a
    // selection you cannot see, and the box costs nothing when there is none.
    layer = layer.child(box_model(&theme));

    deferred(layer.child(pill(&theme, at, width, deck.screen, window_id, window, cx)))
        .into_any_element()
}

/// The drag, handled at the window rather than under the pointer.
///
/// Both of the launcher's faults came from the shape of the old answer: a sheet
/// over the window, mounted only while a drag was live.
///
/// A `div`'s mouse handler fires only over its own hitbox, and a hitbox belongs
/// to the frame that painted it. Every pointer move re-rendered the tree, so
/// moves that arrived against a frame still being rebuilt were dropped — and a
/// drag that loses most of its moves staggers.
///
/// Worse, a sheet that exists only *while* dragging cannot catch the release of
/// a click whose press and release fall inside one frame. The press armed the
/// drag, the release found no sheet to land on, and nothing ever disarmed it —
/// so the launcher followed the pointer for good.
///
/// `Window::on_mouse_event` answers both. It is registered during paint, fires
/// for every event whatever is under the pointer, and is registered on *every*
/// frame rather than only on dragging ones, so the release always has somewhere
/// to land. On a frame with no drag in progress it costs two early returns.
fn drag_listeners(window_id: WindowId) -> impl IntoElement {
    canvas(
        |_, _, _| (),
        move |_, _, window: &mut Window, _| {
            window.on_mouse_event(
                move |event: &MouseMoveEvent, phase, window: &mut Window, cx: &mut App| {
                    if phase != DispatchPhase::Bubble {
                        return;
                    }
                    let float = float_of(window_id, cx);
                    let Some(grab) = float.grab else {
                        return;
                    };
                    // The button can come up somewhere we never hear about —
                    // another window, a menu. A move with nothing held is that
                    // release arriving late.
                    if event.pressed_button != Some(MouseButton::Left) {
                        adjust_float(window_id, cx, |float| float.grab = None);
                        window.refresh();
                        return;
                    }
                    let at = settle(
                        point(event.position.x - grab.x, event.position.y - grab.y),
                        float.painted.size,
                        float.frame,
                    );
                    adjust_float(window_id, cx, |float| float.at = Some(at));
                    window.refresh();
                },
            );
            window.on_mouse_event(
                move |_: &MouseUpEvent, phase, window: &mut Window, cx: &mut App| {
                    if phase != DispatchPhase::Bubble {
                        return;
                    }
                    if float_of(window_id, cx).grab.is_some() {
                        adjust_float(window_id, cx, |float| float.grab = None);
                        window.refresh();
                    }
                },
            );
        },
    )
    .absolute()
    .size_full()
}

/// Keep the launcher inside `frame`, whatever the pointer asks for.
///
/// Dragged past an edge it would otherwise leave the window, and a launcher you
/// cannot reach is a launcher you cannot put back.
fn settle(
    at: Point<Pixels>,
    size: gpui::Size<Pixels>,
    frame: Bounds<Pixels>,
) -> Point<Pixels> {
    if frame.size.width <= px(0.0) {
        return at;
    }
    let margin = px(MARGIN);
    point(
        at.x.clamp(margin, (frame.size.width - size.width - margin).max(margin)),
        at.y.clamp(margin, (frame.size.height - size.height - margin).max(margin)),
    )
}

fn pill(
    theme: &Theme,
    at: Option<Point<Pixels>>,
    width: Pixels,
    active: Screen,
    window_id: WindowId,
    window: &Window,
    cx: &App,
) -> impl IntoElement {
    let open = panel::is_open(window, cx);
    // Quiet until it is wanted, and eased on the application's own hover clock
    // so the launcher does not move at a speed nothing else moves at.
    let resting = if open { 1.0 } else { 0.55 };
    let lit = motion::fade_fraction(FADE_KEY);
    let opacity = resting + (1.0 - resting) * lit;

    div()
        .id("dev-bubble")
        .absolute()
        .map(|pill| match at {
            Some(at) => pill.left(at.x).top(at.y),
            None => pill.right(px(MARGIN)).bottom(px(MARGIN)),
        })
        .w(width)
        .h(px(PILL_HEIGHT))
        .occlude()
        .opacity(opacity)
        .on_hover(|hovered, window, _| {
            if motion::hover_set(FADE_KEY, *hovered) {
                window.refresh();
            }
        })
        .h_flex()
        .items_center()
        .gap(px(1.0))
        .px(px(3.0))
        .rounded_full()
        .bg(theme.surface_overlay)
        .border_1()
        .border_color(theme.hairline_strong)
        .child(measure(move |bounds, cx| {
            adjust_float(window_id, cx, |float| float.painted = bounds);
        }))
        .child(grip(theme, window_id))
        .children(Screen::ALL.map(|screen| {
            Button::new(gpui::SharedString::from(format!("dev-bubble-{}", screen.title())))
                .icon(screen.icon())
                .ghost()
                .xsmall()
                .selected(open && screen == active)
                .tooltip(screen.title())
                .on_click(move |_, window: &mut Window, cx: &mut App| {
                    panel::show(screen, window, cx);
                })
        }))
        // Picking from here is the shortest path there is: one press arms the
        // picker and hands the panel to the Element screen, so the click that
        // lands on something is the same click that shows its report.
        .child(
            Button::new("dev-bubble-pick")
                .icon(gpui_component::IconName::Search)
                .ghost()
                .xsmall()
                .tooltip("Pick an element")
                .on_click(|_, window: &mut Window, cx: &mut App| {
                    panel::start_pick(window, cx);
                }),
        )
}

/// Two rules, not an icon: the launcher is a handle before it is a toolbar, and
/// a drawn grip says so without adding a sixth glyph to a row of five.
fn grip(theme: &Theme, window_id: WindowId) -> impl IntoElement {
    div()
        .id("dev-bubble-grip")
        .h_flex()
        .items_center()
        .justify_center()
        .gap(px(2.0))
        .w(px(14.0))
        .h_full()
        .cursor(gpui::CursorStyle::OpenHand)
        .child(div().w(px(1.0)).h(px(10.0)).bg(theme.text_tertiary))
        .child(div().w(px(1.0)).h(px(10.0)).bg(theme.text_tertiary))
        .on_mouse_down(
            MouseButton::Left,
            move |event, window: &mut Window, cx: &mut App| {
                let position = event.position;
                adjust_float(window_id, cx, |float| {
                    let at = float.painted.origin;
                    float.grab = Some(point(position.x - at.x, position.y - at.y));
                    float.at = Some(at);
                });
                window.refresh();
            },
        )
}

/// Bounds, border, padding and content box for the selected element.
///
/// Painted rather than laid out. GPUI draws its own picker highlight in the
/// paint phase from the frame it is drawing (`window.rs:4626-4640`), and this
/// follows it for the same two reasons.
///
/// The first is truth. A `canvas` paint callback runs after the whole tree has
/// prepainted, so the element's bounds and its `painted` stamp are this frame's
/// — the box is where the element is *now*, not where it was last time
/// something asked. Built out of divs the overlay was always a frame behind and
/// slid whenever the layout moved.
///
/// The second is the disappearance. `frame_began` is taken while the root
/// renders, before anything prepaints. An element still in the tree is stamped
/// after that; one that has gone keeps a stamp from an earlier frame. So
/// `painted >= frame_began` is exact, and the box goes in the same frame its
/// element does — where a duration could only guess, and guessed late.
///
/// Margin is still not drawn. GPUI resolves it during layout and no margin
/// rectangle survives to paint.
fn box_model(theme: &Theme) -> AnyElement {
    let frame_began = Instant::now();
    let outline = theme.accent;
    let bounds_wash = Hsla { a: 0.10, ..theme.accent };
    let padding_wash = Hsla { a: 0.14, ..theme.info };
    let chip_ink = theme.text_on_accent;
    let mono = theme.font_mono.clone();

    canvas(
        |_, _, _| (),
        move |_, _, window: &mut Window, cx: &mut App| {
            let Some(selection) = element::current(window, cx) else {
                return;
            };
            if selection.painted < frame_began {
                return;
            }
            let bounds = selection.bounds;
            let padding_box = selection.padding_box();
            let content_box = selection.content_box();
            let size = format!(
                "{:.0} × {:.0}",
                f32::from(bounds.size.width),
                f32::from(bounds.size.height)
            );

            // Outermost first: each band is the one above it with a little more
            // taken away, so the overlaps read as depth rather than as stripes.
            window.paint_quad(gpui::fill(bounds, bounds_wash));
            window.paint_quad(gpui::fill(padding_box, padding_wash));
            window.paint_quad(gpui::fill(content_box, gpui::transparent_black()));
            window.paint_quad(gpui::outline(bounds, outline, gpui::BorderStyle::Solid));

            // Above the element when there is room, inside its top edge when
            // there is not, so the chip never leaves the window at y = 0.
            let font_size = px(10.0);
            let line_height = px(14.0);
            let run = gpui::TextRun {
                len: size.len(),
                font: gpui::Font {
                    family: mono.clone(),
                    features: Default::default(),
                    fallbacks: None,
                    weight: Default::default(),
                    style: Default::default(),
                },
                color: chip_ink,
                background_color: None,
                underline: None,
                strikethrough: None,
            };
            let line = window
                .text_system()
                .shape_line(size.into(), font_size, &[run], None);
            let above = bounds.origin.y > line_height + px(4.0);
            let plate = Bounds {
                origin: point(
                    bounds.origin.x,
                    if above {
                        bounds.origin.y - line_height - px(2.0)
                    } else {
                        bounds.origin.y + px(2.0)
                    },
                ),
                size: gpui::size(line.width + px(8.0), line_height),
            };
            window.paint_quad(gpui::quad(
                plate,
                gpui::Corners::all(px(3.0)),
                outline,
                gpui::Edges::default(),
                gpui::transparent_black(),
                gpui::BorderStyle::default(),
            ));
            line.paint(
                point(plate.origin.x + px(4.0), plate.origin.y),
                line_height,
                window,
                cx,
            )
            .ok();
        },
    )
    .absolute()
    .size_full()
    .into_any_element()
}
