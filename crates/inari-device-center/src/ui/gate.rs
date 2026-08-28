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

use std::time::Duration;

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
const WIRE_PITCH: f32 = CELL + GAP;
/// How many data packets ride a wire at once. Three at different speeds and
/// offsets keeps traffic constant: a packet is always somewhere on the line.
const PACKETS: usize = 3;
/// Each packet's speed as a multiple of the cascade period, and where in the
/// period it starts. Uneven speeds make packets pass through each other.
const WIRE_SPEEDS: [f32; PACKETS] = [1.0, 0.78, 1.21];
const WIRE_OFFSETS: [f32; PACKETS] = [0.0, 0.37, 0.71];
/// A packet's head alpha, and how the comet trail behind it falls away.
const WIRE_HEAD: f32 = 0.9;
const WIRE_TRAIL: [f32; 4] = [1.0, 0.55, 0.28, 0.12];
/// What a wire cell rests at between packets: the carrier, always lit.
const WIRE_REST: f32 = 0.16;
/// A glitching stream quantises time into this many buckets per period, so
/// the corruption pattern changes stepwise — digital, not soft.
const GLITCH_TICKS: u32 = 14;
/// Per cell per tick: the chance dead air replaces a cell, and the chance a
/// live cell reports corruption in the danger tone.
const GLITCH_DROPOUT: f32 = 0.07;
const GLITCH_ERROR: f32 = 0.045;
/// How long the last packet lives after the path fails.
const LAST_BREATH: Duration = Duration::from_millis(700);

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
/// dim carrier with discrete packets of light travelling source-wards, the
/// same staggered phase wave the alert's cascade uses, so "live" reads as
/// motion in the app's one visual language. On a caution tone the traffic
/// glitches — cells drop out and some flash the danger tone — because that
/// is what a degraded link is. When it is broken the wire goes still — a gap
/// and a cross where the light stops — and the last packet dies at the break.
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
    // The cross speaks the tone: danger when device work is blocked, the
    // quiet tertiary when there is merely nothing to connect to.
    let cross_color = if tone == Tone::Critical { theme.danger } else { theme.text_tertiary };
    let cross = Icon::from(Symbol::Component(gpui_component::IconName::Close))
        .size(px(10.0))
        .flex_none()
        .text_color(cross_color);
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
        .relative()
        .when(broken, |line| {
            line.gap(px(Theme::SPACE_SM))
                .child(stub())
                .child(cross)
                .child(stub())
                // One last packet leaves the source and dies at the break —
                // the attempt the wire made before it went down. Keyed on the
                // id and tone, so it plays when the path fails, not forever.
                .when(motion::enabled(), |line| line.child(last_breath(id, tone, theme)))
        })
        .when(!broken, |line| {
            // The wire canvas is absolute; this wrapper is the box it fills.
            line.child(
                div()
                    .relative()
                    .flex_1()
                    .h_full()
                    .child(flowing_wire(id, tone)),
            )
        })
}

/// The live wire at one moment: a dim carrier with discrete packets of light
/// travelling source-wards.
///
/// Painted rather than built from divs for the same reason the alert's wall
/// is — a canvas answers to the width it is actually given, and a flexed
/// connector's width is decided at layout time. Three packets run the line at
/// different speeds, each a head with a comet trail behind it, so the traffic
/// reads as discrete data rather than as one broad wave. On the caution tone
/// the stream corrupts: cells drop out and some flash the danger tone.
fn flowing_wire(id: &'static str, tone: Tone) -> impl IntoElement {
    let still = !motion::enabled();
    wire_canvas(tone, if still { f32::NAN } else { 0.0 })
        .with_animation(
            gpui::SharedString::from(format!("gate-flow-{id}")),
            motion::cascade(),
            move |_, delta| wire_canvas(tone, delta),
        )
        .into_any_element()
}

/// The wire at one instant. `NaN` delta paints the still form the reduced
/// motion preference shows: every cell at carrier, no traffic.
fn wire_canvas(tone: Tone, delta: f32) -> gpui::Canvas<()> {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, cx| {
            let theme = cx.inari();
            let color = tone.color(theme);
            let glitching = tone == Tone::Caution && !delta.is_nan();
            let columns = (f32::from(bounds.size.width) / WIRE_PITCH).ceil() as usize;
            let y = f32::from(bounds.origin.y) + (f32::from(bounds.size.height) - CELL) / 2.0;
            for column in 0..columns {
                let (mut alpha, corrupted) = wire_cell(column, columns, delta, glitching);
                if alpha < 0.004 {
                    continue;
                }
                if corrupted {
                    alpha = WIRE_HEAD * 0.6;
                }
                let paint = if corrupted { theme.danger } else { color };
                let x = f32::from(bounds.origin.x) + column as f32 * WIRE_PITCH;
                window.paint_quad(fill(
                    Bounds { origin: point(px(x), px(y)), size: size(px(CELL), px(CELL)) },
                    Hsla { a: alpha, ..paint },
                ));
            }
        },
    )
    .absolute()
    .inset_0()
}

/// One cell of the live wire at `delta`: its alpha, and whether it flashes as
/// a corrupted byte.
///
/// The carrier is every cell resting lit — the link is up. Traffic rides on
/// top as three packets, each a head with a two-cell comet trail, at
/// different speeds so passing packets sum into brighter moments. On a
/// glitching tone the stream quantises into time buckets and degrades
/// digitally: cells drop out outright, packet positions jitter, and sparse
/// cells report corruption. All of it is a pure function of the clock, so a
/// dropped frame lands where the traffic actually is.
fn wire_cell(column: usize, columns: usize, delta: f32, glitching: bool) -> (f32, bool) {
    if delta.is_nan() {
        return (WIRE_REST, false);
    }
    let mut alpha = WIRE_REST;
    let tick = (delta * GLITCH_TICKS as f32).floor() as u32;
    for packet in 0..PACKETS {
        let speed = WIRE_SPEEDS[packet];
        let offset = WIRE_OFFSETS[packet];
        let mut head = ((delta * speed + offset).rem_euclid(1.0)) * columns as f32;
        if glitching {
            // The link stutters: packets lurch a cell either way.
            head += (noise(packet as u32 + 7, tick) - 0.5) * 2.4;
        }
        // How far this cell sits behind the packet's head, wrapping so a
        // packet exiting the far end re-enters at the source.
        let behind = (head - column as f32)
            .rem_euclid(columns as f32)
            .round() as usize;
        alpha = alpha.max(WIRE_HEAD * WIRE_TRAIL[behind.min(WIRE_TRAIL.len() - 1)]);
    }
    let mut corrupted = false;
    if glitching {
        // Dead air where a cell should be, and sparse corruption the danger
        // tone will flash: the two faces of a link that needs attention.
        if noise(column as u32, tick) < GLITCH_DROPOUT {
            alpha = 0.0;
        } else if noise((column + 91) as u32, tick) < GLITCH_ERROR {
            corrupted = true;
        }
    }
    (alpha, corrupted)
}

/// Deterministic white noise in [0, 1), so the glitch pattern is stable
/// within a tick and different in the next — digital, not soft.
fn noise(a: u32, b: u32) -> f32 {
    let mut h = a.wrapping_mul(0x9E37_79B9) ^ b.wrapping_mul(0x85EB_CA6B);
    h ^= h >> 13;
    h = h.wrapping_mul(0xC2B2_AE35);
    h ^= h >> 16;
    (h & 0x00FF_FFFF) as f32 / 0x0100_0000 as f32
}

/// The last packet: leaves the source and dies at the break.
///
/// Played once when the path fails, keyed on the tone — the attempt the wire
/// made before it went down, not a loop pretending one is coming.
fn last_breath(id: &'static str, tone: Tone, theme: &Theme) -> impl IntoElement {
    let color = tone.color(theme);
    last_breath_canvas(color, 0.0)
        .with_animation(
            gpui::SharedString::from(format!("gate-last-breath-{id}-{}", motion_key(tone))),
            gpui::Animation::new(LAST_BREATH).with_easing(gpui::ease_out_quint()),
            move |_, delta| last_breath_canvas(color, delta),
        )
        .into_any_element()
}

/// The dying packet at `delta`: a head with a short trail, its light fading
/// as it approaches the break at the connector's middle.
fn last_breath_canvas(color: Hsla, delta: f32) -> gpui::Canvas<()> {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, _| {
            let columns = (f32::from(bounds.size.width) / WIRE_PITCH) as usize;
            let y = f32::from(bounds.origin.y) + (f32::from(bounds.size.height) - CELL) / 2.0;
            let head = delta * 0.5 * columns as f32;
            let fade = 1.0 - delta;
            for (behind, trail) in WIRE_TRAIL.iter().take(3).enumerate() {
                let column = (head - behind as f32).floor();
                if column < 0.0 {
                    continue;
                }
                let alpha = WIRE_HEAD * trail * fade;
                if alpha < 0.004 {
                    continue;
                }
                let x = f32::from(bounds.origin.x) + column * WIRE_PITCH;
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

/// A stable element-id fragment per tone, so a tone change remounts the
/// one-shot animations and they play again.
fn motion_key(tone: Tone) -> usize {
    match tone {
        Tone::Positive => 0,
        Tone::Busy => 1,
        Tone::Neutral => 2,
        Tone::Caution => 3,
        Tone::Critical => 4,
    }
}

/// "Running" becomes "running" so it reads as a sentence after "Agent".
fn lowercase_first(label: &str) -> String {
    let mut characters = label.chars();
    match characters.next() {
        Some(first) => first.to_lowercase().collect::<String>() + characters.as_str(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noise_is_deterministic_and_bounded() {
        for seed in 0..64u32 {
            let a = noise(seed, seed * 7 + 1);
            assert!((0.0..=1.0).contains(&a), "noise({seed}) = {a}");
            assert_eq!(a, noise(seed, seed * 7 + 1), "noise must be stable");
        }
        // Two different inputs produce different values for at least some
        // cells, or the glitch pattern would be uniform.
        let distinct = (0..32u32)
            .map(|i| noise(i, i / 2))
            .collect::<Vec<_>>();
        assert!(
            distinct
                .iter()
                .any(|a| *a != distinct[0])
        );
    }

    #[test]
    fn every_wire_cell_carries_the_carrier() {
        // Even with no packets anywhere near, the cell rests lit: the link is
        // up, and the wire must say so in its still form too.
        for column in 0..40usize {
            let (alpha, corrupted) = wire_cell(column, 40, f32::NAN, false);
            assert_eq!(alpha, WIRE_REST);
            assert!(!corrupted);
        }
    }

    #[test]
    fn traffic_sweeps_the_whole_wire_and_stays_lit() {
        // Over one full period, every cell is touched by a packet head at
        // least once, and no cell ever exceeds the head's own alpha.
        let columns = 40usize;
        let mut ever_lit = vec![false; columns];
        for step in 0..60 {
            let delta = step as f32 / 60.0;
            for (column, lit) in ever_lit.iter_mut().enumerate() {
                let (alpha, _) = wire_cell(column, columns, delta, false);
                assert!(alpha <= WIRE_HEAD + f32::EPSILON, "{alpha}");
                if alpha > WIRE_REST {
                    *lit = true;
                }
            }
        }
        assert!(ever_lit.iter().all(|lit| *lit), "a cell saw no traffic");
    }

    #[test]
    fn a_glitching_wire_still_flows_but_corrupts() {
        // Caution is not broken: most cells keep their carrier, some flash
        // corruption, and dead air appears without taking whole cells away
        // permanently.
        let columns = 60usize;
        let mut corrupted = 0;
        let mut dead_air = 0;
        let mut cells = 0;
        for step in 0..20 {
            let delta = step as f32 / 20.0;
            for column in 0..columns {
                let (alpha, flash) = wire_cell(column, columns, delta, true);
                cells += 1;
                if flash {
                    corrupted += 1;
                }
                if alpha == 0.0 {
                    dead_air += 1;
                }
            }
        }
        assert!(corrupted > 0, "no corruption flashed");
        assert!(dead_air > 0, "no dead air");
        assert!(dead_air * 10 < cells, "dead air took over: {dead_air} of {cells}");
    }

    #[test]
    fn a_steady_wire_never_corrupts_or_drops() {
        for step in 0..30 {
            let delta = step as f32 / 30.0;
            for column in 0..30usize {
                let (alpha, corrupted) = wire_cell(column, 30, delta, false);
                assert!(!corrupted);
                assert!(alpha > 0.0, "a healthy wire dropped a cell");
            }
        }
    }
}
