use std::sync::Arc;

use gpui::{
    AnyElement, App, AppContext as _, Context, Entity, FocusHandle, Focusable,
    InteractiveElement as _, IntoElement, KeyBinding, KeyDownEvent, MouseButton,
    ParentElement as _, Render, StatefulInteractiveElement as _, Styled, Subscription, Task,
    Window, WindowControlArea, actions, div, prelude::FluentBuilder as _, px,
};
use gpui_component::{IconName, StyledExt as _, TitleBar, tooltip::Tooltip};
use inari_agent_client::{
    AgentConnection, AgentEvent, Device, DeviceId, DeviceState, Job, ServiceState, SetupAccess,
    SetupSnapshot,
};

use crate::{
    features::{
        activity::ActivityView, devices::DeviceDirectory, overview::OverviewView,
        support::SupportView,
    },
    infrastructure::{AgentRuntime, TrayCommand, TrayController, platform},
    ui::{
        chrome::{self, NavigationRail, PANEL_INSET, RailItem, content_panel},
        content::Typography as _,
        focus,
        icon::{Glyph, Symbol},
        material, motion,
        status::{Status, StatusDot},
        theme::{ActiveTheme as _, Theme},
    },
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
        OpenApiReference,
        ToggleTranslucency,
        ToggleReducedMotion
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

impl Destination {
    const ALL: [Self; 4] = [Self::Overview, Self::Devices, Self::Activity, Self::Support];

    fn index(self) -> usize {
        Self::ALL
            .iter()
            .position(|destination| *destination == self)
            .unwrap_or(0)
    }

    fn rail_item(self) -> RailItem {
        match self {
            Self::Overview => RailItem::new(
                "nav-overview",
                "Overview",
                Symbol::Component(IconName::LayoutDashboard),
                ShowOverview,
            ),
            Self::Devices => RailItem::new("nav-devices", "Devices", Glyph::Device, ShowDevices),
            Self::Activity => {
                RailItem::new("nav-activity", "Activity", Glyph::Activity, ShowActivity)
            },
            Self::Support => RailItem::new("nav-support", "Support", Glyph::Support, ShowSupport),
        }
    }
}

pub struct DeviceCenter {
    destination: Destination,
    /// Where the rail indicator slides from. Equal to `destination` until the
    /// first navigation, so the rail does not animate while the window opens.
    previous_destination: Destination,
    setup: SetupSnapshot,
    devices: Arc<[Device]>,
    device_directory: Entity<DeviceDirectory>,
    jobs: Arc<[Job]>,
    events: Vec<AgentEvent>,
    connection: AgentConnection,
    service_state: ServiceState,
    service_error: Option<String>,
    agent_error: Option<String>,
    identity_retry_available: bool,
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
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let setup_task = Self::load_setup(runtime.clone(), cx);
        let service_task = Self::load_service_state(runtime.clone(), cx);
        let updates_task = Self::listen_for_updates(runtime.clone(), window.window_handle(), cx);
        let device_directory = cx.new(|cx| DeviceDirectory::new(window, cx));
        let appearance_subscription = window.observe_window_appearance(Theme::sync);
        let tray_task = Self::listen_for_tray(tray_commands, window.window_handle(), cx);
        let focus_handle = cx.focus_handle();
        focus_handle.focus(window);
        Self {
            destination: Destination::Overview,
            previous_destination: Destination::Overview,
            setup: SetupSnapshot::unavailable(),
            devices: Arc::default(),
            device_directory,
            jobs: Arc::default(),
            events: Vec::new(),
            connection: AgentConnection::Checking,
            service_state: ServiceState::Checking,
            service_error: None,
            agent_error: None,
            identity_retry_available: false,
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

    /// Open the device directory with `id` selected.
    ///
    /// The route an operator takes most: they see a device in Needs attention
    /// and want its detail. Without this the only way through is Devices, then
    /// finding the same name again in the list they just read it from.
    pub(crate) fn show_device(&mut self, id: DeviceId, cx: &mut Context<Self>) {
        self.device_directory
            .update(cx, |directory, cx| directory.select(id, cx));
        self.navigate(Destination::Devices, cx);
    }

    /// Open Activity, where a failed job's history is.
    pub(crate) fn show_work(&mut self, cx: &mut Context<Self>) {
        self.navigate(Destination::Activity, cx);
    }

    /// The agent's health, resolved once per frame and shared by the titlebar,
    /// the Overview gate, and Support. One resolution means those three can
    /// never disagree about whether the agent is healthy.
    fn agent_status(&self) -> Status {
        Status::service(self.service_state, self.connection)
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

    fn toggle_translucency(
        &mut self,
        _: &ToggleTranslucency,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        material::set_prefer_opaque(material::resolve().is_glass());
        Theme::sync(window, cx);
        cx.notify();
    }

    fn toggle_reduced_motion(
        &mut self,
        _: &ToggleReducedMotion,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        motion::set_reduced(!motion::reduced());
        cx.notify();
    }

    fn navigate(&mut self, destination: Destination, cx: &mut Context<Self>) {
        if self.destination == destination {
            return;
        }
        self.previous_destination = self.destination;
        self.destination = destination;
        cx.notify();
    }

    fn main_content(&self, cx: &mut Context<Self>) -> impl IntoElement {
        match self.destination {
            Destination::Overview => OverviewView::new(
                &self.devices,
                &self.jobs,
                self.agent_status(),
                self.setup.guidance.clone(),
                cx.entity().downgrade(),
            )
            .into_any_element(),
            Destination::Devices => self
                .device_directory
                .clone()
                .into_any_element(),
            Destination::Activity => ActivityView::new(&self.jobs, &self.events).into_any_element(),
            Destination::Support => SupportView::new(
                self.agent_status(),
                self.service_state,
                self.service_error.clone(),
                self.agent_error.clone(),
                self.identity_retry_available,
            )
            .into_any_element(),
        }
    }

    /// The window titlebar.
    ///
    /// The brand starts after the native window controls. GPUI Component gives
    /// titlebar content an 80px macOS inset, followed by one deliberate gap.
    ///
    /// Agent health lives here rather than on one screen because it is the
    /// fact that decides whether anything else on screen can be trusted, and
    /// it has to stay true while the operator is reading Devices or Activity.
    fn titlebar(&self, cx: &mut Context<Self>) -> AnyElement {
        let theme = cx.inari();
        let status = self.agent_status();
        let tone = status.tone;
        let detail = status.detail.clone();

        TitleBar::new()
            .h(px(Theme::TITLEBAR_HEIGHT))
            // The chip's own padding then lands its edge exactly on the
            // panel's right border, so the two right edges read as one line.
            .pr(px(PANEL_INSET - Theme::SPACE_SM))
            .bg(gpui::transparent_black())
            .border_color(gpui::transparent_black())
            .child(chrome::brand_lockup(theme))
            .child(
                div()
                    .id("titlebar-drag-region")
                    .h_full()
                    .flex_1()
                    // Declared on every platform. gpui 0.2.2 consumes control
                    // areas on Windows only, and its caption press path still
                    // loses drags (fixed upstream after this release), so
                    // movement goes through platform::start_window_drag.
                    .window_control_area(WindowControlArea::Drag)
                    .on_mouse_down(MouseButton::Left, |_, window, _| {
                        platform::start_window_drag(window);
                    }),
            )
            .child(
                div()
                    .id("agent-health")
                    .h_flex()
                    .items_center()
                    .gap(px(Theme::SPACE_SM))
                    .h(px(26.0))
                    .px(px(Theme::SPACE_SM))
                    .rounded(px(Theme::RADIUS_CONTROL))
                    .border_1()
                    .border_color(gpui::transparent_black())
                    .when(true, |chip| {
                        chip.cursor_pointer()
                            .focusable()
                            .tab_stop(true)
                            .when(focus::visible(), |chip| {
                                chip.focus(|style| style.border_color(theme.focus_ring))
                            })
                            .hover(|chip| chip.bg(theme.wash_hover))
                            .active(|chip| chip.bg(theme.wash_pressed))
                            .tooltip(move |window, cx| {
                                Tooltip::new(detail.clone()).build(window, cx)
                            })
                            .on_click(cx.listener(|center, _, _, cx| {
                                center.navigate(Destination::Support, cx);
                            }))
                            .on_key_down(cx.listener(|center, event: &KeyDownEvent, _, cx| {
                                if chrome::is_activation(event) {
                                    center.navigate(Destination::Support, cx);
                                    cx.stop_propagation();
                                }
                            }))
                    })
                    .child(StatusDot::new(tone).size(7.0))
                    .child(
                        div()
                            .text_caption()
                            .text_color(theme.text_secondary)
                            .child(format!("Agent {}", status.label.to_lowercase())),
                    ),
            )
            .into_any_element()
    }

    fn rail(&self, enabled: bool) -> impl IntoElement {
        NavigationRail::new(
            Destination::ALL
                .into_iter()
                .map(Destination::rail_item)
                .collect(),
            self.destination.index(),
            self.previous_destination.index(),
        )
        .enabled(enabled)
    }
}

impl Focusable for DeviceCenter {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

impl Render for DeviceCenter {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let titlebar = self.titlebar(cx);
        let theme = cx.inari();
        let font = theme.font_sans.clone();
        let text = theme.text;
        let surface = content_panel(theme);
        // Enrollment has its own window, so by the time this one exists the
        // rail is always live.
        let rail = self.rail(true);

        div()
            .id("device-center")
            .key_context(KEY_CONTEXT)
            .track_focus(&self.focus_handle)
            .on_action(cx.listener(Self::show_overview))
            .on_action(cx.listener(Self::show_devices))
            .on_action(cx.listener(Self::show_activity))
            .on_action(cx.listener(Self::show_support))
            .on_action(cx.listener(Self::refresh_agent_service))
            .on_action(cx.listener(Self::start_agent_service))
            .on_action(cx.listener(Self::restart_agent_service))
            .on_action(cx.listener(Self::open_logs))
            .on_action(cx.listener(Self::open_api_reference))
            .on_action(cx.listener(Self::toggle_translucency))
            .on_action(cx.listener(Self::toggle_reduced_motion))
            // Focus rings follow the input device. Tracked at the root so one
            // pair of handlers covers every control in the window.
            .on_mouse_down(
                gpui::MouseButton::Left,
                cx.listener(|_, _, _, cx| {
                    if focus::set_keyboard(false) {
                        cx.notify();
                    }
                }),
            )
            .on_key_down(cx.listener(|_, event: &gpui::KeyDownEvent, _, cx| {
                if focus::is_navigation(event) && focus::set_keyboard(true) {
                    cx.notify();
                }
            }))
            .size_full()
            .v_flex()
            .font_family(font)
            .text_color(text)
            .child(titlebar)
            .child(
                div()
                    .h_flex()
                    .flex_1()
                    .min_h(px(0.0))
                    .w_full()
                    .child(rail)
                    .child(surface.child(self.main_content(cx))),
            )
    }
}

/// Devices that are online over devices found, or `None` when the agent cannot
/// be reached.
///
/// An unknown count is not zero. Reporting "0 online" during an agent outage
/// sends an operator to check hardware that is working.
pub(crate) fn device_health(devices: &[Device], agent_reachable: bool) -> Option<(usize, usize)> {
    agent_reachable.then(|| {
        let online = devices
            .iter()
            .filter(|device| device.state == DeviceState::Online)
            .count();
        (online, devices.len())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn destination_order_matches_the_rail_indicator_positions() {
        for (index, destination) in Destination::ALL.into_iter().enumerate() {
            assert_eq!(destination.index(), index);
        }
    }

    #[test]
    fn device_health_is_unknown_rather_than_zero_when_the_agent_is_unreachable() {
        assert_eq!(device_health(&[], false), None);
        assert_eq!(device_health(&[], true), Some((0, 0)));
    }
}
