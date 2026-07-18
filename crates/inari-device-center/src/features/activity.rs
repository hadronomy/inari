use std::cmp::Reverse;

use chrono::{DateTime, Utc};
use gpui::{
    InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce, SharedString, Styled,
    div, prelude::FluentBuilder as _, rems,
};
use gpui_component::StyledExt as _;
use inari_agent_client::{AgentEvent, EventResource, Job, JobState};

use crate::ui::{PageHeader, SectionCard, palette};

#[derive(IntoElement)]
pub struct ActivityView {
    jobs: Vec<Job>,
    events: Vec<AgentEvent>,
}

impl ActivityView {
    pub fn new(jobs: &[Job], events: &[AgentEvent]) -> Self {
        Self { jobs: jobs.to_vec(), events: events.to_vec() }
    }
}

impl RenderOnce for ActivityView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        let mut items = activity_items(self.jobs, self.events, colors);
        items.sort_unstable_by_key(|item| Reverse(item.occurred_at));

        div()
            .v_flex()
            .gap(rems(1.35))
            .max_w(rems(78.))
            .mx_auto()
            .px(rems(2.5))
            .py(rems(2.25))
            .child(PageHeader::new(
                "Activity",
                "Durable jobs and device events, ordered by what happened.",
            ))
            .when(items.is_empty(), |page| {
                page.child(SectionCard::new(
                    "Operational timeline",
                    "No activity has been recorded.",
                    "New device work and connection changes will appear here.",
                ))
            })
            .when(!items.is_empty(), |page| {
                page.child(
                    div()
                        .id("activity-timeline")
                        .v_flex()
                        .rounded(rems(1.))
                        .border_1()
                        .border_color(colors.border)
                        .overflow_hidden()
                        .children(
                            items
                                .into_iter()
                                .take(100)
                                .enumerate()
                                .map(|(index, item)| activity_row(item, index, colors)),
                        ),
                )
            })
    }
}

struct ActivityItem {
    occurred_at: DateTime<Utc>,
    title: SharedString,
    detail: SharedString,
    color: gpui::Hsla,
}

fn activity_items(
    jobs: Vec<Job>,
    events: Vec<AgentEvent>,
    colors: palette::Palette,
) -> Vec<ActivityItem> {
    let mut items = Vec::with_capacity(jobs.len() + events.len());
    items.extend(events.into_iter().map(|event| {
        let resource = match event.resource {
            EventResource::Device(id) => format!("Device {id}"),
            EventResource::Job(id) => format!("Job {id}"),
        };
        ActivityItem {
            occurred_at: event.occurred_at,
            title: event.summary.into(),
            detail: resource.into(),
            color: colors.blue,
        }
    }));
    items.extend(
        jobs.into_iter()
            .map(|job| ActivityItem {
                occurred_at: job.created_at,
                title: job_state(job.state).into(),
                detail: format!("Job {} · device {}", job.id, job.device_id).into(),
                color: job_color(job.state, colors),
            }),
    );
    items
}

fn activity_row(
    item: ActivityItem,
    index: usize,
    colors: palette::Palette,
) -> gpui::Stateful<gpui::Div> {
    div()
        .id(("activity", index))
        .flex()
        .items_start()
        .gap(rems(1.))
        .px(rems(1.15))
        .py(rems(0.95))
        .when(index > 0, |row| {
            row.border_t_1()
                .border_color(colors.border)
        })
        .child(
            div()
                .w(rems(0.22))
                .h(rems(2.2))
                .rounded(rems(0.12))
                .bg(item.color)
                .flex_shrink_0(),
        )
        .child(
            div()
                .v_flex()
                .gap(rems(0.2))
                .child(
                    div()
                        .font_weight(gpui::FontWeight::MEDIUM)
                        .child(item.title),
                )
                .child(
                    div()
                        .text_size(rems(0.76))
                        .text_color(colors.text_muted)
                        .child(item.detail),
                ),
        )
        .child(
            div()
                .ml_auto()
                .text_size(rems(0.72))
                .text_color(colors.text_muted)
                .child(
                    item.occurred_at
                        .format("%Y-%m-%d %H:%M UTC")
                        .to_string(),
                ),
        )
}

fn job_state(state: JobState) -> &'static str {
    match state {
        JobState::Queued => "Job queued",
        JobState::Running => "Job in progress",
        JobState::Succeeded => "Job completed",
        JobState::Failed => "Job failed",
        JobState::Cancelled => "Job cancelled",
        JobState::Unknown => "Job state unavailable",
    }
}

fn job_color(state: JobState, colors: palette::Palette) -> gpui::Hsla {
    match state {
        JobState::Succeeded => colors.green,
        JobState::Failed => colors.vermilion,
        JobState::Queued | JobState::Running => colors.blue,
        JobState::Cancelled | JobState::Unknown => colors.text_muted,
    }
}
