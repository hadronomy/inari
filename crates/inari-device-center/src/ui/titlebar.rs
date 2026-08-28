//! The window's own top edge.
//!
//! Every window this app opens draws its own titlebar, because the shell wants
//! one continuous surface from the top of the window to the bottom of the
//! content. That means every window is also responsible for the things a system
//! titlebar would have given it for free: dragging it moves the window, and the
//! buttons at its trailing end minimise, maximise and close.
//!
//! Leaving that to each window is how the enrollment window shipped unmovable.
//! So neither job is something a caller remembers to add. A caller chooses what
//! sits at the two ends; everything between and after them belongs here.
//!
//! ## Why these buttons carry no click handler on Windows
//!
//! Windows expects an app that extends into its caption to answer the
//! non-client hit test, so the *system* owns what a caption button does —
//! including the Snap Layouts flyout that appears when the pointer rests on
//! maximise. GPUI exposes that through [`WindowControlArea`], and the correct
//! use of it is to mark the region and then keep out of the way. Zed's
//! maintainers put it plainly when asked why a custom titlebar's buttons did
//! nothing: *you shouldn't handle the `on_mouse_down` event*
//! (<https://github.com/zed-industries/zed/discussions/45012>).
//!
//! That is why the buttons below have no `on_click`, and why they call
//! [`InteractiveElement::occlude`]. A press that reaches an ancestor's mouse
//! handler is a press the system never sees: the ancestor arms a window move,
//! the pointer twitches, and the drag eats the click. `gpui_component`'s own
//! `TitleBar` has exactly that shape — its root binds `on_mouse_down` and its
//! control icons never occlude — which is why this app draws its own caption
//! instead of using it.
//!
//! Linux is the opposite case: GPUI's control-area hit testing is inert under
//! client-side decorations, so there the buttons do carry handlers.
//!
//! macOS draws its own traffic lights over the transparent titlebar, so this
//! component only leaves room for them.

// The caption buttons ease their hover fill through `on_hover`, and Linux's
// handlers click through `on_click` — both live on the stateful interactive
// trait. macOS draws no controls and never needs it.
#[cfg(not(target_os = "macos"))]
use gpui::StatefulInteractiveElement as _;
use gpui::{
    AnyElement, InteractiveElement as _, IntoElement, MouseButton, ParentElement as _, RenderOnce,
    Styled, Window, WindowControlArea, div, px,
};
use gpui_component::StyledExt as _;
#[cfg(not(target_os = "macos"))]
use gpui_component::{Icon, IconName};

#[cfg(not(target_os = "macos"))]
use super::theme::ActiveTheme as _;
use super::theme::Theme;
use crate::infrastructure::platform;

/// The room macOS needs at the leading edge for its traffic lights.
#[cfg(target_os = "macos")]
const TRAFFIC_LIGHTS: f32 = 80.0;

/// The leading inset every window's content starts at.
///
/// On Windows and Linux nothing native claims the leading edge, so the bar
/// sets the inset itself. It matches the navigation rail's inset, so the brand
/// mark sits on the same vertical line as the rail items below it.
#[cfg(not(target_os = "macos"))]
const LEADING_INSET: f32 = Theme::SPACE_LG;

/// Windows caption buttons are 36px wide at 100% scale, which is what makes a
/// cluster drawn by an app sit at the size the eye expects from a system one.
#[cfg(not(target_os = "macos"))]
const CAPTION_BUTTON: f32 = 36.0;

/// A window titlebar: something at each end, a drag region between them, and
/// the window's own controls after both.
#[derive(IntoElement)]
pub struct WindowChrome {
    id: &'static str,
    leading: Option<AnyElement>,
    trailing: Option<AnyElement>,
    trailing_pad: f32,
}

impl WindowChrome {
    /// `id` distinguishes this window's drag region from another window's.
    pub fn new(id: &'static str) -> Self {
        Self { id, leading: None, trailing: None, trailing_pad: Theme::SPACE_MD }
    }

    /// What sits at the leading edge: a brand lockup, or a plain title.
    pub fn leading(mut self, leading: impl IntoElement) -> Self {
        self.leading = Some(leading.into_any_element());
        self
    }

    /// What sits at the trailing edge, before the window controls.
    pub fn trailing(mut self, trailing: impl IntoElement) -> Self {
        self.trailing = Some(trailing.into_any_element());
        self
    }

    /// Padding between the trailing content and the controls, for a window that
    /// wants that content to line up with something below it.
    pub fn trailing_pad(mut self, padding: f32) -> Self {
        self.trailing_pad = padding;
        self
    }
}

impl RenderOnce for WindowChrome {
    fn render(self, window: &mut Window, cx: &mut gpui::App) -> impl IntoElement {
        let bar = div()
            .h_flex()
            .items_center()
            .w_full()
            .h(px(Theme::TITLEBAR_HEIGHT))
            .flex_none();

        #[cfg(target_os = "macos")]
        let bar = bar.pl(px(TRAFFIC_LIGHTS));

        #[cfg(not(target_os = "macos"))]
        let bar = bar.pl(px(LEADING_INSET));

        bar.children(self.leading)
            .child(drag_region(self.id))
            .children(self.trailing.map(|trailing| {
                div()
                    .flex_none()
                    .pr(px(self.trailing_pad))
                    .child(trailing)
            }))
            .children(controls(self.id, window, cx))
    }
}

/// The part of the titlebar that moves the window.
///
/// It claims all the space between the two ends, so any gap a caller leaves is
/// draggable rather than dead.
///
/// Unlike the caption buttons, the drag region does take a press. The control
/// area alone is the documented path and it is declared here, but gpui 0.2.2
/// drops the caption press for drags specifically, so the move is started
/// directly as well.
fn drag_region(id: &'static str) -> impl IntoElement {
    div()
        .id(id)
        .h_full()
        .flex_1()
        .window_control_area(WindowControlArea::Drag)
        .on_mouse_down(MouseButton::Left, |_, window, _| {
            platform::start_window_drag(window);
        })
}

/// Minimise, maximise and close. Absent on macOS, which draws its own.
#[cfg(target_os = "macos")]
fn controls(_: &'static str, _: &mut Window, _: &mut gpui::App) -> Option<gpui::Div> {
    None
}

#[cfg(not(target_os = "macos"))]
fn controls(chrome_id: &'static str, window: &mut Window, cx: &mut gpui::App) -> Option<gpui::Div> {
    let theme = cx.inari();
    // The glyph reports what the button will do next, so a maximised window
    // offers restore rather than claiming it can maximise again.
    let (maximize_id, maximize_icon) = if window.is_maximized() {
        ("window-restore", IconName::WindowRestore)
    } else {
        ("window-maximize", IconName::WindowMaximize)
    };
    Some(
        div()
            .h_flex()
            .items_center()
            .h_full()
            .flex_none()
            .child(caption(
                chrome_id,
                "window-minimize",
                IconName::WindowMinimize,
                WindowControlArea::Min,
                theme.wash_hover,
                theme.text,
            ))
            .child(caption(
                chrome_id,
                maximize_id,
                maximize_icon,
                WindowControlArea::Max,
                theme.wash_hover,
                theme.text,
            ))
            .child(caption(
                chrome_id,
                "window-close",
                IconName::WindowClose,
                WindowControlArea::Close,
                // The one control that ends the session says so in the colour
                // every desktop already uses for it.
                gpui::rgb(0xc42b1c).into(),
                gpui::white(),
            )),
    )
}

/// One caption button.
///
/// No click handler on Windows by design — see the module note. `occlude` is
/// what keeps the press away from the drag region beside it, so the system
/// receives the non-client press it needs to act on.
///
/// The hover fill eases in over [`super::motion::HOVER`], and its fade key
/// carries the chrome id so two windows' captions fade independently.
#[cfg(not(target_os = "macos"))]
fn caption(
    chrome_id: &'static str,
    id: &'static str,
    icon: IconName,
    area: WindowControlArea,
    hover_bg: gpui::Hsla,
    hover_fg: gpui::Hsla,
) -> impl IntoElement {
    let fade_key = format!("{chrome_id}-{id}");
    let hover_fade_key = fade_key.clone();
    let button = div()
        .id(id)
        .flex()
        .items_center()
        .justify_center()
        .w(px(CAPTION_BUTTON))
        .h_full()
        .flex_none()
        .occlude()
        .window_control_area(area)
        .on_hover(move |hovered, window, _| {
            if super::motion::hover_set(hover_fade_key.clone(), *hovered) {
                // Refresh: request_animation_frame panics outside paint.
                window.refresh();
            }
        })
        .bg(super::motion::hover_blend(fade_key, hover_bg))
        // The label switches the moment the pointer arrives; the fill is what
        // carries the motion, and a label waiting on it reads as lag.
        .hover(move |style| style.text_color(hover_fg))
        .child(Icon::new(icon).size(px(13.0)));

    // Under client-side decorations GPUI's control-area hit testing never
    // fires, so Linux is the one platform that has to act on the click itself.
    #[cfg(target_os = "linux")]
    let button = button.on_click(move |_, window, _| match area {
        WindowControlArea::Min => window.minimize_window(),
        WindowControlArea::Max => window.zoom_window(),
        WindowControlArea::Close => window.remove_window(),
        WindowControlArea::Drag => {},
    });

    button
}

/// A quiet window title, for a window with no brand lockup of its own.
///
/// The bar owns the leading inset, so this adds none of its own.
pub fn title(theme: &Theme, label: &'static str) -> impl IntoElement {
    div()
        .h_flex()
        .items_center()
        .h_full()
        .text_xs()
        .text_color(theme.text_tertiary)
        .child(label)
}
