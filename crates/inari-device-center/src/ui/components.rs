use gpui::{
    Action, App, Div, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce,
    SharedString, Styled, Window, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::StyledExt as _;
use gpui_component::button::{Button, ButtonVariants as _};

use super::palette;

#[derive(IntoElement)]
pub struct NavigationItem {
    label: SharedString,
    description: SharedString,
    active: bool,
    action: Box<dyn Action>,
}

impl NavigationItem {
    pub fn new(
        label: impl Into<SharedString>,
        description: impl Into<SharedString>,
        active: bool,
        action: impl Action,
    ) -> Self {
        Self {
            label: label.into(),
            description: description.into(),
            active,
            action: Box::new(action),
        }
    }
}

impl RenderOnce for NavigationItem {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        Button::new(self.label.clone())
            .ghost()
            .w_full()
            .justify_start()
            .py(rems(0.65))
            .px(rems(0.7))
            .when(self.active, |button| {
                button
                    .bg(colors.surface)
                    .border_1()
                    .border_color(colors.border)
            })
            .child(
                div()
                    .v_flex()
                    .items_start()
                    .gap(rems(0.1))
                    .child(
                        div()
                            .font_weight(gpui::FontWeight::MEDIUM)
                            .child(self.label),
                    )
                    .child(
                        div()
                            .text_size(rems(0.68))
                            .text_color(colors.text_muted)
                            .child(self.description),
                    ),
            )
            .on_click(move |_, window, cx| {
                window.dispatch_action(self.action.boxed_clone(), cx);
            })
    }
}

#[derive(IntoElement)]
pub struct PageHeader {
    title: SharedString,
    description: SharedString,
}

impl PageHeader {
    pub fn new(title: impl Into<SharedString>, description: impl Into<SharedString>) -> Self {
        Self { title: title.into(), description: description.into() }
    }
}

impl RenderOnce for PageHeader {
    fn render(self, _: &mut gpui::Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        div()
            .v_flex()
            .gap(rems(0.45))
            .pb(rems(0.45))
            .child(
                div()
                    .id("page-heading")
                    .text_size(rems(2.05))
                    .line_height(rems(2.35))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .child(self.title),
            )
            .child(
                div()
                    .max_w(rems(44.))
                    .text_size(rems(0.92))
                    .line_height(rems(1.35))
                    .text_color(colors.text_muted)
                    .child(self.description),
            )
    }
}

#[derive(IntoElement)]
pub struct MetricCard {
    label: SharedString,
    value: SharedString,
    detail: SharedString,
    accent: gpui::Hsla,
}

impl MetricCard {
    pub fn new(
        label: impl Into<SharedString>,
        value: impl Into<SharedString>,
        detail: impl Into<SharedString>,
        accent: gpui::Hsla,
    ) -> Self {
        Self { label: label.into(), value: value.into(), detail: detail.into(), accent }
    }
}

impl RenderOnce for MetricCard {
    fn render(self, _: &mut gpui::Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        card(colors)
            .child(
                div()
                    .text_size(rems(0.72))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .text_color(self.accent)
                    .child(self.label.to_uppercase()),
            )
            .child(
                div()
                    .mt(rems(1.1))
                    .text_size(rems(1.55))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .child(self.value),
            )
            .child(
                div()
                    .mt(rems(0.25))
                    .text_size(rems(0.78))
                    .text_color(colors.text_muted)
                    .child(self.detail),
            )
    }
}

#[derive(IntoElement)]
pub struct SectionCard {
    title: SharedString,
    summary: SharedString,
    detail: SharedString,
}

impl SectionCard {
    pub fn new(
        title: impl Into<SharedString>,
        summary: impl Into<SharedString>,
        detail: impl Into<SharedString>,
    ) -> Self {
        Self { title: title.into(), summary: summary.into(), detail: detail.into() }
    }
}

impl RenderOnce for SectionCard {
    fn render(self, _: &mut gpui::Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        card(colors)
            .min_h(rems(10.))
            .child(
                div()
                    .text_size(rems(0.78))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .text_color(colors.text_muted)
                    .child(self.title.to_uppercase()),
            )
            .child(
                div()
                    .mt(rems(1.35))
                    .text_size(rems(1.15))
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .child(self.summary),
            )
            .child(
                div()
                    .mt(rems(0.45))
                    .text_size(rems(0.82))
                    .line_height(rems(1.25))
                    .text_color(colors.text_muted)
                    .child(self.detail),
            )
    }
}

fn card(colors: palette::Palette) -> Div {
    div()
        .v_flex()
        .p(rems(1.15))
        .rounded(rems(1.))
        .border_1()
        .border_color(colors.border)
        .bg(colors.surface)
}
