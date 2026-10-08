//! Activity: what the agent and its devices did, most recent first.
//!
//! Times are shown twice — relative for scanning ("4 min ago") and absolute on
//! the same row for the ticket someone is about to file. Absolute times use
//! this computer's zone and name it, so a log pasted into a chat is not
//! ambiguous about which clock it came from.

use std::cmp::Reverse;

use chrono::{DateTime, Local, Utc};
use gpui::{
    InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce, SharedString, Styled,
    div, prelude::FluentBuilder as _, px,
};
use gpui_component::{IconName, StyledExt as _};
use inari_agent_client::{
    AgentEvent, Device, EventResource, Job, OutputEvidence, PrintJob, PrintJobState,
    PrintOriginKind,
};

use crate::ui::{
    content::{EmptyState, PageTitle, Section, Typography as _, page, row_divider},
    icon::Symbol,
    status::{Status, StatusDot, Tone},
    surface::list_card,
    theme::{ActiveTheme as _, Theme},
};

/// The newest entries worth keeping on screen. Beyond this the page stops
/// being a review surface and becomes a log file, which is what Open local
/// logs in Support is for.
const VISIBLE_ENTRIES: usize = 100;

#[derive(IntoElement)]
pub struct ActivityView {
    jobs: Vec<Job>,
    print_jobs: Vec<PrintJob>,
    events: Vec<AgentEvent>,
    devices: Vec<Device>,
}

impl ActivityView {
    pub fn new(
        jobs: &[Job],
        print_jobs: &[PrintJob],
        events: &[AgentEvent],
        devices: &[Device],
    ) -> Self {
        Self {
            jobs: jobs.to_vec(),
            print_jobs: print_jobs.to_vec(),
            events: events.to_vec(),
            devices: devices.to_vec(),
        }
    }
}

impl RenderOnce for ActivityView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let now = Utc::now();
        let mut entries = entries(self.jobs, self.print_jobs, self.events, &self.devices);
        entries.sort_unstable_by_key(|entry| Reverse(entry.occurred_at));
        entries.truncate(VISIBLE_ENTRIES);

        page("activity")
            .child(PageTitle::new("Activity", "Recent work and device events, newest first."))
            .child(Section::new("Recent").child(if entries.is_empty() {
                EmptyState::new(
                    Symbol::Component(IconName::Calendar),
                    "No activity yet",
                    "Jobs and device events appear here as the agent reports them.",
                )
                .into_any_element()
            } else {
                list_card(theme)
                    .id("activity-timeline")
                    .w_full()
                    .children(
                        entries
                            .into_iter()
                            .enumerate()
                            .map(|(index, entry)| {
                                div()
                                    .v_flex()
                                    .w_full()
                                    .when(index > 0, |row| row.child(row_divider(theme)))
                                    .child(entry_row(entry, now, theme))
                            }),
                    )
                    .into_any_element()
            }))
    }
}

struct Entry {
    occurred_at: DateTime<Utc>,
    title: SharedString,
    detail: SharedString,
    tone: Tone,
}

fn entries(
    jobs: Vec<Job>,
    print_jobs: Vec<PrintJob>,
    events: Vec<AgentEvent>,
    devices: &[Device],
) -> Vec<Entry> {
    let mut entries = Vec::with_capacity(jobs.len() + print_jobs.len() + events.len());
    entries.extend(print_jobs.into_iter().map(|job| {
        let (label, tone) = match job.state {
            PrintJobState::Accepted => ("Accepted", Tone::Busy),
            PrintJobState::InProgress => ("In Progress", Tone::Busy),
            PrintJobState::OutputConfirmed(_) => ("Output Confirmed", Tone::Positive),
            PrintJobState::Failed => ("Failed", Tone::Critical),
            PrintJobState::OutcomeUnknown => ("Outcome Unknown", Tone::Caution),
            PrintJobState::Expired => ("Expired", Tone::Neutral),
            PrintJobState::Canceled => ("Canceled", Tone::Neutral),
        };
        let kind = match job.origin_kind {
            PrintOriginKind::Pos if job.document_kind.as_deref() == Some("customer_receipt") => {
                "POS receipt"
            },
            PrintOriginKind::Pos => "POS document",
            PrintOriginKind::Preparation => "Preparation ticket",
            PrintOriginKind::Report => "Report",
        };
        let device = devices
            .iter()
            .find(|device| device.id == job.device_id)
            .map(|device| device.name.as_str())
            .unwrap_or(job.device_id.as_str());
        let source = match &job.pos_configuration_id {
            Some(id) => format!("Odoo {} · POS {id}", job.database),
            None => format!("Odoo {}", job.database),
        };
        let evidence = match job.state {
            PrintJobState::OutputConfirmed(OutputEvidence::Device) => " · Device evidence",
            PrintJobState::OutputConfirmed(OutputEvidence::Spooler) => " · Spooler evidence",
            PrintJobState::OutputConfirmed(OutputEvidence::Transport) => " · Transport evidence",
            PrintJobState::OutcomeUnknown => " · Check the output before a Reprint",
            _ => "",
        };
        Entry {
            occurred_at: job.occurred_at,
            title: format!("{kind} · {label}").into(),
            detail: format!("{device} · {source}{evidence} · Print Job {}", job.id).into(),
            tone,
        }
    }));
    entries.extend(events.into_iter().map(|event| {
        Entry {
            occurred_at: event.occurred_at,
            title: event.summary.into(),
            detail: match event.resource {
                EventResource::Device(id) => format!("Device {id}"),
                EventResource::Job(id) => format!("Job {id}"),
            }
            .into(),
            tone: Tone::Busy,
        }
    }));
    entries.extend(jobs.into_iter().map(|job| {
        let status = Status::job(job.state);
        Entry {
            occurred_at: job.created_at,
            title: status.label,
            detail: format!("Job {} · device {}", job.id, job.device_id).into(),
            tone: status.tone,
        }
    }));
    entries
}

fn entry_row(entry: Entry, now: DateTime<Utc>, theme: &Theme) -> impl IntoElement {
    let local = entry.occurred_at.with_timezone(&Local);
    div()
        .h_flex()
        .items_start()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .px(px(Theme::SPACE_MD + 2.0))
        .py(px(Theme::SPACE_MD))
        .child(
            div()
                .v_flex()
                .flex_1()
                .min_w(px(0.0))
                .gap(px(1.0))
                .child(
                    // Dot and title share one centred row, so the dot's
                    // centre meets the title line's centre by flexbox —
                    // no nudges to drift apart later.
                    div()
                        .h_flex()
                        .items_center()
                        .gap(px(Theme::SPACE_SM))
                        .child(StatusDot::new(entry.tone).size(7.0))
                        .child(
                            div()
                                .text_body()
                                .font_weight(gpui::FontWeight::MEDIUM)
                                .child(entry.title),
                        ),
                )
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_secondary)
                        // Aligned under the title text, not under the dot:
                        // the dot's box (7px + its 3px ring) plus the row gap.
                        .pl(px(13.0 + Theme::SPACE_SM))
                        .child(entry.detail),
                ),
        )
        .child(
            div()
                .v_flex()
                .items_end()
                .flex_none()
                .mt(px(1.5))
                .gap(px(1.0))
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_secondary)
                        .child(relative_time(entry.occurred_at, now)),
                )
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_tertiary)
                        .child(local.format("%H:%M %Z").to_string()),
                ),
        )
}

/// A short, coarse "how long ago". Coarse on purpose: a timeline that ticks
/// every second turns a review surface into something that moves while you
/// read it.
fn relative_time(occurred_at: DateTime<Utc>, now: DateTime<Utc>) -> String {
    let seconds = (now - occurred_at).num_seconds();
    if seconds < 0 {
        return "just now".into();
    }
    let minutes = seconds / 60;
    let hours = minutes / 60;
    let days = hours / 24;
    match (days, hours, minutes) {
        (0, 0, 0) => "just now".into(),
        (0, 0, minutes) => format!("{minutes} min ago"),
        (0, hours, _) => format!("{hours} h ago"),
        (days, _, _) if days < 7 => format!("{days} d ago"),
        _ => occurred_at
            .with_timezone(&Local)
            .format("%Y-%m-%d")
            .to_string(),
    }
}

#[cfg(test)]
mod tests {
    use chrono::TimeZone as _;
    use inari_agent_client::JobState;

    use super::*;

    fn at(hour: u32, minute: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 8, 24, hour, minute, 0)
            .unwrap()
    }

    #[test]
    fn relative_time_steps_from_minutes_to_hours_to_days() {
        let now = at(12, 0);

        assert_eq!(relative_time(at(12, 0), now), "just now");
        assert_eq!(relative_time(at(11, 45), now), "15 min ago");
        assert_eq!(relative_time(at(9, 0), now), "3 h ago");
    }

    #[test]
    fn a_clock_skewed_future_entry_does_not_render_a_negative_age() {
        assert_eq!(relative_time(at(13, 0), at(12, 0)), "just now");
    }

    #[test]
    fn failed_work_carries_the_critical_tone_into_the_timeline() {
        let job = Job {
            id: inari_agent_client::JobId::parse("job_one").unwrap(),
            device_id: inari_agent_client::DeviceId::parse("dev_one").unwrap(),
            state: JobState::Failed,
            created_at: at(12, 0),
        };

        let entries = entries(vec![job], Vec::new(), Vec::new(), &[]);

        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].tone, Tone::Critical);
    }

    #[test]
    fn receipt_history_names_the_odoo_source_and_preserves_evidence_and_risk() {
        let mut job = PrintJob {
            id: inari_agent_client::JobId::parse("job_receipt").unwrap(),
            intent_id: "pi_v1_receipt".into(),
            device_id: inari_agent_client::DeviceId::parse("dev_pos80").unwrap(),
            origin_kind: PrintOriginKind::Pos,
            database: "odoo".into(),
            document_kind: Some("customer_receipt".into()),
            pos_configuration_id: Some("4".into()),
            state: PrintJobState::OutputConfirmed(OutputEvidence::Spooler),
            state_version: 3,
            occurred_at: at(12, 0),
        };
        let devices = [Device {
            id: job.device_id.clone(),
            name: "POS-80".into(),
            kind: inari_agent_client::DeviceKind::Printer,
            state: inari_agent_client::DeviceState::Online,
        }];
        let history = entries(Vec::new(), vec![job.clone()], Vec::new(), &devices);
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].title, "POS receipt · Output Confirmed");
        assert_eq!(history[0].tone, Tone::Positive);
        assert!(
            history[0]
                .detail
                .contains("POS-80 · Odoo odoo · POS 4 · Spooler evidence")
        );
        assert!(
            history[0]
                .detail
                .contains("Print Job job_receipt")
        );
        job.state = PrintJobState::OutcomeUnknown;
        let history = entries(Vec::new(), vec![job], Vec::new(), &devices);
        assert_eq!(history[0].tone, Tone::Caution);
        assert_eq!(history[0].title, "POS receipt · Outcome Unknown");
        assert!(
            history[0]
                .detail
                .contains("Check the output before a Reprint")
        );
    }
}
