//! Banners: a state that needs a sentence and sometimes an action.
//!
//! The title takes the tone color and the body stays in normal text. Tinting
//! both puts every word of the explanation at the tone's contrast ratio, which
//! is the point where a warning becomes harder to read than the thing it warns
//! about.

use gpui::{
    AnyElement, Hsla, InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce,
    SharedString, Styled, div, px,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    icon::Symbol,
    status::Tone,
    theme::{ActiveTheme as _, Theme},
};

#[derive(IntoElement)]
pub struct Banner {
    id: &'static str,
    tone: Tone,
    title: SharedString,
    detail: SharedString,
    action: Option<AnyElement>,
}

impl Banner {
    pub fn new(
        id: &'static str,
        tone: Tone,
        title: impl Into<SharedString>,
        detail: impl Into<SharedString>,
    ) -> Self {
        Self { id, tone, title: title.into(), detail: detail.into(), action: None }
    }

    /// A recovery control, placed on the banner's trailing edge.
    pub fn action(mut self, action: impl IntoElement) -> Self {
        self.action = Some(action.into_any_element());
        self
    }
}

impl RenderOnce for Banner {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let color = self.tone.color(theme);
        div()
            .id(self.id)
            .h_flex()
            .items_start()
            .gap(px(Theme::SPACE_MD))
            .w_full()
            .p(px(Theme::SPACE_MD + 2.0))
            .rounded(px(Theme::RADIUS_CARD))
            .bg(self.tone.wash(theme))
            .border_1()
            .border_color(Hsla { a: 0.24, ..color })
            .child(
                Icon::from(Symbol::Component(self.tone.symbol()))
                    .size(px(16.0))
                    .flex_none()
                    .mt(px(1.0))
                    .text_color(color),
            )
            .child(
                div()
                    .v_flex()
                    .flex_1()
                    .gap(px(2.0))
                    .child(
                        div()
                            .text_body()
                            .font_weight(gpui::FontWeight::SEMIBOLD)
                            .text_color(color)
                            .child(self.title),
                    )
                    .child(
                        div()
                            .text_body()
                            .text_color(theme.text_secondary)
                            .child(self.detail),
                    ),
            )
            .children(
                self.action
                    .map(|action| div().flex_none().child(action)),
            )
    }
}
