use std::sync::Arc;

use gpui::{
    App, AppContext as _, Context, Entity, FocusHandle, Focusable, InteractiveElement as _,
    IntoElement, KeyBinding, ParentElement as _, Render, StatefulInteractiveElement as _, Styled,
    Subscription, Task, Window, actions, div, prelude::FluentBuilder as _, rems,
};
use gpui_component::{StyledExt as _, Theme, input::InputState};
use inari_agent_client::{
    AgentConnection, AgentEvent, Device, EnrollmentPreview, Job, ServiceState, SetupAccess,
    SetupSnapshot,
};

use crate::{
    assets::image as brand_image,
    features::{
        activity::ActivityView, devices::DeviceDirectory, overview::OverviewView, setup::SetupView,
        support::SupportView,
    },
    infrastructure::{AgentRuntime, TrayCommand, TrayController},
    ui::{NavigationItem, palette},
};

mod runtime;
mod setup;

actions!(
    device_center,
    [
        ShowOverview,
        ShowDevices,
        ShowActivity,
        ShowSupport,
        RetryConnection,
        PreviewInvitation,
        BeginSetup,
        ConfirmDevices,
        ContinueWithoutDevices,
        StartOver,
        RefreshAgentService,
        StartAgentService,
        RestartAgentService,
        OpenLogs,
        OpenApiReference
    ]
);

const KEY_CONTEXT: &str = "DeviceCenter";

pub fn bind_keys(cx: &mut App) {
    cx.bind_keys([
        KeyBinding::new("cmd-1", ShowOverview, Some(KEY_CONTEXT)),
        KeyBinding::new("cmd-2", ShowDevices, Some(KEY_CONTEXT)),
        KeyBinding::new("cmd-3", ShowActivity, Some(KEY_CONTEXT)),
        KeyBinding::new("cmd-4", ShowSupport, Some(KEY_CONTEXT)),
        KeyBinding::new("ctrl-1", ShowOverview, Some(KEY_CONTEXT)),
        KeyBinding::new("ctrl-2", ShowDevices, Some(KEY_CONTEXT)),
        KeyBinding::new("ctrl-3", ShowActivity, Some(KEY_CONTEXT)),
        KeyBinding::new("ctrl-4", ShowSupport, Some(KEY_CONTEXT)),
    ]);
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Destination {
    Overview,
    Devices,
    Activity,
    Support,
}

pub struct DeviceCenter {
    destination: Destination,
    setup: SetupSnapshot,
    devices: Arc<[Device]>,
    device_directory: Entity<DeviceDirectory>,
    jobs: Arc<[Job]>,
    events: Vec<AgentEvent>,
    connection: AgentConnection,
    service_state: ServiceState,
    service_error: Option<String>,
    invitation_input: Entity<InputState>,
    preview: Option<EnrollmentPreview>,
    setup_error: Option<String>,
    setup_working: bool,
    setup_forced: bool,
    runtime: Arc<AgentRuntime>,
    tray: Option<TrayController>,
    focus_handle: FocusHandle,
    _setup_task: Task<()>,
    _service_task: Task<()>,
    _data_task: Task<()>,
    _updates_task: Task<()>,
    _tray_task: Task<()>,
    _appearance_subscription: Subscription,
}

impl DeviceCenter {
    pub fn new(
        runtime: Arc<AgentRuntime>,
        tray_commands: async_channel::Receiver<TrayCommand>,
        initial_invitation: Option<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let setup_task = Self::load_setup(runtime.clone(), cx);
        let service_task = Self::load_service_state(runtime.clone(), cx);
        let updates_task = Self::listen_for_updates(runtime.clone(), window.window_handle(), cx);
        let device_directory = cx.new(|cx| DeviceDirectory::new(window, cx));
        let appearance_subscription = window.observe_window_appearance(|window, cx| {
            Theme::sync_system_appearance(Some(window), cx);
            palette::apply_brand(cx);
        });
        let invitation_input = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("Paste an invitation link")
                .default_value(
                    initial_invitation
                        .clone()
                        .unwrap_or_default(),
                )
        });
        let tray_task = Self::listen_for_tray(tray_commands, window.window_handle(), cx);
        let focus_handle = cx.focus_handle();
        focus_handle.focus(window);
        Self {
            destination: Destination::Overview,
            setup: if initial_invitation.is_some() {
                SetupSnapshot::invitation()
            } else {
                SetupSnapshot::unavailable()
            },
            devices: Arc::default(),
            device_directory,
            jobs: Arc::default(),
            events: Vec::new(),
            connection: AgentConnection::Checking,
            service_state: ServiceState::Checking,
            service_error: None,
            invitation_input,
            preview: None,
            setup_error: None,
            setup_working: false,
            setup_forced: initial_invitation.is_some(),
            runtime,
            tray: None,
            focus_handle,
            _setup_task: setup_task,
            _service_task: service_task,
            _data_task: Task::ready(()),
            _updates_task: updates_task,
            _tray_task: tray_task,
            _appearance_subscription: appearance_subscription,
        }
    }

    pub fn install_tray(&mut self, tray: TrayController) {
        tray.set_connection(runtime::connection_label(self.connection));
        tray.set_setup_required(self.setup.access != SetupAccess::Complete);
        tray.set_service_state(self.service_state);
        self.tray = Some(tray);
    }

    fn show_overview(&mut self, _: &ShowOverview, _: &mut Window, cx: &mut Context<Self>) {
        self.navigate(Destination::Overview, cx);
    }

    fn show_devices(&mut self, _: &ShowDevices, _: &mut Window, cx: &mut Context<Self>) {
        self.navigate(Destination::Devices, cx);
    }

    fn show_activity(&mut self, _: &ShowActivity, _: &mut Window, cx: &mut Context<Self>) {
        self.navigate(Destination::Activity, cx);
    }

    fn show_support(&mut self, _: &ShowSupport, _: &mut Window, cx: &mut Context<Self>) {
        self.navigate(Destination::Support, cx);
    }

    fn navigate(&mut self, destination: Destination, cx: &mut Context<Self>) {
        if self.setup.access == SetupAccess::Complete {
            self.destination = destination;
            cx.notify();
        }
    }

    fn main_content(&self) -> impl IntoElement {
        match self.destination {
            Destination::Overview => {
                OverviewView::new(&self.devices, &self.jobs, self.connection, self.service_state)
                    .into_any_element()
            },
            Destination::Devices => self
                .device_directory
                .clone()
                .into_any_element(),
            Destination::Activity => ActivityView::new(&self.jobs, &self.events).into_any_element(),
            Destination::Support => {
                SupportView::new(self.service_state, self.service_error.clone()).into_any_element()
            },
        }
    }

    fn navigation(&self, colors: palette::Palette) -> impl IntoElement {
        div()
            .id("primary-navigation")
            .v_flex()
            .w(rems(14.5))
            .h_full()
            .flex_shrink_0()
            .p(rems(1.25))
            .border_r_1()
            .border_color(colors.border)
            .bg(colors.sidebar)
            .child(
                div()
                    .h(rems(3.5))
                    .flex()
                    .items_center()
                    .gap(rems(0.7))
                    .px(rems(0.55))
                    .child(
                        brand_image("inari-icon-64.png")
                            .size(rems(1.85))
                            .rounded(rems(0.55))
                            .flex_shrink_0(),
                    )
                    .child(
                        div()
                            .text_size(rems(1.05))
                            .font_weight(gpui::FontWeight::SEMIBOLD)
                            .child("inari"),
                    ),
            )
            .child(
                div()
                    .v_flex()
                    .gap(rems(0.35))
                    .mt(rems(1.35))
                    .child(NavigationItem::new(
                        "Overview",
                        "System health and next actions",
                        self.destination == Destination::Overview,
                        ShowOverview,
                    ))
                    .child(NavigationItem::new(
                        "Devices",
                        "Printers, scales, and scanners",
                        self.destination == Destination::Devices,
                        ShowDevices,
                    ))
                    .child(NavigationItem::new(
                        "Activity",
                        "Jobs and device events",
                        self.destination == Destination::Activity,
                        ShowActivity,
                    ))
                    .child(NavigationItem::new(
                        "Support",
                        "Diagnostics and recovery",
                        self.destination == Destination::Support,
                        ShowSupport,
                    )),
            )
            .child(
                div()
                    .mt_auto()
                    .px(rems(0.55))
                    .pb(rems(0.4))
                    .text_size(rems(0.75))
                    .text_color(colors.text_muted)
                    .child("Private device operations"),
            )
    }
}

impl Focusable for DeviceCenter {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

impl Render for DeviceCenter {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let colors = palette::Palette::current(cx);
        div()
            .id("device-center")
            .key_context(KEY_CONTEXT)
            .track_focus(&self.focus_handle)
            .on_action(cx.listener(Self::show_overview))
            .on_action(cx.listener(Self::show_devices))
            .on_action(cx.listener(Self::show_activity))
            .on_action(cx.listener(Self::show_support))
            .on_action(cx.listener(Self::retry_connection))
            .on_action(cx.listener(Self::preview_invitation))
            .on_action(cx.listener(Self::begin_setup))
            .on_action(cx.listener(Self::confirm_devices))
            .on_action(cx.listener(Self::continue_without_devices))
            .on_action(cx.listener(Self::start_over))
            .on_action(cx.listener(Self::refresh_agent_service))
            .on_action(cx.listener(Self::start_agent_service))
            .on_action(cx.listener(Self::restart_agent_service))
            .on_action(cx.listener(Self::open_logs))
            .on_action(cx.listener(Self::open_api_reference))
            .size_full()
            .bg(colors.canvas)
            .text_color(colors.text)
            .when(self.setup.access == SetupAccess::Complete && !self.setup_forced, |layout| {
                layout
                    .flex()
                    .child(self.navigation(colors))
                    .child(
                        div()
                            .id("main-content")
                            .flex_1()
                            .h_full()
                            .overflow_y_scroll()
                            .child(self.main_content()),
                    )
            })
            .when(self.setup.access != SetupAccess::Complete || self.setup_forced, |layout| {
                layout.child(SetupView::new(
                    self.setup.clone(),
                    self.invitation_input.clone(),
                    self.preview.clone(),
                    self.setup_error.clone(),
                    self.setup_working,
                ))
            })
    }
}
