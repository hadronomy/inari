//! The gate: the Device Center's one signature object.
//!
//! Inari's mark is a torii — the gate at the entrance of a shrine. This screen
//! draws the real thing it stands for: the path from this computer, through
//! the local agent, out to the devices. It is not decoration. Every segment is
//! bound to live state, so a glance answers the only question an operator
//! opens this app with — where is the path broken?
//!
//! The composition is legible with no motion at all. When the path is live and
//! motion is allowed, the mark breathes; that is the only thing the animation
//! adds, and it is never the sole carrier of a state.

use gpui::{
    AnimationExt as _, Hsla, IntoElement, ParentElement as _, RenderOnce, SharedString, Styled,
    div, prelude::FluentBuilder as _, px, svg,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    icon::{Glyph, Symbol},
    motion,
    status::{Status, StatusDot, Tone},
    theme::{ActiveTheme as _, Theme},
};

/// The live path from this computer to the devices.
#[derive(IntoElement)]
pub struct Gate {
    service: Status,
    /// `None` while the agent cannot be reached: an unknown device count is
    /// not the same as zero devices, and showing "0 online" during an outage
    /// sends operators to check hardware that is fine.
    devices: Option<(usize, usize)>,
}

impl Gate {
    pub fn new(service: Status, devices: Option<(usize, usize)>) -> Self {
        Self { service, devices }
    }
}

impl RenderOnce for Gate {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let tone = self.service.tone;
        let live = tone == Tone::Positive;
        let accent = theme.accent;

        let mark = svg()
            .path("inari-mark-torii-ui.svg")
            .size(px(40.0))
            .flex_none()
            .text_color(if live { accent } else { theme.text_tertiary });
        let mark = if live && motion::enabled() {
            mark.with_animation("gate-pulse", motion::pulse(0.72, 1.0), move |mark, delta| {
                mark.text_color(Hsla { a: delta, ..accent })
            })
            .into_any_element()
        } else {
            mark.into_any_element()
        };

        let (devices_label, devices_tone) = match self.devices {
            Some((_, 0)) => ("No devices found".into(), Tone::Neutral),
            Some((online, total)) if online == total => {
                (SharedString::from(format!("{total} online")), Tone::Positive)
            },
            Some((online, total)) => (
                SharedString::from(format!("{online} of {total} online")),
                if online == 0 { Tone::Critical } else { Tone::Caution },
            ),
            None => ("State unavailable".into(), Tone::Neutral),
        };

        div()
            .v_flex()
            .w_full()
            .gap(px(Theme::SPACE_LG))
            .p(px(Theme::SPACE_XL))
            .rounded(px(Theme::RADIUS_CARD))
            .bg(theme.surface_raised)
            .border_1()
            .border_color(theme.hairline)
            .child(
                // Capped and centred. Left to fill the panel, the connectors
                // stretch until the three nodes stop reading as one path.
                div()
                    .h_flex()
                    .items_start()
                    .w_full()
                    .max_w(px(440.0))
                    .mx_auto()
                    .child(node(
                        Glyph::Computer.into(),
                        "This computer",
                        None,
                        Tone::Positive,
                        theme,
                    ))
                    .child(connector(tone, theme))
                    .child(
                        div()
                            .flex_none()
                            .pt(px(2.0))
                            .child(mark),
                    )
                    .child(connector(if live { devices_tone } else { tone }, theme))
                    .child(node(
                        Glyph::Device.into(),
                        "Devices",
                        // The count belongs under the node it counts, not on a
                        // separate summary line where it reads as unlabelled.
                        Some((devices_label, devices_tone)),
                        devices_tone,
                        theme,
                    )),
            )
            .child(
                div()
                    .v_flex()
                    .gap(px(1.0))
                    .w_full()
                    .child(
                        div()
                            .h_flex()
                            .items_center()
                            .gap(px(Theme::SPACE_SM))
                            .child(StatusDot::new(tone))
                            .child(
                                div()
                                    .text_body()
                                    .font_weight(gpui::FontWeight::MEDIUM)
                                    .text_color(theme.text)
                                    .child(format!(
                                        "Agent {}",
                                        lowercase_first(&self.service.label)
                                    )),
                            ),
                    )
                    .child(
                        div()
                            .text_caption()
                            .text_color(theme.text_secondary)
                            .child(self.service.detail),
                    ),
            )
    }
}

/// One endpoint of the path, with an optional reading under its label.
fn node(
    symbol: Symbol,
    label: &'static str,
    detail: Option<(SharedString, Tone)>,
    tone: Tone,
    theme: &Theme,
) -> gpui::Div {
    div()
        .v_flex()
        .items_center()
        .gap(px(Theme::SPACE_SM))
        .flex_none()
        .w(px(104.0))
        .child(
            div()
                .relative()
                .flex()
                .items_center()
                .justify_center()
                .size(px(38.0))
                .child(
                    Icon::from(symbol)
                        .size(px(19.0))
                        .text_color(theme.text_secondary),
                )
                .child(
                    div()
                        .absolute()
                        .right(px(-1.0))
                        .bottom(px(-1.0))
                        .child(StatusDot::new(tone).size(7.0)),
                ),
        )
        .child(
            div()
                .v_flex()
                .items_center()
                .gap(px(1.0))
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_secondary)
                        .child(label),
                )
                .children(detail.map(|(text, tone)| {
                    div()
                        .text_caption()
                        .text_center()
                        .text_color(tone.color(theme))
                        .child(text)
                })),
        )
}

/// The segment between two nodes. Solid when traffic can pass, and broken by a
/// visible gap when it cannot, so the failure reads without relying on color.
fn connector(tone: Tone, theme: &Theme) -> gpui::Div {
    let broken = matches!(tone, Tone::Critical | Tone::Neutral);
    let color = if broken { theme.hairline_strong } else { tone.color(theme) };
    div()
        .h_flex()
        .items_center()
        .flex_1()
        .min_w(px(20.0))
        .h(px(38.0))
        .gap(px(if broken { Theme::SPACE_SM } else { 0.0 }))
        .child(
            div()
                .h(px(2.0))
                .flex_1()
                .rounded_full()
                .bg(color),
        )
        .when(broken, |line| {
            line.child(
                Icon::from(Symbol::Component(gpui_component::IconName::Close))
                    .size(px(10.0))
                    .flex_none()
                    .text_color(theme.text_tertiary),
            )
        })
        .child(
            div()
                .h(px(2.0))
                .flex_1()
                .rounded_full()
                .bg(color),
        )
}

/// "Running" becomes "running" so it reads as a sentence after "Agent".
fn lowercase_first(label: &str) -> String {
    let mut characters = label.chars();
    match characters.next() {
        Some(first) => first.to_lowercase().collect::<String>() + characters.as_str(),
        None => String::new(),
    }
}
