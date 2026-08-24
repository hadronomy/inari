//! Page structure and the type voice.
//!
//! The type scale is small on purpose — a display size, a section size, a body
//! size, and a caption. Every screen draws from those four, so hierarchy comes
//! from position and weight rather than from a new size invented per view.

use gpui::{
    AnyElement, Div, InteractiveElement as _, IntoElement, ParentElement, RenderOnce, SharedString,
    StatefulInteractiveElement as _, Styled, div, prelude::FluentBuilder as _, px,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    icon::Symbol,
    theme::{ActiveTheme as _, Theme},
};

/// The four type roles. Applied through [`Typography`] so a call site names the
/// role and never a raw size.
pub trait Typography: Styled + Sized {
    /// A page title. One per screen.
    fn text_display(self) -> Self {
        self.text_size(px(22.0))
            .line_height(px(28.0))
            .font_weight(gpui::FontWeight::SEMIBOLD)
    }

    /// A section or card heading.
    fn text_heading(self) -> Self {
        self.text_size(px(15.0))
            .line_height(px(20.0))
            .font_weight(gpui::FontWeight::SEMIBOLD)
    }

    /// Body copy and control labels.
    fn text_body(self) -> Self {
        self.text_size(px(13.5))
            .line_height(px(19.0))
    }

    /// Metadata, timestamps, and helper text.
    fn text_caption(self) -> Self {
        self.text_size(px(12.0))
            .line_height(px(16.0))
    }
}

impl<E: Styled + Sized> Typography for E {}

/// A scrolling page body.
///
/// The measure keeps long guidance readable; without it, Support's prose
/// stretches to the full width of a maximized window and gets hard to track.
/// The scroll viewport stays full width so the scrollbar rides the panel edge
/// rather than the text column.
#[derive(IntoElement)]
pub struct Page {
    id: &'static str,
    children: Vec<AnyElement>,
}

pub fn page(id: &'static str) -> Page {
    Page { id, children: Vec::new() }
}

impl ParentElement for Page {
    fn extend(&mut self, elements: impl IntoIterator<Item = AnyElement>) {
        self.children.extend(elements);
    }
}

impl RenderOnce for Page {
    fn render(self, _: &mut gpui::Window, _: &mut gpui::App) -> impl IntoElement {
        div()
            .id(self.id)
            .size_full()
            .overflow_y_scroll()
            .child(
                div()
                    .v_flex()
                    .w_full()
                    .max_w(px(Theme::CONTENT_WIDTH + Theme::SPACE_2XL * 2.0))
                    .mx_auto()
                    .gap(px(Theme::SPACE_XL))
                    .px(px(Theme::SPACE_2XL))
                    .pt(px(Theme::SPACE_2XL))
                    // A deep bottom inset, not a symmetric one: scrolled to the
                    // end, the last line should sit clear of the panel edge
                    // rather than against it.
                    .pb(px(Theme::SPACE_2XL + Theme::SPACE_LG))
                    .children(self.children),
            )
    }
}

/// The heading block at the top of a page.
#[derive(IntoElement)]
pub struct PageTitle {
    title: SharedString,
    description: SharedString,
}

impl PageTitle {
    pub fn new(title: impl Into<SharedString>, description: impl Into<SharedString>) -> Self {
        Self { title: title.into(), description: description.into() }
    }
}

impl RenderOnce for PageTitle {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        div()
            .v_flex()
            .gap(px(Theme::SPACE_XS + 2.0))
            .child(div().text_display().child(self.title))
            .child(
                div()
                    .max_w(px(Theme::MEASURE))
                    .text_body()
                    .text_color(theme.text_secondary)
                    .child(self.description),
            )
    }
}

/// A titled group of related content.
#[derive(IntoElement)]
pub struct Section {
    title: SharedString,
    /// Sits opposite the title: a count, a state, or an action.
    aside: Option<AnyElement>,
    children: Vec<AnyElement>,
}

impl Section {
    pub fn new(title: impl Into<SharedString>) -> Self {
        Self { title: title.into(), aside: None, children: Vec::new() }
    }

    pub fn aside(mut self, aside: impl IntoElement) -> Self {
        self.aside = Some(aside.into_any_element());
        self
    }
}

impl ParentElement for Section {
    fn extend(&mut self, elements: impl IntoIterator<Item = AnyElement>) {
        self.children.extend(elements);
    }
}

impl RenderOnce for Section {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        div()
            .v_flex()
            .gap(px(Theme::SPACE_MD))
            .child(
                div()
                    .h_flex()
                    .items_center()
                    .justify_between()
                    .gap(px(Theme::SPACE_MD))
                    .min_h(px(24.0))
                    .child(
                        div()
                            .text_heading()
                            .text_color(theme.text)
                            .child(self.title),
                    )
                    .children(self.aside),
            )
            .children(self.children)
    }
}

/// A label above a value. The value is monospaced when it is an identifier or
/// an endpoint: those get read character by character and copied into tickets,
/// where a proportional face makes `l`, `1`, and `I` ambiguous.
#[derive(IntoElement)]
pub struct Field {
    label: SharedString,
    value: SharedString,
    mono: bool,
}

impl Field {
    pub fn new(label: impl Into<SharedString>, value: impl Into<SharedString>) -> Self {
        Self { label: label.into(), value: value.into(), mono: false }
    }

    /// Render the value in the monospace face.
    pub fn technical(mut self) -> Self {
        self.mono = true;
        self
    }
}

impl RenderOnce for Field {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let mono = theme.font_mono.clone();
        div()
            .v_flex()
            .gap(px(2.0))
            .child(
                div()
                    .text_caption()
                    .text_color(theme.text_tertiary)
                    .child(self.label),
            )
            .child(
                div()
                    .text_body()
                    .text_color(theme.text)
                    .when(self.mono, |value| {
                        value
                            .font_family(mono)
                            .text_size(px(12.5))
                    })
                    .child(self.value),
            )
    }
}

/// The state a list reaches when it has nothing to show.
///
/// Always says which of the two empties this is — "nothing exists yet" and
/// "your filter excluded everything" need different actions from the operator.
#[derive(IntoElement)]
pub struct EmptyState {
    symbol: Symbol,
    title: SharedString,
    guidance: SharedString,
}

impl EmptyState {
    pub fn new(
        symbol: impl Into<Symbol>,
        title: impl Into<SharedString>,
        guidance: impl Into<SharedString>,
    ) -> Self {
        Self { symbol: symbol.into(), title: title.into(), guidance: guidance.into() }
    }
}

impl RenderOnce for EmptyState {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        div()
            .v_flex()
            .items_center()
            .justify_center()
            // Claim the container's spare height so the message sits in the
            // middle of the card rather than at the top of it. `flex_grow`
            // rather than `flex_1`: the latter zeroes the basis, which would
            // collapse the message in a card that has no spare height to give.
            .flex_grow()
            .gap(px(Theme::SPACE_SM))
            .w_full()
            .py(px(Theme::SPACE_2XL + Theme::SPACE_SM))
            .px(px(Theme::SPACE_XL))
            .child(
                Icon::from(self.symbol)
                    .size(px(22.0))
                    .text_color(theme.text_tertiary),
            )
            .child(
                div()
                    .text_body()
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .text_color(theme.text_secondary)
                    .child(self.title),
            )
            .child(
                div()
                    .max_w(px(320.0))
                    .text_caption()
                    .text_color(theme.text_tertiary)
                    .text_center()
                    .child(self.guidance),
            )
    }
}

/// A horizontal rule between rows in a list.
pub fn row_divider(theme: &Theme) -> Div {
    div()
        .h(px(1.0))
        .w_full()
        .flex_none()
        .bg(theme.hairline)
}
