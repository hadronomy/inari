//! Overview answers one question: is anything wrong, and where?
//!
//! The gate carries the system-level answer. Below it, attention items are
//! listed individually rather than counted, because "3 items need review" makes
//! an operator open another screen to learn what a list could have said here.

use gpui::{
    InteractiveElement as _, IntoElement, KeyDownEvent, ParentElement as _, RenderOnce,
    SharedString, StatefulInteractiveElement as _, Styled, WeakEntity, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::{Icon, IconName, StyledExt as _};
use inari_agent_client::{Device, DeviceState, Job, JobState};

use crate::{
    app::{DeviceCenter, device_health},
    ui::{
        banner::Banner,
        chrome::is_activation,
        content::{EmptyState, PageTitle, Section, Typography as _, page, row_divider},
        focus,
        gate::Gate,
        icon::Symbol,
        motion,
        status::{Status, StatusChip, Tone},
        surface::list_card,
        theme::{ActiveTheme as _, Theme},
    },
};

/// One thing that needs a person.
struct Attention {
    symbol: Symbol,
    title: SharedString,
    status: Status,
    /// Where selecting this item takes the operator.
    target: Target,
}

/// The screen that can act on an attention item.
#[derive(Clone)]
enum Target {
    Device(inari_agent_client::DeviceId),
    Work,
}

#[derive(IntoElement)]
pub struct OverviewView {
    devices: Vec<Device>,
    jobs: Vec<Job>,
    agent: Status,
    agent_guidance: Option<String>,
    center: WeakEntity<DeviceCenter>,
}

impl OverviewView {
    pub fn new(
        devices: &[Device],
        jobs: &[Job],
        agent: Status,
        agent_guidance: Option<String>,
        center: WeakEntity<DeviceCenter>,
    ) -> Self {
        Self { devices: devices.to_vec(), jobs: jobs.to_vec(), agent, agent_guidance, center }
    }
}

impl RenderOnce for OverviewView {
    fn render(self, _: &mut gpui::Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        let agent_reachable = self.agent_guidance.is_none();
        let health = device_health(&self.devices, agent_reachable);
        let attention = attention_items(&self.devices, &self.jobs, agent_reachable);
        let active_jobs = self
            .jobs
            .iter()
            .filter(|job| matches!(job.state, JobState::Queued | JobState::Running))
            .count();

        page("overview")
            .child(PageTitle::new(
                "Overview",
                "The state of this computer's agent and the devices it operates.",
            ))
            .when_some(self.agent_guidance.clone(), |view, guidance| {
                view.child(Banner::new(
                    "agent-guidance",
                    Tone::Critical,
                    "Agent unreachable",
                    guidance,
                ))
            })
            .child(Gate::new(self.agent, health))
            .child(
                // Empty and populated states share the card, so the section
                // keeps one shape as devices come and go instead of the page
                // reflowing every time the last issue clears.
                Section::new("Needs attention")
                    .aside(attention_summary(attention.len(), agent_reachable))
                    .child(
                        list_card(theme)
                            .w_full()
                            .child(if !agent_reachable {
                                EmptyState::new(
                            Symbol::Component(IconName::Info),
                            "Device state is unavailable",
                            "Restore the agent connection before reviewing devices and work.",
                        )
                        .into_any_element()
                            } else if attention.is_empty() {
                                EmptyState::new(
                                    Symbol::Component(IconName::CircleCheck),
                                    "Nothing needs attention",
                                    "No device issues and no failed work.",
                                )
                                .into_any_element()
                            } else {
                                let attention_count = attention.len();
                                div()
                                    .v_flex()
                                    .w_full()
                                    .children(attention.into_iter().enumerate().map(
                                        |(index, item)| {
                                            div()
                                                .v_flex()
                                                .w_full()
                                                .when(index > 0, |row| {
                                                    row.child(row_divider(theme))
                                                })
                                                .child(
                                                    attention_row(index, item, &self.center, theme)
                                                        // Same corner law as the device
                                                        // rows: the wash is full-bleed,
                                                        // the mask is rectangular, so the
                                                        // end rows carry the card's curve.
                                                        .when(index == 0, |row| {
                                                            row.rounded_t(px(Theme::RADIUS_CARD))
                                                        })
                                                        .when(
                                                            index == attention_count - 1,
                                                            |row| {
                                                                row.rounded_b(px(
                                                                    Theme::RADIUS_CARD,
                                                                ))
                                                            },
                                                        ),
                                                )
                                        },
                                    ))
                                    .into_any_element()
                            }),
                    ),
            )
            .when(agent_reachable, |view| {
                view.child(
                    Section::new("Work").child(
                        div()
                            .text_body()
                            .text_color(theme.text_secondary)
                            .child(match active_jobs {
                                0 => "No work is queued or running.".to_string(),
                                1 => "1 job is queued or running.".to_string(),
                                count => format!("{count} jobs are queued or running."),
                            }),
                    ),
                )
            })
    }
}

fn attention_summary(count: usize, agent_reachable: bool) -> impl IntoElement {
    let status = if !agent_reachable {
        Status::device(DeviceState::Unknown)
    } else if count == 0 {
        Status::device(DeviceState::Online)
    } else {
        Status::job(JobState::Failed)
    };
    div().when(agent_reachable, |aside| {
        aside.child(StatusChip::new(Status {
            tone: if count == 0 { Tone::Positive } else { Tone::Caution },
            label: match count {
                0 => "All clear".into(),
                1 => "1 item".into(),
                count => format!("{count} items").into(),
            },
            detail: status.detail,
        }))
    })
}

/// An attention item, as a control.
///
/// Each row opens the screen that can act on it. Reading a problem and then
/// having to find the same device again by hand is the difference between a
/// dashboard and a tool.
fn attention_row(
    index: usize,
    item: Attention,
    center: &WeakEntity<DeviceCenter>,
    theme: &Theme,
) -> gpui::Stateful<gpui::Div> {
    let click_target = item.target.clone();
    let key_target = item.target;
    let click_center = center.clone();
    let key_center = center.clone();
    let fade_key = SharedString::from(format!("attention-{index}"));
    div()
        .id(("attention", index))
        .h_flex()
        .items_center()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .px(px(Theme::SPACE_MD + 2.0))
        .py(px(Theme::SPACE_MD))
        .cursor_pointer()
        .focusable()
        .tab_stop(true)
        .border_1()
        .border_color(gpui::transparent_black())
        .when(focus::visible(), |row| row.focus(|style| style.border_color(theme.focus_ring)))
        .on_hover({
            let fade_key = fade_key.clone();
            move |hovered, window, _| {
                if motion::hover_set(fade_key.clone(), *hovered) {
                    // Refresh: request_animation_frame panics outside paint
                    // (see hover_set).
                    window.refresh();
                }
            }
        })
        .bg(motion::hover_blend(fade_key, theme.wash_hover))
        .active(|row| row.bg(theme.wash_pressed))
        .on_click(move |_, _, cx| open(&click_center, &click_target, cx))
        .on_key_down(move |event: &KeyDownEvent, _, cx| {
            if is_activation(event) {
                open(&key_center, &key_target, cx);
                cx.stop_propagation();
            }
        })
        .child(
            Icon::from(item.symbol)
                .size(px(16.0))
                .flex_none()
                .text_color(theme.text_secondary),
        )
        .child(
            div()
                .v_flex()
                .flex_1()
                .min_w(px(0.0))
                .gap(px(1.0))
                .child(
                    div()
                        .text_body()
                        .font_weight(gpui::FontWeight::MEDIUM)
                        .child(item.title),
                )
                .child(
                    div()
                        .text_caption()
                        .text_color(theme.text_secondary)
                        .child(item.status.detail.clone()),
                ),
        )
        .child(StatusChip::new(item.status))
        .child(
            Icon::from(Symbol::Component(IconName::ChevronRight))
                .size(px(14.0))
                .flex_none()
                .text_color(theme.text_tertiary),
        )
}

fn open(center: &WeakEntity<DeviceCenter>, target: &Target, cx: &mut gpui::App) {
    let target = target.clone();
    center
        .update(cx, |center, cx| match target {
            Target::Device(id) => center.show_device(id, cx),
            Target::Work => center.show_work(cx),
        })
        .ok();
}

/// Devices that are not healthy, then work that failed. Devices come first:
/// a failed job is usually a symptom of the device below it, so fixing the
/// device is what clears both.
fn attention_items(devices: &[Device], jobs: &[Job], agent_reachable: bool) -> Vec<Attention> {
    if !agent_reachable {
        return Vec::new();
    }
    let mut items: Vec<Attention> = devices
        .iter()
        .filter(|device| {
            matches!(
                device.state,
                DeviceState::Offline | DeviceState::Degraded | DeviceState::Blocked
            )
        })
        .map(|device| Attention {
            symbol: crate::features::devices::device_symbol(device.kind),
            title: device.name.clone().into(),
            status: Status::device(device.state),
            target: Target::Device(device.id.clone()),
        })
        .collect();
    items.extend(
        jobs.iter()
            .filter(|job| job.state == JobState::Failed)
            .map(|job| Attention {
                symbol: Symbol::Component(IconName::TriangleAlert),
                title: format!("Job {} on device {}", job.id, job.device_id).into(),
                status: Status::job(job.state),
                target: Target::Work,
            }),
    );
    items
}

#[cfg(test)]
mod tests {
    use chrono::Utc;
    use inari_agent_client::{DeviceId, DeviceKind, JobId};

    use super::*;

    fn device(name: &str, state: DeviceState) -> Device {
        Device {
            id: DeviceId::parse("dev_one").unwrap(),
            name: name.into(),
            kind: DeviceKind::Printer,
            state,
        }
    }

    fn job(state: JobState) -> Job {
        Job {
            id: JobId::parse("job_one").unwrap(),
            device_id: DeviceId::parse("dev_one").unwrap(),
            state,
            created_at: Utc::now(),
        }
    }

    #[test]
    fn healthy_devices_and_finished_work_raise_nothing() {
        let items = attention_items(
            &[device("Front desk", DeviceState::Online)],
            &[job(JobState::Succeeded)],
            true,
        );

        assert!(items.is_empty());
    }

    #[test]
    fn devices_are_listed_before_the_work_they_broke() {
        let items = attention_items(
            &[device("Front desk", DeviceState::Blocked)],
            &[job(JobState::Failed)],
            true,
        );

        assert_eq!(items.len(), 2);
        assert_eq!(items[0].title, "Front desk");
    }

    /// Each attention item routes to the screen that can act on it. A device
    /// row that opened Activity, or a failed job that opened a device detail,
    /// would send the operator somewhere they cannot fix anything.
    #[test]
    fn each_attention_item_routes_to_the_screen_that_can_act_on_it() {
        let items = attention_items(
            &[device("Front desk", DeviceState::Blocked)],
            &[job(JobState::Failed)],
            true,
        );

        assert!(matches!(items[0].target, Target::Device(_)));
        assert!(matches!(items[1].target, Target::Work));
    }

    #[test]
    fn an_unreachable_agent_reports_no_device_issues_it_cannot_see() {
        let items = attention_items(
            &[device("Front desk", DeviceState::Offline)],
            &[job(JobState::Failed)],
            false,
        );

        assert!(items.is_empty());
    }
}
