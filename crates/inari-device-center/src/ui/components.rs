use gpui::{
    Action, App, Div, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce,
    SharedString, Styled, Window, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{
    Icon, IconName, StyledExt as _,
    button::{Button, ButtonVariants as _},
};

use super::palette;

#[derive(IntoElement)]
pub struct NavigationItem {
    label: SharedString,
    icon: IconName,
    active: bool,
    action: Box<dyn Action>,
}

impl NavigationItem {
    pub fn new(
        label: impl Into<SharedString>,
        icon: IconName,
        active: bool,
        action: impl Action,
    ) -> Self {
        Self { label: label.into(), icon, active, action: Box::new(action) }
    }
}

impl RenderOnce for NavigationItem {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        Button::new(self.label.clone())
            .ghost()
            .w_full()
            .h(rems(2.25))
            .justify_start()
            .gap(rems(0.75))
            .px(rems(0.75))
            .when(self.active, |button| {
                button
                    .bg(colors.surface_raised)
                    .text_color(colors.text)
            })
            .icon(self.icon)
            .label(self.label)
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
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        div()
            .v_flex()
            .gap(rems(0.5))
            .pb(rems(0.5))
            .child(
                div()
                    .id("page-heading")
                    .text_size(rems(1.75))
                    .line_height(rems(2.))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .child(self.title),
            )
            .child(
                div()
                    .max_w(rems(42.))
                    .text_size(rems(0.875))
                    .line_height(rems(1.25))
                    .text_color(colors.text_muted)
                    .child(self.description),
            )
    }
}

#[derive(Clone, Copy)]
pub enum MessageTone {
    Info,
    Success,
    Warning,
    Danger,
}

#[derive(IntoElement)]
pub struct Message {
    id: &'static str,
    tone: MessageTone,
    title: SharedString,
    detail: SharedString,
}

impl Message {
    pub fn new(
        id: &'static str,
        tone: MessageTone,
        title: impl Into<SharedString>,
        detail: impl Into<SharedString>,
    ) -> Self {
        Self { id, tone, title: title.into(), detail: detail.into() }
    }
}

impl RenderOnce for Message {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        let (color, background, icon) = match self.tone {
            MessageTone::Info => (colors.info, colors.info_wash, IconName::Info),
            MessageTone::Success => (colors.success, colors.success_wash, IconName::CircleCheck),
            MessageTone::Warning => (colors.warning, colors.warning_wash, IconName::TriangleAlert),
            MessageTone::Danger => (colors.danger, colors.danger_wash, IconName::CircleX),
        };
        div()
            .id(self.id)
            .flex()
            .items_start()
            .gap(rems(0.75))
            .p(rems(1.))
            .rounded(rems(0.5))
            .bg(background)
            .child(
                Icon::new(icon)
                    .size(rems(1.))
                    .text_color(color),
            )
            .child(
                div()
                    .v_flex()
                    .gap(rems(0.25))
                    .child(
                        div()
                            .font_weight(gpui::FontWeight::SEMIBOLD)
                            .text_color(color)
                            .child(self.title),
                    )
                    .child(
                        div()
                            .text_size(rems(0.8125))
                            .line_height(rems(1.125))
                            .text_color(color)
                            .child(self.detail),
                    ),
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
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        div()
            .v_flex()
            .gap(rems(0.5))
            .p(rems(1.))
            .rounded(rems(0.5))
            .bg(colors.surface)
            .child(
                div()
                    .text_size(rems(0.75))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .text_color(colors.text_muted)
                    .child(self.label),
            )
            .child(
                div()
                    .text_size(rems(1.375))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .text_color(self.accent)
                    .child(self.value),
            )
            .child(
                div()
                    .text_size(rems(0.75))
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
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        div()
            .v_flex()
            .gap(rems(0.5))
            .p(rems(1.))
            .rounded(rems(0.5))
            .bg(colors.surface)
            .child(
                div()
                    .text_size(rems(0.75))
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .text_color(colors.text_muted)
                    .child(self.title),
            )
            .child(
                div()
                    .font_weight(gpui::FontWeight::SEMIBOLD)
                    .child(self.summary),
            )
            .child(
                div()
                    .text_size(rems(0.8125))
                    .line_height(rems(1.125))
                    .text_color(colors.text_muted)
                    .child(self.detail),
            )
    }
}

pub fn page() -> Div {
    div()
        .v_flex()
        .gap(rems(1.5))
        .max_w(rems(72.))
        .mx_auto()
        .px(rems(1.5))
        .py(rems(1.5))
}
