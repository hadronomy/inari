//! One vocabulary for every state the Device Center shows.
//!
//! Devices, jobs, the agent service, and the connection all resolve to the
//! same small [`Tone`] set and render through the same components. That is the
//! point: an operator learns "amber ring with a label means act soon" once, and
//! it holds on every screen.
//!
//! Tone is never the only carrier. Each status has a label, and each tone has
//! its own glyph, so the interface still reads under Differentiate Without
//! Color or in a grayscale screenshot pasted into a ticket.

use gpui::{Hsla, IntoElement, ParentElement as _, RenderOnce, SharedString, Styled, div, px};
use gpui_component::{Icon, IconName, StyledExt as _};
use inari_agent_client::{AgentConnection, DeviceState, JobState, ServiceState};

use super::{
    icon::Symbol,
    theme::{ActiveTheme as _, Theme},
};

/// What a state means for the operator, not what color it is.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Tone {
    /// Healthy and needs nothing.
    Positive,
    /// Work is under way. Transient by definition.
    Busy,
    /// Nothing is wrong; nothing is happening either.
    Neutral,
    /// Act soon, but device work continues.
    Caution,
    /// Device work is blocked until someone acts.
    Critical,
}

impl Tone {
    pub fn color(self, theme: &Theme) -> Hsla {
        match self {
            Self::Positive => theme.success,
            Self::Busy => theme.info,
            Self::Neutral => theme.text_tertiary,
            Self::Caution => theme.warning,
            Self::Critical => theme.danger,
        }
    }

    pub fn wash(self, theme: &Theme) -> Hsla {
        match self {
            Self::Positive => theme.success_wash,
            Self::Busy => theme.info_wash,
            Self::Neutral => theme.surface_raised,
            Self::Caution => theme.warning_wash,
            Self::Critical => theme.danger_wash,
        }
    }

    /// The glyph that carries this tone when color cannot.
    pub fn symbol(self) -> IconName {
        match self {
            Self::Positive => IconName::CircleCheck,
            Self::Busy => IconName::LoaderCircle,
            Self::Neutral => IconName::Minus,
            Self::Caution => IconName::TriangleAlert,
            Self::Critical => IconName::CircleX,
        }
    }
}

/// A resolved state: what to call it, how it reads, and why.
#[derive(Clone, Debug)]
pub struct Status {
    pub tone: Tone,
    pub label: SharedString,
    /// One sentence on what this means or what to do. Kept beside the label so
    /// a state can never appear somewhere without its explanation available.
    pub detail: SharedString,
}

impl Status {
    fn new(tone: Tone, label: &'static str, detail: &'static str) -> Self {
        Self { tone, label: label.into(), detail: detail.into() }
    }

    pub fn device(state: DeviceState) -> Self {
        match state {
            DeviceState::Online => {
                Self::new(Tone::Positive, "Online", "The agent can reach this device.")
            },
            DeviceState::Offline => Self::new(
                Tone::Neutral,
                "Offline",
                "The agent cannot reach this device. Check power and cabling.",
            ),
            DeviceState::Degraded => Self::new(
                Tone::Caution,
                "Needs attention",
                "The device responds, but not correctly. Jobs may fail.",
            ),
            DeviceState::Blocked => Self::new(
                Tone::Critical,
                "Blocked",
                "The device refuses work. An administrator must clear it.",
            ),
            DeviceState::Unknown => Self::new(Tone::Busy, "Checking", "Reading the device state."),
        }
    }

    pub fn job(state: JobState) -> Self {
        match state {
            JobState::Queued => Self::new(Tone::Neutral, "Queued", "Waiting for a device."),
            JobState::Running => Self::new(Tone::Busy, "Running", "The device is doing the work."),
            JobState::Succeeded => Self::new(Tone::Positive, "Completed", "The work finished."),
            JobState::Failed => {
                Self::new(Tone::Critical, "Failed", "The work stopped before it finished.")
            },
            JobState::Cancelled => Self::new(Tone::Neutral, "Cancelled", "Someone stopped this."),
            JobState::Unknown => {
                Self::new(Tone::Caution, "State unavailable", "The agent did not report a state.")
            },
        }
    }

    /// The agent service state, refined by whether live updates actually flow.
    ///
    /// A running service with a dead connection is the state operators hit
    /// most and the one a plain service check reports as healthy, so the two
    /// signals resolve together rather than in two separate places.
    pub fn service(state: ServiceState, connection: AgentConnection) -> Self {
        match state {
            ServiceState::Checking => {
                Self::new(Tone::Busy, "Checking", "Reading the service state.")
            },
            ServiceState::Starting => {
                Self::new(Tone::Busy, "Starting", "Waiting for the service to come up.")
            },
            ServiceState::Running => match connection {
                AgentConnection::Connected => {
                    Self::new(Tone::Positive, "Running", "Live updates are flowing.")
                },
                AgentConnection::Checking => {
                    Self::new(Tone::Busy, "Running", "Opening the local connection.")
                },
                AgentConnection::Reconnecting => {
                    Self::new(Tone::Caution, "Reconnecting", "The local connection dropped.")
                },
                AgentConnection::Unavailable => Self::new(
                    Tone::Critical,
                    "Not responding",
                    "The service runs but does not answer. Restart it in Support.",
                ),
            },
            ServiceState::Stopped => {
                Self::new(Tone::Critical, "Stopped", "Start the service to restore device work.")
            },
            ServiceState::NotInstalled => Self::new(
                Tone::Critical,
                "Not installed",
                "Repair the Inari installation to restore the service.",
            ),
            ServiceState::Unavailable => Self::new(
                Tone::Critical,
                "Unavailable",
                "Device Center cannot read the service state.",
            ),
        }
    }
}

/// A small filled disc. Pairs with an adjacent label; never the only signal.
#[derive(IntoElement)]
pub struct StatusDot {
    tone: Tone,
    size: f32,
}

impl StatusDot {
    pub fn new(tone: Tone) -> Self {
        Self { tone, size: 8.0 }
    }

    pub fn size(mut self, size: f32) -> Self {
        self.size = size;
        self
    }
}

impl RenderOnce for StatusDot {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let color = self.tone.color(theme);
        div()
            .size(px(self.size))
            .rounded_full()
            .flex_none()
            .bg(color)
            // A tinted ring at the dot's own hue separates it from whatever
            // sits behind it without drawing a grey outline around a colour.
            .border(px(3.0))
            .border_color(Hsla { a: 0.18, ..color })
    }
}

/// A status as a compact chip: glyph, label, tonal wash.
#[derive(IntoElement)]
pub struct StatusChip {
    status: Status,
}

impl StatusChip {
    pub fn new(status: Status) -> Self {
        Self { status }
    }
}

impl RenderOnce for StatusChip {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let tone = self.status.tone;
        let color = tone.color(theme);
        div()
            .h_flex()
            .flex_none()
            .items_center()
            .gap(px(Theme::SPACE_XS))
            .h(px(24.0))
            .px(px(Theme::SPACE_SM))
            .rounded_full()
            .bg(tone.wash(theme))
            .border_1()
            .border_color(Hsla { a: 0.22, ..color })
            .child(
                Icon::from(Symbol::Component(tone.symbol()))
                    .size(px(13.0))
                    .text_color(color),
            )
            .child(
                div()
                    .text_size(px(12.0))
                    // Pin the line box to the glyphs: a default 1.5em line
                    // box is taller than the text, and centring that box
                    // against the icon leaves the label reading low.
                    .line_height(px(16.0))
                    .font_weight(gpui::FontWeight::MEDIUM)
                    .text_color(color)
                    .child(self.status.label),
            )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_running_service_without_a_connection_is_not_reported_healthy() {
        let status = Status::service(ServiceState::Running, AgentConnection::Unavailable);

        assert_eq!(status.tone, Tone::Critical);
        assert_eq!(status.label, "Not responding");
    }

    #[test]
    fn a_running_connected_service_is_the_only_positive_service_state() {
        for state in [
            ServiceState::Checking,
            ServiceState::Starting,
            ServiceState::Stopped,
            ServiceState::NotInstalled,
            ServiceState::Unavailable,
        ] {
            assert_ne!(Status::service(state, AgentConnection::Connected).tone, Tone::Positive);
        }

        assert_eq!(
            Status::service(ServiceState::Running, AgentConnection::Connected).tone,
            Tone::Positive
        );
    }

    /// Colour is never the only carrier of a tone, so the glyphs must differ
    /// too. This is what keeps the interface readable under Differentiate
    /// Without Color and in a grayscale screenshot.
    #[test]
    fn every_tone_carries_a_distinct_glyph() {
        let tones = [Tone::Positive, Tone::Busy, Tone::Neutral, Tone::Caution, Tone::Critical];
        for (index, tone) in tones.iter().enumerate() {
            for other in &tones[index + 1..] {
                assert_ne!(
                    gpui_component::IconNamed::path(tone.symbol()),
                    gpui_component::IconNamed::path(other.symbol())
                );
            }
        }
    }
}
