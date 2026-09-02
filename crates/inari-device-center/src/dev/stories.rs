//! Whole-screen stories, and the scripted data they run against.
//!
//! Screens live here rather than beside their views because a screen story is
//! about the *data* — an unreachable agent, a directory with nothing in it —
//! and the fixtures that make those cases are shared. Component stories live
//! beside their components; see `ui/button.rs` for the shape.

use chrono::{Duration, Utc};
use gpui::{
    App, AppContext as _, BorrowAppContext as _, Entity, Global, IntoElement, ParentElement as _, Styled as _,
    WeakEntity, Window, div, px,
};
use gpui_component::{StyledExt as _, input::InputState};
use inari_agent_client::{
    AgentConnection, AgentEvent, AgentEventKind, Device, DeviceId, DeviceKind, DeviceState,
    EnrollmentPreview, EventResource, Job, JobId, JobState, ServiceState, SetupAccess,
    SetupSnapshot, SetupStage,
};

use crate::{
    dev::Scope,
    features::{
        activity::ActivityView, devices::DeviceDirectory, overview::OverviewView, setup::SetupView,
        support::SupportView,
    },
    story,
    ui::{
        content::{Section, page},
        gate::Gate,
        status::Status,
        theme::Theme,
    },
};

/// The entities the screen stories drive. Both are noted by the windows that
/// own them at construction; a story whose source has since closed still
/// renders — its controls simply have nowhere to act.
#[derive(Clone, Default)]
struct Sources {
    center: Option<WeakEntity<crate::app::DeviceCenter>>,
    onboarding: Option<WeakEntity<crate::onboarding::Onboarding>>,
}

impl Global for Sources {}

/// State a story needs to keep between frames. Built on first use, because
/// every constructor here wants a `Window` and `init` has none.
#[derive(Default)]
struct Fixtures {
    directory: Option<Entity<DeviceDirectory>>,
    empty_directory: Option<Entity<DeviceDirectory>>,
    invitation: Option<Entity<InputState>>,
}

impl Global for Fixtures {}

pub fn init(cx: &mut App) {
    cx.set_global(Sources::default());
    cx.set_global(Fixtures::default());
}

/// Remember the operations shell for the stories that navigate to it.
pub fn note_center(center: &Entity<crate::app::DeviceCenter>, cx: &mut App) {
    cx.update_global(|sources: &mut Sources, _| sources.center = Some(center.downgrade()));
}

/// Remember the enrollment window for the setup stories.
pub fn note_onboarding(onboarding: &Entity<crate::onboarding::Onboarding>, cx: &mut App) {
    cx.update_global(|sources: &mut Sources, _| {
        sources.onboarding = Some(onboarding.downgrade())
    });
}

fn sources(cx: &App) -> Sources {
    cx.try_global::<Sources>()
        .cloned()
        .unwrap_or_default()
}

fn directory(window: &mut Window, cx: &mut App) -> Entity<DeviceDirectory> {
    if let Some(existing) = cx
        .global::<Fixtures>()
        .directory
        .clone()
    {
        return existing;
    }
    let directory = cx.new(|cx| DeviceDirectory::new(window, cx));
    directory.update(cx, |directory, cx| {
        directory.replace_devices(mock_devices(), cx);
    });
    cx.update_global(|fixtures: &mut Fixtures, _| {
        fixtures.directory = Some(directory.clone())
    });
    directory
}

fn empty_directory(window: &mut Window, cx: &mut App) -> Entity<DeviceDirectory> {
    if let Some(existing) = cx
        .global::<Fixtures>()
        .empty_directory
        .clone()
    {
        return existing;
    }
    let directory = cx.new(|cx| DeviceDirectory::new(window, cx));
    cx.update_global(|fixtures: &mut Fixtures, _| {
        fixtures.empty_directory = Some(directory.clone())
    });
    directory
}

fn invitation(window: &mut Window, cx: &mut App) -> Entity<InputState> {
    if let Some(existing) = cx
        .global::<Fixtures>()
        .invitation
        .clone()
    {
        return existing;
    }
    let input =
        cx.new(|cx| InputState::new(window, cx).placeholder("Paste an invitation link"));
    cx.update_global(|fixtures: &mut Fixtures, _| fixtures.invitation = Some(input.clone()));
    input
}

// ---- scripted fixtures ----

fn device(name: &str, kind: DeviceKind, state: DeviceState) -> Device {
    Device {
        id: DeviceId::parse(
            format!(
                "dev_{}",
                name.split_whitespace()
                    .next()
                    .unwrap_or(name)
                    .to_lowercase()
            )
            .as_str(),
        )
        .unwrap(),
        name: name.into(),
        kind,
        state,
    }
}

fn job(id: &str, state: JobState, device: &str) -> Job {
    Job {
        id: JobId::parse(id).unwrap(),
        device_id: DeviceId::parse(device).unwrap(),
        state,
        created_at: Utc::now(),
    }
}

fn mock_devices() -> Vec<Device> {
    vec![
        device("Front desk printer", DeviceKind::Printer, DeviceState::Online),
        device("Receiving scale", DeviceKind::Scale, DeviceState::Online),
        device("Document scanner", DeviceKind::Scanner, DeviceState::Online),
        device("Label applicator", DeviceKind::Other, DeviceState::Degraded),
        device("Archive printer", DeviceKind::Printer, DeviceState::Offline),
        device("Pallet scale", DeviceKind::Scale, DeviceState::Blocked),
    ]
}

// ---- screens ----

story! {
    id: "screen.overview",
    name: "Overview",
    scope: Scope::Screens,
    about: "The three states of the first screen anyone sees.",
    render: |_dial, _window, cx| {
        let healthy_devices = vec![
            device("Front desk printer", DeviceKind::Printer, DeviceState::Online),
            device("Receiving scale", DeviceKind::Scale, DeviceState::Online),
            device("Document scanner", DeviceKind::Scanner, DeviceState::Online),
        ];
        let attention_devices = vec![
            device("Front desk printer", DeviceKind::Printer, DeviceState::Online),
            device("Receiving scale", DeviceKind::Scale, DeviceState::Offline),
            device("Document scanner", DeviceKind::Scanner, DeviceState::Degraded),
            device("Label applicator", DeviceKind::Other, DeviceState::Blocked),
        ];
        let healthy_jobs = vec![
            job("job_reprint", JobState::Succeeded, "dev_front"),
            job("job_count", JobState::Succeeded, "dev_receiving"),
        ];
        let attention_jobs = vec![
            job("job_labels", JobState::Failed, "dev_label"),
            job("job_scan", JobState::Failed, "dev_document"),
            job("job_next", JobState::Queued, "dev_front"),
        ];
        let connected = Status::service(ServiceState::Running, AgentConnection::Connected);
        let Some(center) = sources(cx).center else {
            return unavailable("the operations window");
        };

        page("story-overview")
            .child(Section::new("All clear").child(
                OverviewView::new(
                    &healthy_devices,
                    &healthy_jobs,
                    connected.clone(),
                    None,
                    center.clone(),
                )
                .into_any_element(),
            ))
            .child(Section::new("Needs attention").child(
                OverviewView::new(
                    &attention_devices,
                    &attention_jobs,
                    connected,
                    None,
                    center.clone(),
                )
                .into_any_element(),
            ))
            .child(Section::new("Agent unreachable").child(
                OverviewView::new(
                    &[],
                    &[],
                    Status::service(ServiceState::Running, AgentConnection::Unavailable),
                    Some(
                        "Device Center could not reach the agent. Start the service, then try \
                         again."
                            .into(),
                    ),
                    center,
                )
                .into_any_element(),
            ))
            .into_any_element()
    },
}

story! {
    id: "screen.gate",
    name: "Gate",
    scope: Scope::Screens,
    about: "Every state of the mark, including the ones that only exist in trouble.",
    render: |dial, _window, _cx| {
        let online = dial.count("Devices online", 3, 0..=3);
        let scenario = |caption: &'static str, status: Status, devices| {
            Section::new(caption).child(Gate::new(status, devices))
        };
        let connected = Status::service(ServiceState::Running, AgentConnection::Connected);
        page("story-gate")
            .child(scenario("Running", connected.clone(), Some((online, 3))))
            .child(scenario(
                "Reconnecting",
                Status::service(ServiceState::Running, AgentConnection::Reconnecting),
                None,
            ))
            .child(scenario(
                "Stopped",
                Status::service(ServiceState::Stopped, AgentConnection::Unavailable),
                None,
            ))
            .child(scenario(
                "Not installed",
                Status::service(ServiceState::NotInstalled, AgentConnection::Unavailable),
                None,
            ))
            .into_any_element()
    },
}

story! {
    id: "screen.devices",
    name: "Devices",
    scope: Scope::Screens,
    about: "The directory with rows, and with none.",
    render: |_dial, window, cx| {
        let populated = directory(window, cx);
        let empty = empty_directory(window, cx);
        page("story-devices")
            .child(Section::new("Populated directory").child(populated))
            .child(Section::new("Empty directory").child(empty))
            .into_any_element()
    },
}

story! {
    id: "screen.activity",
    name: "Activity",
    scope: Scope::Screens,
    about: "Jobs and events, and the empty state under them.",
    render: |_dial, _window, _cx| {
        let now = Utc::now();
        let events = vec![
            AgentEvent {
                sequence: 4,
                occurred_at: now - Duration::minutes(2),
                resource: EventResource::Device(DeviceId::parse("dev_label").unwrap()),
                kind: AgentEventKind::DeviceDisconnected,
                summary: "Label applicator went offline".into(),
            },
            AgentEvent {
                sequence: 3,
                occurred_at: now - Duration::minutes(18),
                resource: EventResource::Job(JobId::parse("job_labels").unwrap()),
                kind: AgentEventKind::JobQueued,
                summary: "Job labels queued".into(),
            },
        ];
        let jobs = vec![
            job("job_labels", JobState::Failed, "dev_label"),
            job("job_reprint", JobState::Running, "dev_front"),
            job("job_count", JobState::Succeeded, "dev_receiving"),
            job("job_next", JobState::Queued, "dev_front"),
        ];
        page("story-activity")
            .child(Section::new("Populated").child(ActivityView::new(&jobs, &events)))
            .child(Section::new("Empty").child(ActivityView::new(&[], &[])))
            .into_any_element()
    },
}

story! {
    id: "screen.support",
    name: "Support",
    scope: Scope::Screens,
    about: "What the screen offers when the agent is not answering.",
    render: |_dial, _window, _cx| {
        page("story-support")
            .child(Section::new("Stopped").child(SupportView::new(
                Status::service(ServiceState::Stopped, AgentConnection::Unavailable),
                ServiceState::Stopped,
                None,
                None,
                false,
            )))
            .child(Section::new("Running, not answering").child(SupportView::new(
                Status::service(ServiceState::Running, AgentConnection::Unavailable),
                ServiceState::Running,
                None,
                Some("connection reset by peer (os error 104)".into()),
                false,
            )))
            .child(Section::new("Healthy").child(SupportView::new(
                Status::service(ServiceState::Running, AgentConnection::Connected),
                ServiceState::Running,
                None,
                None,
                false,
            )))
            .into_any_element()
    },
}

story! {
    id: "screen.setup",
    name: "Setup",
    scope: Scope::Screens,
    about: "Enrollment, from a pasted link to a rejection.",
    render: |_dial, window, cx| {
        let Some(onboarding) = sources(cx).onboarding else {
            return unavailable("the enrollment window");
        };
        let input = invitation(window, cx);

        let snapshot_for = |stage, devices| SetupSnapshot {
            access: SetupAccess::Required,
            stage,
            completed_at: None,
            guidance: None,
            devices,
        };
        let invitation_snapshot = snapshot_for(SetupStage::Invitation, Vec::new());
        let selecting = snapshot_for(
            SetupStage::Devices,
            mock_devices()
                .into_iter()
                .take(3)
                .collect(),
        );
        let connecting = snapshot_for(SetupStage::Connecting, Vec::new());
        let failed = snapshot_for(SetupStage::Failed, Vec::new());

        let selected = selecting
            .devices
            .iter()
            .map(|device| device.id.clone())
            .collect();
        let preview = EnrollmentPreview {
            controller_name: Some("Acme Operations".into()),
            controller_url: "https://controller.acme.example"
                .parse()
                .unwrap(),
            expires_at: Utc::now() + Duration::days(7),
            requires_mutual_tls: true,
            supported_protocol_versions: vec!["1".into()],
        };

        page("story-setup")
            .child(Section::new("Invitation, submitted and invalid").child(SetupView::new(
                invitation_snapshot.clone(),
                input.clone(),
                None,
                Some("invalid Inari invitation link".into()),
                false,
                Default::default(),
                onboarding.clone(),
            )))
            .child(Section::new("Invitation, reviewed").child(SetupView::new(
                invitation_snapshot,
                input.clone(),
                Some(preview),
                None,
                false,
                Default::default(),
                onboarding.clone(),
            )))
            .child(Section::new("Connecting").child(SetupView::new(
                connecting,
                input.clone(),
                None,
                None,
                true,
                Default::default(),
                onboarding.clone(),
            )))
            .child(Section::new("Devices").child(SetupView::new(
                selecting,
                input.clone(),
                None,
                None,
                false,
                selected,
                onboarding.clone(),
            )))
            .child(Section::new("Failed").child(SetupView::new(
                failed,
                input,
                None,
                Some("the controller rejected this computer".into()),
                false,
                Default::default(),
                onboarding,
            )))
            .into_any_element()
    },
}

/// A story whose source window has closed says so, rather than panicking or
/// drawing an empty stage that reads as a crash.
fn unavailable(what: &'static str) -> gpui::AnyElement {
    div()
        .v_flex()
        .py(px(Theme::SPACE_XL))
        .child(format!("This story drives {what}, which is not open."))
        .into_any_element()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixtures_parse_into_real_ids() {
        let device = device("Front desk printer", DeviceKind::Printer, DeviceState::Online);
        assert_eq!(device.id.as_str(), "dev_front");
        let job = job("job_labels", JobState::Failed, "dev_applicator");
        assert_eq!(job.device_id.as_str(), "dev_applicator");
    }
}
