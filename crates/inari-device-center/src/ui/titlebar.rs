//! The window's own top edge.
//!
//! Every window this app opens draws its own titlebar, because the shell wants
//! one continuous surface from the top of the window to the bottom of the
//! content. That means every window is also responsible for the things a system
//! titlebar would have given it for free — chiefly that dragging it moves the
//! window.
//!
//! Leaving that to each window is how the enrollment window shipped unmovable.
//! So the drag region is not something a caller remembers to add: it is the
//! part between the two ends, and a caller can only choose what sits at those
//! ends.

use gpui::{
    AnyElement, InteractiveElement as _, IntoElement, MouseButton, ParentElement as _, RenderOnce,
    Styled, WindowControlArea, div, px,
};
use gpui_component::{StyledExt as _, TitleBar};

use super::theme::Theme;
use crate::infrastructure::platform;

/// A window titlebar: something at each end, and a drag region between them.
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

    /// Right padding, for a window that wants its trailing content to line up
    /// with something below it.
    pub fn trailing_pad(mut self, padding: f32) -> Self {
        self.trailing_pad = padding;
        self
    }
}

impl RenderOnce for WindowChrome {
    fn render(self, _: &mut gpui::Window, _: &mut gpui::App) -> impl IntoElement {
        TitleBar::new()
            .h(px(Theme::TITLEBAR_HEIGHT))
            // Transparent on both counts: the titlebar is not a bar, it is the
            // top of whatever surface the window already paints.
            .bg(gpui::transparent_black())
            .border_color(gpui::transparent_black())
            .pr(px(self.trailing_pad))
            .children(self.leading)
            .child(drag_region(self.id))
            .children(self.trailing)
    }
}

/// The part of the titlebar that moves the window.
///
/// It claims all the space between the two ends, so any gap a caller leaves is
/// draggable rather than dead.
///
/// The control area is declared on every platform, but gpui 0.2.2 only consumes
/// it on Windows, and its caption press path still drops drags there (fixed
/// upstream after this release). So movement actually goes through
/// `platform::start_window_drag`, and the declaration is what keeps the window
/// manager's own affordances — snap layouts, double-click to maximise — working.
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

/// A quiet window title, for a window with no brand lockup of its own.
pub fn title(theme: &Theme, label: &'static str) -> impl IntoElement {
    div()
        .h_flex()
        .items_center()
        .h_full()
        .text_xs()
        .text_color(theme.text_tertiary)
        .child(label)
}
