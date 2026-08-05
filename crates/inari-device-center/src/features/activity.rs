use std::cmp::Reverse;

use chrono::{DateTime, Local, Utc};
use gpui::{
    InteractiveElement as _, IntoElement, ParentElement as _, RenderOnce, SharedString, Styled,
    div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{Icon, IconName, StyledExt as _};
use inari_agent_client::{AgentEvent, EventResource, Job, JobState};

use crate::ui::{PageHeader, SectionCard, page, palette};

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

        page()
            .child(PageHeader::new(
                "Activity",
                "Review recent jobs and device events. Times use this computer's time zone.",
            ))
            .when(items.is_empty(), |view| {
                view.child(SectionCard::new(
                    "Recent activity",
                    "No activity recorded",
                    "New jobs and device events appear here.",
                ))
            })
            .when(!items.is_empty(), |view| {
                view.child(
                    div()
                        .id("activity-timeline")
                        .v_flex()
                        .rounded(rems(0.5))
                        .bg(colors.surface)
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
    icon: IconName,
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
            color: colors.info,
            icon: IconName::Info,
        }
    }));
    items.extend(jobs.into_iter().map(|job| {
        let (color, icon) = job_treatment(job.state, colors);
        ActivityItem {
            occurred_at: job.created_at,
            title: job_state(job.state).into(),
            detail: format!("Job {} · device {}", job.id, job.device_id).into(),
            color,
            icon,
        }
    }));
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
        .flex_wrap()
        .items_start()
        .gap(rems(0.75))
        .px(rems(1.))
        .py(rems(0.75))
        .when(index > 0, |row| {
            row.border_t_1()
                .border_color(colors.separator)
        })
        .child(
            Icon::new(item.icon)
                .size(rems(1.))
                .text_color(item.color),
        )
        .child(
            div()
                .min_w(rems(13.))
                .flex_1()
                .v_flex()
                .gap(rems(0.25))
                .child(
                    div()
                        .font_weight(gpui::FontWeight::SEMIBOLD)
                        .child(item.title),
                )
                .child(
                    div()
                        .text_size(rems(0.75))
                        .text_color(colors.text_muted)
                        .child(item.detail),
                ),
        )
        .child(
            div()
                .text_size(rems(0.75))
                .text_color(colors.text_muted)
                .child(
                    item.occurred_at
                        .with_timezone(&Local)
                        .format("%Y-%m-%d %H:%M %Z")
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

fn job_treatment(state: JobState, colors: palette::Palette) -> (gpui::Hsla, IconName) {
    match state {
        JobState::Succeeded => (colors.success, IconName::CircleCheck),
        JobState::Failed => (colors.danger, IconName::CircleX),
        JobState::Queued => (colors.info, IconName::Calendar),
        JobState::Running => (colors.info, IconName::LoaderCircle),
        JobState::Cancelled => (colors.text_muted, IconName::Close),
        JobState::Unknown => (colors.warning, IconName::TriangleAlert),
    }
}
