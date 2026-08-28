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
    motion::CASCADE,
    status::{Status, StatusDot, Tone},
    theme::{ActiveTheme as _, Theme},
};

/// The live wire's own geometry: a segment, and the gap to the next one.
const CELL: f32 = 3.0;
const GAP: f32 = 1.0;
const WIRE_PITCH: f32 = CELL + GAP;
/// How many data packets ride a wire at once. Four at different speeds and
/// offsets keeps traffic constant: a packet is always somewhere on the line.
const PACKETS: usize = 4;
/// Each packet's speed as a multiple of the cascade period, and where in the
/// period it starts. Uneven speeds make packets pass through each other.
const WIRE_SPEEDS: [f32; PACKETS] = [1.0, 0.78, 1.21, 0.9];
const WIRE_OFFSETS: [f32; PACKETS] = [0.0, 0.37, 0.71, 0.52];
/// A packet's head alpha, and how the comet trail behind it falls away.
const WIRE_HEAD: f32 = 0.9;
const WIRE_TRAIL: [f32; 4] = [1.0, 0.55, 0.28, 0.12];
/// What a wire cell rests at between packets: the carrier, always lit. The
/// line must never vanish — the traffic rides on a wire the operator can
/// still see.
const WIRE_REST: f32 = 0.3;
/// A glitching stream quantises time, so the corruption pattern changes
/// stepwise — digital, not soft.
const TICKS_PER_SECOND: f32 = 7.4;
/// Per cell per tick outside the tears: the faint chance of dead air, the
/// texture of a link that is merely imperfect.
const GLITCH_DROPOUT: f32 = 0.02;
/// The tears: how many cross a wire, how often, and how wide they tear.
const TEAR_COUNT: usize = 2;
const TEAR_PERIOD: Duration = Duration::from_millis(2400);
const TEAR_HALF_WIDTH: f32 = 2.5;
/// How long the last packet lives after the path fails.
const LAST_BREATH: Duration = Duration::from_millis(700);
const TAU: f32 = std::f32::consts::TAU;

/// The wire's time source: one process-lifetime instant. A per-render
/// `Instant::now` resets `t` to zero every frame, which froze the traffic in
/// its opening position; a shared clock keeps every wire on one continuous
/// timeline, wherever and whenever it is mounted.
static WIRE_CLOCK: std::sync::LazyLock<std::time::Instant> =
    std::sync::LazyLock::new(std::time::Instant::now);

/// One flanking trace of the information field: a pixel sine on one side of
/// the line, with its own frequency, amplitude, and travel speed.
struct WakeLane {
    /// Which side of the line: +1 above, -1 below.
    direction: f32,
    /// Frequency as a multiple of the base wavelength.
    frequency: f32,
    /// Peak offset from the trace's rest position, in pixels.
    amplitude: f32,
    /// Travel speed along the wire, in pixels per second.
    speed: f32,
}

/// The two traces: above at a long, slow wavelength; below shorter and
/// quicker. Different frequencies and amplitudes, as asked.
const WAKE_LANES: [WakeLane; 2] = [
    WakeLane { direction: 1.0, frequency: 1.0, amplitude: 4.0, speed: 55.0 },
    WakeLane { direction: -1.0, frequency: 1.55, amplitude: 2.5, speed: 92.0 },
];
/// Each trace's rest offset from the line, in pixels.
const WAKE_GAP: f32 = 3.0;
/// What a trace cell rests at: present, quiet.
const WAKE_REST: f32 = 0.13;
/// The base wavelength of the field, in pixels.
const WIRE_WAVELENGTH: f32 = 84.0;

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

/// The live wire at time `t` seconds since the wire mounted.
///
/// Painted rather than built from divs for the same reason the alert's wall
/// is — a canvas answers to the width it is actually given, and a flexed
/// connector's width is decided at layout time. Everything the wire shows is
/// a pure function of `t`, never of the animation's looping delta: a looping
/// delta made the comet trails teleport at the wrap and the glitch pattern
/// snap back to its start, which read as the wire rewinding.
fn flowing_wire(id: &'static str, tone: Tone) -> impl IntoElement {
    let still = !motion::enabled();
    let t = (!still).then(|| WIRE_CLOCK.elapsed().as_secs_f32());
    wire_canvas(tone, t)
        .with_animation(
            gpui::SharedString::from(format!("gate-flow-{id}")),
            // The cascade only drives repaint cadence here; the wire's math
            // runs on the shared clock, so neither the loop boundary nor a
            // re-render can rewind it.
            motion::cascade(),
            move |_, _| wire_canvas(tone, Some(WIRE_CLOCK.elapsed().as_secs_f32())),
        )
        .into_any_element()
}

/// The wire at time `t`. `None` paints the still form the reduced motion
/// preference shows: carrier and flanking traces at rest, no traffic.
fn wire_canvas(tone: Tone, clock: Option<f32>) -> gpui::Canvas<()> {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, cx| {
            let theme = cx.inari();
            let color = tone.color(theme);
            let glitching = tone == Tone::Caution;
            let columns = (f32::from(bounds.size.width) / WIRE_PITCH).ceil() as usize;
            let center = f32::from(bounds.origin.y) + f32::from(bounds.size.height) / 2.0;
            let t = clock;
            for column in 0..columns {
                let x = f32::from(bounds.origin.x) + column as f32 * WIRE_PITCH;
                let (mut alpha, corrupted) = wire_cell(column, columns, t, glitching);
                if corrupted {
                    alpha = WIRE_HEAD * 0.6;
                }
                if alpha > 0.004 {
                    let paint = if corrupted { theme.danger } else { color };
                    window.paint_quad(fill(
                        Bounds {
                            origin: point(px(x), px(center - CELL / 2.0)),
                            size: size(px(CELL), px(CELL)),
                        },
                        Hsla { a: alpha, ..paint },
                    ));
                }
                for (offset, alpha, corrupted) in wake_cells(column, columns, t, glitching) {
                    if alpha < 0.004 {
                        continue;
                    }
                    let paint = if corrupted { theme.danger } else { color };
                    window.paint_quad(fill(
                        Bounds {
                            origin: point(px(x), px(center + offset - CELL / 2.0)),
                            size: size(px(CELL), px(CELL)),
                        },
                        Hsla { a: alpha, ..paint },
                    ));
                }
            }
        },
    )
    .absolute()
    .inset_0()
}

/// One cell of the live wire at time `t`: its alpha, and whether it flashes
/// as a corrupted byte.
///
/// The carrier is every cell resting lit — the link is up. Traffic rides on
/// top as packets, each a head with a comet trail behind it, at different
/// speeds so passing packets sum into brighter moments. Trails are clipped
/// at the wire's ends rather than wrapped: a packet exits right and re-enters
/// left empty, which is what makes the loop invisible. On a glitching tone
/// the stream quantises into time buckets and degrades digitally — cells
/// drop out outright, packet positions lurch, and sparse cells report
/// corruption. All of it is a pure function of the clock, so a dropped frame
/// lands where the traffic actually is.
/// Deterministic white noise in [0, 1), so the glitch pattern is stable
/// within a tick and different in the next — digital, not soft.
fn noise(a: u32, b: u32) -> f32 {
    let mut h = a.wrapping_mul(0x9E37_79B9) ^ b.wrapping_mul(0x85EB_CA6B);
    h ^= h >> 13;
    h = h.wrapping_mul(0xC2B2_AE35);
    h ^= h >> 16;
    (h & 0x00FF_FFFF) as f32 / 0x0100_0000 as f32
}

fn wire_cell(column: usize, columns: usize, t: Option<f32>, glitching: bool) -> (f32, bool) {
    let Some(t) = t else {
        return (WIRE_REST, false);
    };
    let period = CASCADE.as_secs_f32();
    let mut alpha = WIRE_REST;
    let tick = (t * TICKS_PER_SECOND) as u32;
    for packet in 0..PACKETS {
        let phase = (t / period * WIRE_SPEEDS[packet] + WIRE_OFFSETS[packet]).rem_euclid(1.0);
        let mut head = phase * columns as f32;
        if glitching {
            // The link stutters: packets lurch a cell either way.
            head += (noise(packet as u32 + 7, tick) - 0.5) * 2.4;
        }
        // How far this cell sits behind the packet's head. Past the trail's
        // reach the cell is just carrier; nothing wraps, so a packet exits
        // the wire with its trail instead of teleporting it to the source.
        let behind = (head - column as f32).round();
        if behind < 0.0 || behind >= WIRE_TRAIL.len() as f32 {
            continue;
        }
        alpha = alpha.max(WIRE_HEAD * WIRE_TRAIL[behind as usize]);
    }
    let corrupted = false;
    if glitching {
        // The tears carry the corruption: a front passing over the cell
        // tears it — dead air, or a flash of the danger tone — and the
        // further out it is, the lighter the touch.
        let strength = tear_strength(&tears(t, columns), column);
        if strength > 0.0 {
            let roll = noise(column as u32, tick);
            if roll < 0.55 * strength {
                return (0.0, false);
            }
            if roll < 0.85 {
                return (WIRE_HEAD * 0.6, true);
            }
            alpha *= 1.0 - 0.6 * strength;
        } else if noise(column as u32, tick) < GLITCH_DROPOUT {
            alpha = 0.0;
        }
    }
    (alpha, corrupted)
}

/// The two tears crossing the wire at this tick: (centre, half-width) in
/// cells. Column-quantised with the tick, so the scan steps digitally
/// instead of sweeping smoothly.
fn tears(t: f32, columns: usize) -> [(f32, f32); TEAR_COUNT] {
    let tick = (t * TICKS_PER_SECOND) as u32;
    let mut fronts = [(0.0, 0.0); TEAR_COUNT];
    for (index, front) in fronts.iter_mut().enumerate() {
        let progress =
            (t / TEAR_PERIOD.as_secs_f32() + index as f32 / TEAR_COUNT as f32).rem_euclid(1.0);
        *front = (
            progress * columns as f32,
            TEAR_HALF_WIDTH * (0.7 + 0.6 * noise(index as u32 + 3, tick)),
        );
    }
    fronts
}

/// How hard the tears are passing over `column` right now: 0 for untouched,
/// rising to 1 at a tear's centre.
fn tear_strength(fronts: &[(f32, f32); TEAR_COUNT], column: usize) -> f32 {
    let column = column as f32;
    let mut strength: f32 = 0.0;
    for &(centre, half_width) in fronts {
        let distance = (column - centre).abs() / half_width;
        if distance < 1.0 {
            strength = strength.max(1.0 - distance);
        }
    }
    strength
}

/// The information field: two pixel sine traces flanking the line, above and
/// below, each its own frequency, amplitude, and travel speed. Rows snap to
/// the two-pixel grid — the termy hard-edge rule — so the waves read as
/// stepped data, not as a curve. On a glitching tone the traces quantise in
/// time (they jump between steps) and corrupt like the line does.
fn wake_cells(
    column: usize,
    columns: usize,
    t: Option<f32>,
    glitching: bool,
) -> [(f32, f32, bool); WAKE_LANES.len()] {
    let mut cells = [(0.0, 0.0, false); WAKE_LANES.len()];
    let Some(t) = t else {
        for (cell, lane) in cells.iter_mut().zip(WAKE_LANES.iter()) {
            *cell = (lane.direction * WAKE_GAP, WAKE_REST, false);
        }
        return cells;
    };
    // Fade the traces in from the wire's ends so they never hard-clip at
    // the nodes.
    let edge = ((column as f32 / 3.0).min(1.0)).min(((columns - 1 - column) as f32 / 3.0).min(1.0));
    // On a glitching tone the traces quantise in time: they jump between
    // ten shapes a second instead of flowing.
    let step = if glitching { (t * 10.0).floor() / 10.0 } else { t };
    let fronts = if glitching { tears(t, columns) } else { Default::default() };
    let tick = (t * TICKS_PER_SECOND) as u32;
    for (cell, lane) in cells.iter_mut().zip(WAKE_LANES.iter()) {
        let wavelength = WIRE_WAVELENGTH / lane.frequency;
        let mut wave = (TAU * (column as f32 * WIRE_PITCH - step * lane.speed) / wavelength).sin();
        let mut corrupted = false;
        if glitching {
            // A tear tears the wave where it passes: the trace jumps a
            // quarter-turn out of phase and gets yanked off its line.
            let strength = tear_strength(&fronts, column);
            if strength > 0.0 {
                let roll = noise(column as u32 + 17, tick);
                if roll < 0.35 * strength {
                    *cell = (0.0, 0.0, false);
                    continue;
                }
                wave = (TAU * (column as f32 * WIRE_PITCH - (step + 0.37) * lane.speed)
                    / wavelength)
                    .sin();
                if roll < 0.6 {
                    corrupted = true;
                }
            }
        }
        let offset = lane.direction * (WAKE_GAP + lane.amplitude * wave);
        let snapped = (offset / 2.0).round() * 2.0;
        *cell = (snapped, WAKE_REST * edge, corrupted);
    }
    cells
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
    fn the_still_form_carries_every_cell_and_rests_the_wake() {
        for column in 0..40usize {
            let (alpha, corrupted) = wire_cell(column, 40, None, false);
            assert_eq!(alpha, WIRE_REST);
            assert!(!corrupted);
        }
        for (offset, alpha, corrupted) in wake_cells(5, 40, None, false) {
            assert_eq!(alpha, WAKE_REST);
            assert!(!corrupted);
            assert!(offset.abs() >= WAKE_GAP);
        }
    }

    #[test]
    fn traffic_sweeps_the_whole_wire_over_one_period() {
        let columns = 40usize;
        let period = CASCADE.as_secs_f32();
        let mut ever_lit = vec![false; columns];
        for step in 0..80 {
            let t = step as f32 / 80.0 * period;
            for (column, lit) in ever_lit.iter_mut().enumerate() {
                let (alpha, _) = wire_cell(column, columns, Some(t), false);
                assert!(alpha <= WIRE_HEAD + f32::EPSILON, "{alpha}");
                if alpha > WIRE_REST {
                    *lit = true;
                }
            }
        }
        assert!(ever_lit.iter().all(|lit| *lit), "a cell saw no traffic");
    }

    #[test]
    fn a_steady_wire_never_corrupts_or_drops() {
        let period = CASCADE.as_secs_f32();
        for step in 0..40 {
            let t = step as f32 / 40.0 * period;
            for column in 0..30usize {
                let (alpha, corrupted) = wire_cell(column, 30, Some(t), false);
                assert!(!corrupted);
                assert!(alpha >= WIRE_REST - f32::EPSILON, "{alpha}");
            }
        }
    }

    #[test]
    fn a_glitching_wire_corrupts_without_dying() {
        let columns = 60usize;
        let mut corrupted = 0;
        let mut dead_air = 0;
        let mut cells = 0;
        for step in 0..30 {
            let t = step as f32 / 30.0 * CASCADE.as_secs_f32();
            for column in 0..columns {
                let (alpha, flash) = wire_cell(column, columns, Some(t), true);
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
    fn tears_sweep_the_whole_wire_over_their_period() {
        let columns = 60usize;
        let mut torn = vec![false; columns];
        for step in 0..60 {
            let t = step as f32 / 60.0 * TEAR_PERIOD.as_secs_f32();
            for (centre, half_width) in tears(t, columns) {
                let first = (centre - half_width).floor().max(0.0) as usize;
                let last = ((centre + half_width).ceil() as usize).min(columns - 1);
                for torn_cell in &mut torn[first..=last] {
                    *torn_cell = true;
                }
            }
        }
        assert!(torn.iter().all(|torn| *torn), "a cell was never torn");
    }

    #[test]
    fn corruption_concentrates_where_the_tears_are() {
        let columns = 60usize;
        let t0 = 0.4 * TEAR_PERIOD.as_secs_f32();
        let mut torn_zone = (0, 0);
        let mut calm_zone = (0, 0);
        for step in 0..10 {
            let t = t0 + step as f32 * 0.02;
            let fronts = tears(t, columns);
            for column in 0..columns {
                let (alpha, flash) = wire_cell(column, columns, Some(t), true);
                let hit = flash || alpha == 0.0;
                if fronts
                    .iter()
                    .any(|&(centre, half)| (column as f32 - centre).abs() <= half)
                {
                    torn_zone.0 += u32::from(hit);
                    torn_zone.1 += 1;
                } else {
                    calm_zone.0 += u32::from(hit);
                    calm_zone.1 += 1;
                }
            }
        }
        let torn_density = torn_zone.0 as f32 / torn_zone.1 as f32;
        let calm_density = calm_zone.0 as f32 / calm_zone.1 as f32;
        assert!(
            torn_density > calm_density * 2.0,
            "tear density {torn_density:.2} vs calm {calm_density:.2}"
        );
    }

    #[test]
    fn wake_traces_stay_pixel_snapped_and_quiet() {
        for step in 0..40 {
            let t = step as f32 * 0.06;
            for column in (0..30).step_by(3) {
                for (offset, alpha, corrupted) in wake_cells(column, 30, Some(t), false) {
                    assert_eq!(offset % 2.0, 0.0, "{offset} is not on the grid");
                    assert!(!corrupted);
                    assert!(alpha <= WAKE_REST + f32::EPSILON, "{alpha}");
                }
            }
        }
    }
}
