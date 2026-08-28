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
    AnimationExt as _, Bounds, Hsla, IntoElement, ParentElement as _, RenderOnce, SharedString,
    Styled, canvas, div, fill, point, prelude::FluentBuilder as _, px, size, svg,
};
use gpui_component::{Icon, StyledExt as _};

use super::{
    content::Typography as _,
    icon::{Glyph, Symbol},
    motion,
    status::{Status, StatusDot, Tone},
    theme::{ActiveTheme as _, Theme},
};

/// The live wire's own geometry: a segment, and the gap to the next one.
const CELL: f32 = 3.0;
const GAP: f32 = 1.0;
/// How far apart two neighbouring columns sit in the wave. Small enough that
/// the crest reads as one travelling band rather than as cells taking turns.
const STAGGER: f32 = 0.03;
/// What a wire segment dims to between crests, as a fraction of its lit
/// alpha. Never zero: the wire has to describe the same live path when the
/// crest is elsewhere as when it is here.
const REST_WIRE: f32 = 0.55;

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
                    .child(connector("gate-wire-in", tone, theme))
                    .child(
                        div()
                            .flex_none()
                            .pt(px(2.0))
                            .child(mark),
                    )
                    .child(connector(
                        "gate-wire-out",
                        if live { devices_tone } else { tone },
                        theme,
                    ))
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

/// The segment between two nodes. When traffic passes it is a live wire: a
/// dim base with a crest of light travelling source-wards, the same staggered
/// phase wave the alert's cascade uses, so "live" reads as motion in the
/// app's one visual language. When it is broken the wire goes still — a gap
/// and a cross where the light stops — because a broken path has nothing
/// flowing to show.
fn connector(id: &'static str, tone: Tone, theme: &Theme) -> gpui::Div {
    let broken = matches!(tone, Tone::Critical | Tone::Neutral);
    let color = if broken { theme.hairline_strong } else { tone.color(theme) };
    // The stubs survive when broken — the break is a gap in a wire, not the
    // absence of one — and the cross settles in so the failure arrives rather
    // than pops. Keyed on the id, so a state change remounts and replays it.
    let stub = || {
        div()
            .h(px(2.0))
            .flex_1()
            .rounded_full()
            .bg(color)
    };
    let cross = Icon::from(Symbol::Component(gpui_component::IconName::Close))
        .size(px(10.0))
        .flex_none()
        .text_color(theme.text_tertiary);
    let cross = if motion::enabled() {
        cross
            .with_animation(
                gpui::SharedString::from(format!("gate-break-{id}")),
                motion::settle(),
                |cross, delta| cross.opacity(delta),
            )
            .into_any_element()
    } else {
        cross.into_any_element()
    };
    div()
        .h_flex()
        .items_center()
        .flex_1()
        .min_w(px(20.0))
        .h(px(38.0))
        .gap(px(if broken { Theme::SPACE_SM } else { 0.0 }))
        .child(stub())
        .when(broken, |line| line.child(cross))
        .when(!broken, |line| line.child(flowing_wire(id, tone, theme)))
        .child(stub())
}

/// The live wire at one moment: dim segments with a crest running through.
///
/// Painted rather than built from divs for the same reason the alert's wall
/// is — a canvas answers to the width it is actually given, and a flexed
/// connector's width is decided at layout time. Cells run the line at the
/// cascade's own pitch; each carries the wave phase of its position, so one
/// crest travels source-wards and the rest of the wire stays at a quiet base
/// alpha. The direction is the story: this computer, through the agent, out
/// to the devices.
fn flowing_wire(id: &'static str, tone: Tone, theme: &Theme) -> impl IntoElement {
    let color = tone.color(theme);
    let base = 0.35;
    if !motion::enabled() {
        return static_wire(color, base).into_any_element();
    }
    static_wire(color, base)
        .with_animation(
            gpui::SharedString::from(format!("gate-flow-{id}")),
            motion::cascade(),
            move |_, delta| flowing_wire_canvas(color, base, delta),
        )
        .into_any_element()
}

/// The wire with no motion: the base at full presence.
fn static_wire(color: Hsla, base: f32) -> gpui::Canvas<()> {
    flowing_wire_canvas(color, base, f32::NAN)
}

/// The wire at one instant of the travelling wave. `NaN` delta paints the
/// still form: every segment at base, no crest.
fn flowing_wire_canvas(color: Hsla, base: f32, delta: f32) -> gpui::Canvas<()> {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, _| {
            let height = f32::from(bounds.size.height);
            let width = f32::from(bounds.size.width);
            let pitch = CELL + GAP;
            let columns = (width / pitch).ceil() as usize;
            let y = f32::from(bounds.origin.y) + (height - CELL) / 2.0;
            for column in 0..columns {
                let crest = if delta.is_nan() {
                    0.0
                } else {
                    let phase = motion::staggered_phase(delta, column, STAGGER);
                    motion::pulse_wave(phase).powf(3.0)
                };
                let alpha = base * (REST_WIRE + (1.0 - REST_WIRE) * crest);
                if alpha < 0.004 {
                    continue;
                }
                let x = f32::from(bounds.origin.x) + column as f32 * pitch;
                window.paint_quad(fill(
                    Bounds { origin: point(px(x), px(y)), size: size(px(CELL), px(CELL)) },
                    Hsla { a: alpha, ..color },
                ));
            }
        },
    )
    .absolute()
    .inset_0()
}

/// "Running" becomes "running" so it reads as a sentence after "Agent".
fn lowercase_first(label: &str) -> String {
    let mut characters = label.chars();
    match characters.next() {
        Some(first) => first.to_lowercase().collect::<String>() + characters.as_str(),
        None => String::new(),
    }
}
