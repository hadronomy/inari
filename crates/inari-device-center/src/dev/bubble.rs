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

use std::cell::Cell;

use gpui::{
    AnyElement, App, BorrowAppContext as _, Bounds, Global, InteractiveElement as _, IntoElement,
    MouseButton, ParentElement as _, Pixels, Point, StatefulInteractiveElement as _, Styled as _,
    Window, canvas, deferred, div, point, prelude::FluentBuilder as _, px,
};
use gpui_component::{
    Selectable as _, Sizable as _, StyledExt as _,
    button::{Button, ButtonVariants as _},
};

use crate::{
    dev::{
        element::Selection,
        panel::{self, Tool},
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

/// Where the launcher sits, and whether it is being moved.
#[derive(Default)]
struct Float {
    /// `None` until the launcher is first moved: it rests bottom-right, which
    /// follows the window rather than being pinned to a stale point.
    at: Option<Point<Pixels>>,
    /// The grab point inside the pill, so it does not jump under the pointer.
    grab: Option<Point<Pixels>>,
}

impl Global for Float {}

thread_local! {
    /// Where the launcher was laid out last frame.
    ///
    /// A mouse-down listener is handed a pointer position and nothing else, so
    /// without this the first drag would compute a grab offset of zero and the
    /// pill would jump to sit under the pointer. A `canvas` is the cheapest way
    /// to read an element's own bounds; Zeron uses the same trick to measure its
    /// composer.
    static PAINTED_AT: Cell<Point<Pixels>> = const { Cell::new(point(px(0.0), px(0.0))) };
}

/// The whole floating layer for one root render.
pub fn render(window: &mut Window, cx: &mut App) -> AnyElement {
    let theme = cx.inari().clone();
    let deck = panel::deck(cx);

    let float = cx.try_global::<Float>();
    let dragging = float.and_then(|float| float.grab).is_some();
    let width = px(PILL_HEIGHT * Tool::ALL.len() as f32 + 40.0);
    // Until it is dragged the launcher is anchored, not positioned: the root is
    // narrower while the dock is open, and an inset follows that where a
    // computed left/top would put the pill under the panel.
    let at = float.and_then(|float| float.at);

    let mut layer = div().absolute().size_full();

    if deck.box_model && let Some(selection) = cx.try_global::<Selection>() {
        layer = layer.child(box_model(&theme, selection));
    }

    // While dragging, a transparent sheet over the window catches the moves the
    // pill itself would miss the moment the pointer leaves it.
    if dragging {
        layer = layer.child(
            div()
                .absolute()
                .size_full()
                .occlude()
                .on_mouse_move(|event, _, cx| {
                    cx.update_global(|float: &mut Float, _| {
                        if let Some(grab) = float.grab {
                            float.at = Some(point(
                                event.position.x - grab.x,
                                event.position.y - grab.y,
                            ));
                        }
                    });
                })
                .on_mouse_up(MouseButton::Left, |_, _, cx| {
                    cx.update_global(|float: &mut Float, _| float.grab = None);
                }),
        );
    }

    deferred(layer.child(pill(&theme, at, width, deck.tool, window, cx))).into_any_element()
}

fn pill(
    theme: &Theme,
    at: Option<Point<Pixels>>,
    width: Pixels,
    active: Tool,
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
        .child(canvas(
            |bounds, _, _| PAINTED_AT.with(|painted| painted.set(bounds.origin)),
            |_, _, _, _| {},
        ))
        .child(grip(theme))
        .children(Tool::ALL.map(|tool| {
            Button::new(gpui::SharedString::from(format!("dev-bubble-{}", tool.title())))
                .icon(tool.icon())
                .ghost()
                .xsmall()
                .selected(open && tool == active)
                .tooltip(tool.title())
                .on_click(move |_, window: &mut Window, cx: &mut App| {
                    panel::show(tool, window, cx);
                })
        }))
}

/// Two rules, not an icon: the launcher is a handle before it is a toolbar, and
/// a drawn grip says so without adding a sixth glyph to a row of five.
fn grip(theme: &Theme) -> impl IntoElement {
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
        .on_mouse_down(MouseButton::Left, |event, _, cx| {
            if !cx.has_global::<Float>() {
                cx.set_global(Float::default());
            }
            let position = event.position;
            let at = PAINTED_AT.with(|painted| painted.get());
            cx.update_global(|float: &mut Float, _| {
                float.grab = Some(point(position.x - at.x, position.y - at.y));
                float.at = Some(at);
            });
        })
}

/// Bounds, border, padding and content box for the selected element.
///
/// Margin is not here on purpose. GPUI resolves margin during layout and no
/// margin rectangle survives to paint, so drawing one would mean guessing — in
/// the one tool whose whole job is to not guess.
fn box_model(theme: &Theme, selection: &Selection) -> AnyElement {
    let band = |bounds: Bounds<Pixels>, fill: gpui::Hsla| {
        div()
            .absolute()
            .left(bounds.origin.x)
            .top(bounds.origin.y)
            .w(bounds.size.width)
            .h(bounds.size.height)
            .bg(fill)
    };

    let bounds = selection.bounds;
    let size = format!(
        "{:.0} × {:.0}",
        f32::from(bounds.size.width),
        f32::from(bounds.size.height)
    );
    // Above the element when there is room, inside its top edge when there is
    // not, so the chip never leaves the window at y = 0.
    let chip_above = bounds.origin.y > px(20.0);

    div()
        .absolute()
        .size_full()
        .child(band(bounds, theme.accent.opacity(0.10)))
        .child(band(selection.padding_box(), theme.info.opacity(0.14)))
        .child(band(selection.content_box(), theme.accent.opacity(0.0)))
        .child(
            div()
                .absolute()
                .left(bounds.origin.x)
                .top(bounds.origin.y)
                .w(bounds.size.width)
                .h(bounds.size.height)
                .border_1()
                .border_color(theme.accent),
        )
        .child(
            div()
                .absolute()
                .left(bounds.origin.x)
                .top(if chip_above {
                    bounds.origin.y - px(18.0)
                } else {
                    bounds.origin.y + px(2.0)
                })
                .px(px(4.0))
                .rounded(px(3.0))
                .bg(theme.accent)
                .text_size(px(10.0))
                .font_family(theme.font_mono.clone())
                .text_color(theme.text_on_accent)
                .child(size),
        )
        .into_any_element()
}
