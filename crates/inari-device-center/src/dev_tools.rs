//! Development previews: every screen and state, with no agent required.
//!
//! Three sources set the shape. glassy-ui is a gallery application — one page
//! per component, a theme toggle, a next-page key. Comet ships a mock harness
//! that replays scripted events through the real backend trait, so the app
//! runs unmodified against fiction. Zed gives each component a Story that
//! renders it in isolation. This module takes the middle road those choices
//! point to: the screens are already pure views over their data, so a
//! dev-only window renders the real views against scripted scenarios — no
//! mock agent to maintain, no state to fake at the transport.
//!
//! Compiled only under `debug_assertions`; a release build never sees it.
//! Open with cmd-alt-d (ctrl-alt-d on Windows and Linux). The window reuses
//! the app's own rail, pages, and chrome, so it previews the real thing down
//! to the navigation. The Overview previews navigate the real operations
//! window when an attention row is opened; setup previews whose enrollment
//! window has closed render fully but their controls have nowhere to act.

use chrono::{Duration, Utc};
use gpui::{
    App, AppContext as _, BorrowAppContext as _, Entity, FocusHandle, Focusable, Global,
    InteractiveElement as _, IntoElement, KeyBinding, ParentElement as _, Render,
    Pixels, SharedString, StatefulInteractiveElement as _, Styled, WeakEntity, Window,
    WindowOptions, actions, div, point, px, size,
};
use gpui_component::{
    Root, StyledExt as _,
    input::{InputEvent, InputState},
    switch::Switch,
};
use inari_agent_client::{
    AgentConnection, AgentEvent, AgentEventKind, Device, DeviceId, DeviceKind, DeviceState,
    EnrollmentPreview, EventResource, Job, JobId, JobState, ServiceState, SetupAccess,
    SetupSnapshot, SetupStage,
};

use crate::{
    features::{
        activity::ActivityView, devices::DeviceDirectory, overview::OverviewView, setup::SetupView,
        support::SupportView,
    },
    ui::{
        banner::Banner,
        chrome::{NavigationRail, RailItem, content_panel},
        content::{Section, page},
        effect::{self, Frost},
        field,
        gate::Gate,
        icon::Glyph,
        material, motion,
        pixel_bloom::{self, WallControls},
        readout,
        status::Status,
        surface::card,
        theme::{ActiveTheme as _, Appearance, Theme},
        titlebar::WindowChrome,
    },
};

actions!(
    dev_tools,
    [
        ToggleDevTools,
        ShowOverview,
        ShowGate,
        ShowDevices,
        ShowActivity,
        ShowSupport,
        ShowSetup,
        ShowBanners,
        ShowEffects
    ]
);

const KEY_CONTEXT: &str = "DevTools";

/// The entities the previews drive. Both are noted by the windows that own
/// them at construction; a preview whose source has since closed still
/// renders — its controls simply have nowhere to act.
#[derive(Clone, Default)]
struct DevSources {
    center: Option<WeakEntity<crate::app::DeviceCenter>>,
    onboarding: Option<WeakEntity<crate::onboarding::Onboarding>>,
}

impl Global for DevSources {}

/// Remember the operations shell for the previews that navigate to it.
pub fn note_center(center: &Entity<crate::app::DeviceCenter>, cx: &mut App) {
    cx.update_global(|sources: &mut DevSources, _| sources.center = Some(center.downgrade()));
}

/// Remember the enrollment window for the setup previews.
pub fn note_onboarding(onboarding: &Entity<crate::onboarding::Onboarding>, cx: &mut App) {
    cx.update_global(|sources: &mut DevSources, _| {
        sources.onboarding = Some(onboarding.downgrade())
    });
}

fn sources(cx: &App) -> DevSources {
    cx.global::<DevSources>().clone()
}

/// Bind the toggle and note the handler. Called from `main` on debug builds.
pub fn init(cx: &mut App) {
    cx.set_global(DevSources::default());
    cx.bind_keys([
        KeyBinding::new("cmd-alt-d", ToggleDevTools, None),
        KeyBinding::new("ctrl-alt-d", ToggleDevTools, None),
        KeyBinding::new("alt-1", ShowOverview, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-2", ShowGate, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-3", ShowDevices, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-4", ShowActivity, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-5", ShowSupport, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-6", ShowSetup, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-7", ShowBanners, Some(KEY_CONTEXT)),
        KeyBinding::new("alt-8", ShowEffects, Some(KEY_CONTEXT)),
    ]);
    cx.on_action(|_: &ToggleDevTools, cx| toggle(cx));
}

/// Open the preview window, or bring it to the front when it is already open.
fn toggle(cx: &mut App) {
    if let Some(existing) = cx.try_global::<DevWindow>() {
        existing
            .0
            .update(cx, |_, window, _| window.activate_window())
            .ok();
        return;
    }

    let bounds = gpui::Bounds::centered(None, size(px(980.0), px(680.0)), cx);
    let handle = cx
        .open_window(
            WindowOptions {
                window_bounds: Some(gpui::WindowBounds::Windowed(bounds)),
                window_min_size: Some(size(px(760.0), px(540.0))),
                titlebar: Some(gpui::TitlebarOptions {
                    title: Some("Inari Dev Tools".into()),
                    appears_transparent: true,
                    traffic_light_position: Some(point(
                        px(20.0),
                        px((Theme::TITLEBAR_HEIGHT - 12.0) / 2.0),
                    )),
                }),
                window_background: material::resolve().window_background(),
                app_id: Some("dev.inari.device-center".into()),
                show: false,
                ..WindowOptions::default()
            },
            |window, cx| {
                Theme::sync(window, cx);
                let tools = cx.new(|cx| DevTools::new(window, cx));
                cx.new(|cx| Root::new(tools, window, cx))
            },
        )
        .expect("failed to open Dev Tools");
    handle
        .update(cx, |_, window, cx| {
            crate::infrastructure::platform::show_window(window, cx);
        })
        .ok();
    cx.set_global(DevWindow(handle.into()));
}

struct DevWindow(gpui::AnyWindowHandle);

impl Global for DevWindow {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Page {
    Overview,
    Gate,
    Devices,
    Activity,
    Support,
    Setup,
    Banners,
    Effects,
}

impl Page {
    const ALL: [Self; 8] = [
        Self::Overview,
        Self::Gate,
        Self::Devices,
        Self::Activity,
        Self::Support,
        Self::Setup,
        Self::Banners,
        Self::Effects,
    ];

    fn rail_item(self) -> RailItem {
        use crate::ui::icon::Symbol;

        match self {
            Self::Overview => RailItem::new(
                "dev-overview",
                "Overview",
                Symbol::Component(gpui_component::IconName::LayoutDashboard),
                ShowOverview,
            ),
            Self::Gate => RailItem::new("dev-gate", "Gate", Glyph::Agent, ShowGate),
            Self::Devices => RailItem::new("dev-devices", "Devices", Glyph::Device, ShowDevices),
            Self::Activity => {
                RailItem::new("dev-activity", "Activity", Glyph::Activity, ShowActivity)
            },
            Self::Support => RailItem::new("dev-support", "Support", Glyph::Support, ShowSupport),
            Self::Setup => RailItem::new("dev-setup", "Setup", Glyph::Computer, ShowSetup),
            Self::Banners => RailItem::new("dev-banners", "Banners", Glyph::Scale, ShowBanners),
            Self::Effects => RailItem::new("dev-effects", "Effects", Glyph::Activity, ShowEffects),
        }
    }

    fn index(self) -> usize {
        Self::ALL
            .iter()
            .position(|page| *page == self)
            .unwrap_or(0)
    }
}

struct DevTools {
    page: Page,
    focus_handle: FocusHandle,
    directory: Entity<DeviceDirectory>,
    invitation_input: Entity<InputState>,
    wall_controls: WallControls,
}

impl DevTools {
    fn new(window: &mut Window, cx: &mut gpui::Context<Self>) -> Self {
        let wall_controls = WallControls::new(cx);
        let directory = cx.new(|cx| DeviceDirectory::new(window, cx));
        directory.update(cx, |directory, cx| {
            directory.replace_devices(mock_devices(), cx);
        });
        let invitation_input =
            cx.new(|cx| InputState::new(window, cx).placeholder("Paste an invitation link"));
        // The field chrome eases its focus ring off the input's own focus
        // events; the enrollment window reports them, so this one does too.
        cx.subscribe_in(&invitation_input, window, |_, _, event, window, _cx| {
            if matches!(event, InputEvent::Focus | InputEvent::Blur) {
                let focused = matches!(event, InputEvent::Focus);
                if motion::hover_set(field::FADE_KEY_FOCUS, focused) {
                    window.refresh();
                }
            }
        })
        .detach();
        let focus_handle = cx.focus_handle();
        focus_handle.focus(window);
        Self { page: Page::Overview, focus_handle, directory, invitation_input, wall_controls }
    }

    fn navigate(&mut self, page: Page, cx: &mut gpui::Context<Self>) {
        if self.page == page {
            return;
        }
        self.page = page;
        cx.notify();
    }
}

impl Focusable for DevTools {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

impl Render for DevTools {
    fn render(&mut self, window: &mut Window, cx: &mut gpui::Context<Self>) -> impl IntoElement {
        // The previews are for judging how the real components feel, so they
        // owe them the same frame loop the app's own root keeps: without it a
        // hover wash never eases and a copy's tick never expires here, and the
        // preview reports a stiffness the shipped screen does not have.
        if motion::fades_live() || readout::acknowledgements_live() {
            window.request_animation_frame();
        }
        let page = self.page;

        let body: gpui::AnyElement = match page {
            Page::Overview => self.render_overview(cx),
            Page::Gate => render_gate(),
            Page::Devices => self.render_devices(window, cx),
            Page::Activity => render_activity(),
            Page::Support => render_support(),
            Page::Setup => self.render_setup(cx),
            Page::Banners => render_banners(),
            Page::Effects => self.render_effects(cx),
        };

        let theme = cx.inari();
        let font = theme.font_sans.clone();

        let rail = NavigationRail::new(
            Page::ALL
                .into_iter()
                .map(Page::rail_item)
                .collect(),
            page.index(),
            page.index(),
        );

        div()
            .id("dev-tools")
            .key_context(KEY_CONTEXT)
            .track_focus(&self.focus_handle)
            .on_action(
                cx.listener(|this, _: &ShowOverview, _, cx| this.navigate(Page::Overview, cx)),
            )
            .on_action(cx.listener(|this, _: &ShowGate, _, cx| this.navigate(Page::Gate, cx)))
            .on_action(cx.listener(|this, _: &ShowDevices, _, cx| this.navigate(Page::Devices, cx)))
            .on_action(
                cx.listener(|this, _: &ShowActivity, _, cx| this.navigate(Page::Activity, cx)),
            )
            .on_action(cx.listener(|this, _: &ShowSupport, _, cx| this.navigate(Page::Support, cx)))
            .on_action(cx.listener(|this, _: &ShowSetup, _, cx| this.navigate(Page::Setup, cx)))
            .on_action(cx.listener(|this, _: &ShowBanners, _, cx| this.navigate(Page::Banners, cx)))
            .on_action(cx.listener(|this, _: &ShowEffects, _, cx| this.navigate(Page::Effects, cx)))
            .size_full()
            .v_flex()
            .font_family(font)
            .text_color(theme.text)
            .child(WindowChrome::new("devtools-drag").trailing(appearance_controls(theme)))
            .child(
                div()
                    .h_flex()
                    .flex_1()
                    .min_h(px(0.0))
                    .w_full()
                    .child(rail)
                    .child(content_panel(theme).child(body)),
            )
    }
}

impl DevTools {
    fn render_effects(&self, cx: &mut gpui::Context<Self>) -> gpui::AnyElement {
        use crate::ui::content::Typography as _;

        page("dev-effects")
            .child(Section::new("Pixel wall — point at it").child(
                div().h(px(380.0)).w_full().child(
                    pixel_bloom::wall("dev-pixel-bloom").tuning(self.wall_controls.tuning(cx)),
                ),
            ))
            .child(
                Section::new("Frost — an effect over what is under it").child(
                    div()
                        .h_flex()
                        .gap(px(Theme::SPACE_LG))
                        .w_full()
                        .child(frosted("Blurred", 6.0))
                        .child(frosted("Untouched", 0.0)),
                ),
            )
            .child(
                Section::new("Swap — the blur that bridges two glyphs").child(
                    div()
                        .v_flex()
                        .gap(px(Theme::SPACE_MD))
                        .w_full()
                        .child(
                            div()
                                .h_flex()
                                .gap(px(Theme::SPACE_XL))
                                .items_end()
                                .children(
                                    [0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0].map(swap_frame),
                                ),
                        )
                        .child(div().text_caption().child(
                            "Held still along the curve. The middle frames are the ones the \
                             blur exists for: two sharp marks at half strength read as two \
                             marks, two soft ones read as one changing.",
                        )),
                ),
            )
            .child(
                Section::new("Blur — a separable Gaussian over real text").child(
                    div()
                        .h_flex()
                        .items_start()
                        .gap(px(Theme::SPACE_LG))
                        .w_full()
                        .children([0.0, 1.0, 2.0, 6.0, 16.0].map(|radius| blurred_sample(px(radius)))),
                ),
            )
            .child(
                Section::new("Tuning").child(
                    card(cx.inari())
                        .w_full()
                        .p(px(Theme::SPACE_LG))
                        .v_flex()
                        .gap(px(Theme::SPACE_SM))
                        .child(
                            div()
                                .h_flex()
                                .items_center()
                                .justify_between()
                                .gap(px(Theme::SPACE_MD))
                                .child("Bloom from the pointer, not the centre")
                                .child(
                                    Switch::new("wall-from-pointer")
                                        .checked(self.wall_controls.blooms_from_pointer())
                                        // Bound to the entity rather than
                                        // dispatched as an action: a switch does
                                        // not take focus, so an action has no
                                        // path to travel.
                                        .on_click(cx.listener(|this, _: &bool, _, cx| {
                                            this.wall_controls.toggle_origin();
                                            pixel_bloom::restart("dev-pixel-bloom");
                                            cx.notify();
                                        })),
                                ),
                        )
                        .child(self.wall_controls.render(cx)),
                ),
            )
            .into_any_element()
    }

    fn render_overview(&self, cx: &mut gpui::Context<Self>) -> gpui::AnyElement {
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
        let center = sources(cx)
            .center
            .expect("operations window noted at startup");

        page("dev-overview")
            .child(
                Section::new("All clear").child(
                    OverviewView::new(
                        &healthy_devices,
                        &healthy_jobs,
                        connected.clone(),
                        None,
                        center.clone(),
                    )
                    .into_any_element(),
                ),
            )
            .child(
                Section::new("Needs attention").child(
                    OverviewView::new(
                        &attention_devices,
                        &attention_jobs,
                        connected,
                        None,
                        center.clone(),
                    )
                    .into_any_element(),
                ),
            )
            .child(
                Section::new("Agent unreachable").child(
                    OverviewView::new(
                        &[],
                        &[],
                        Status::service(ServiceState::Running, AgentConnection::Unavailable),
                        Some(
                            "Device Center could not reach the agent. Start the service, then \
                             try again."
                                .into(),
                        ),
                        center,
                    )
                    .into_any_element(),
                ),
            )
            .into_any_element()
    }

    fn render_devices(
        &self,
        window: &mut Window,
        cx: &mut gpui::Context<Self>,
    ) -> gpui::AnyElement {
        let empty = cx.new(|cx| DeviceDirectory::new(window, cx));
        page("dev-devices")
            .child(Section::new("Populated directory").child(self.directory.clone()))
            .child(Section::new("Empty directory").child(empty))
            .into_any_element()
    }

    fn render_setup(&self, cx: &mut gpui::Context<Self>) -> gpui::AnyElement {
        let onboarding = sources(cx)
            .onboarding
            .expect("enrollment window noted at startup");

        let snapshot_for = |stage, devices| SetupSnapshot {
            access: SetupAccess::Required,
            stage,
            completed_at: None,
            guidance: None,
            devices,
        };
        let invitation = snapshot_for(SetupStage::Invitation, Vec::new());
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

        let input = self.invitation_input.clone();
        page("dev-setup")
            .child(Section::new("Invitation, submitted and invalid").child(SetupView::new(
                invitation.clone(),
                input.clone(),
                None,
                Some("invalid Inari invitation link".into()),
                false,
                Default::default(),
                onboarding.clone(),
            )))
            .child(Section::new("Invitation, reviewed").child(SetupView::new(
                invitation,
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
                input,
                None,
                None,
                false,
                selected,
                onboarding.clone(),
            )))
            .child(Section::new("Failed").child(SetupView::new(
                failed,
                self.invitation_input.clone(),
                None,
                Some("the controller rejected this computer".into()),
                false,
                Default::default(),
                onboarding,
            )))
            .into_any_element()
    }
}

fn render_gate() -> gpui::AnyElement {
    let scenario = |caption: &'static str, status: Status, devices| {
        Section::new(caption).child(Gate::new(status, devices))
    };
    let connected = Status::service(ServiceState::Running, AgentConnection::Connected);
    page("dev-gate")
        .child(scenario("Running, all devices online", connected.clone(), Some((3, 3))))
        .child(scenario("Running, partial devices", connected.clone(), Some((2, 3))))
        .child(scenario("Running, none online", connected, Some((0, 3))))
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
}

fn render_activity() -> gpui::AnyElement {
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
    page("dev-activity")
        .child(Section::new("Populated").child(ActivityView::new(&jobs, &events)))
        .child(Section::new("Empty").child(ActivityView::new(&[], &[])))
        .into_any_element()
}

fn render_support() -> gpui::AnyElement {
    page("dev-support")
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
}

fn render_banners() -> gpui::AnyElement {
    page("dev-banners")
        .child(
            Section::new("Notices — block nothing").child(
                div()
                    .v_flex()
                    .gap(px(16.0))
                    .child(Banner::new(
                        "dev-positive",
                        crate::ui::status::Tone::Positive,
                        "Agent running",
                        "Live updates are flowing.",
                    ))
                    .child(Banner::new(
                        "dev-busy",
                        crate::ui::status::Tone::Busy,
                        "Checking",
                        "Reading the service state.",
                    ))
                    .child(Banner::new(
                        "dev-neutral",
                        crate::ui::status::Tone::Neutral,
                        "Idle",
                        "Nothing is happening.",
                    )),
            ),
        )
        .child(
            Section::new("Alerts — stop device work").child(
                div()
                    .v_flex()
                    .gap(px(16.0))
                    .child(Banner::new(
                        "dev-caution",
                        crate::ui::status::Tone::Caution,
                        "Reconnecting",
                        "The local connection dropped.",
                    ))
                    .child(Banner::new(
                        "dev-critical",
                        crate::ui::status::Tone::Critical,
                        "Agent unreachable",
                        "Device Center could not reach the agent. Start the service, then try \
                         again.",
                    )),
            ),
        )
        .into_any_element()
}

/// The appearance controls the dev window carries: a theme the OS cannot
/// override, the material preference, and the motion switch.
fn appearance_controls(theme: &Theme) -> gpui::AnyElement {
    let chip = |id: &'static str, label: &'static str| {
        div()
            .id(id)
            .h_flex()
            .items_center()
            .h(px(24.0))
            .px(px(8.0))
            .rounded(px(Theme::RADIUS_CONTROL))
            .border_1()
            .border_color(gpui::transparent_black())
            .text_size(px(12.0))
            .text_color(theme.text_secondary)
            .child(label)
            .hover(|style| style.bg(theme.wash_hover))
    };
    div()
        .h_flex()
        .items_center()
        .gap(px(4.0))
        .pr(px(8.0))
        .child(
            chip("dev-light", "Light")
                .on_click(|_, window, cx| force_appearance(Appearance::Light, window, cx)),
        )
        .child(
            chip("dev-dark", "Dark")
                .on_click(|_, window, cx| force_appearance(Appearance::Dark, window, cx)),
        )
        .child(chip("dev-follow-os", "Follow OS").on_click(|_, window, cx| Theme::sync(window, cx)))
        .child(
            div()
                .h(px(16.0))
                .w(px(1.0))
                .bg(theme.hairline)
                .mx(px(4.0)),
        )
        .child(
            Switch::new("dev-translucent")
                .checked(material::resolve().is_glass())
                .on_click(|checked, window, cx| {
                    material::set_prefer_opaque(!checked);
                    Theme::sync(window, cx);
                }),
        )
        .child(
            Switch::new("dev-reduced-motion")
                .checked(motion::reduced())
                .on_click(|checked, _, cx| {
                    motion::set_reduced(*checked);
                    cx.refresh_windows();
                }),
        )
        .into_any_element()
}

/// Install a theme pinned to `appearance`, whatever the OS says. The next
/// appearance event or session window writes over it, which is fine: this is
/// a preview control, not a preference.
fn force_appearance(appearance: Appearance, window: &mut Window, cx: &mut App) {
    Theme::resolve(appearance, material::resolve()).install(cx);
    window.set_background_appearance(material::resolve().window_background());
    cx.refresh_windows();
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

/// A card of real content with a blur over it, so a capture that resolved and
/// one that did not look different.
fn frosted(label: &'static str, radius: f32) -> impl IntoElement {
    use crate::ui::content::Typography as _;
    use gpui::effect_layer;

    effect_layer(
        &Frost { radius, tint: gpui::rgba(0x6ea8fe22).into() },
        div()
            .v_flex()
            .gap(px(Theme::SPACE_SM))
            .w(px(220.0))
            .p(px(Theme::SPACE_LG))
            .bg(gpui::rgb(0x1c1f26))
            .child(div().text_body().child(label))
            .child(
                div().text_caption().child(
                    "Small text is the honest test: a blur that is not running still reads.",
                ),
            ),
    )
    .corner_radii(px(Theme::RADIUS_CARD))
}

/// One frame of a swap, held still, at four times the size it ships at.
///
/// Big on purpose. The blur is two logical pixels on a fourteen-pixel glyph, so
/// at shipping size the thing being judged is smaller than the eye can argue
/// with, and a preview that cannot be argued with is decoration.
fn swap_frame(progress: f32) -> impl IntoElement {
    use crate::ui::content::Typography as _;
    use crate::ui::icon::Symbol;
    use gpui_component::IconName;

    div()
        .v_flex()
        .items_center()
        .gap(px(Theme::SPACE_SM))
        .child(
            crate::ui::swap::icon(
                SharedString::from(format!("dev-swap-{progress}")),
                Symbol::Component(IconName::Copy),
                Symbol::Component(IconName::Check),
                true,
            )
            .size(56.0)
            .tones(gpui::white(), gpui::rgb(0x4ade80).into())
            .pinned(progress),
        )
        .child(div().text_caption().child(format!("{:.0}%", progress * 100.0)))
}

/// One column of the blur story: the same text at one radius.
///
/// Text rather than a shape on purpose. A glyph is the hardest thing to blur
/// correctly — it is mostly edge, so premultiplication mistakes show up as a
/// dark rim, and it is the case the copy button actually uses.
fn blurred_sample(radius: Pixels) -> impl IntoElement {
    use crate::ui::content::Typography as _;

    let sample = div()
        .v_flex()
        .gap(px(Theme::SPACE_XS))
        .w(px(150.0))
        .child(div().text_body().child("Copied"))
        .child(div().text_caption().child("A halo here means the taps are summing straight alpha."));

    div()
        .v_flex()
        .gap(px(Theme::SPACE_SM))
        .child(div().text_caption().child(format!("blur({}px)", f32::from(radius))))
        .child(effect::blurred(radius, sample))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_preview_page_has_its_own_rail_destination() {
        for (index, page) in Page::ALL.iter().enumerate() {
            assert_eq!(page.index(), index);
        }
    }

    #[test]
    fn fixtures_parse_into_real_ids() {
        let device = device("Front desk printer", DeviceKind::Printer, DeviceState::Online);
        assert_eq!(device.id.as_str(), "dev_front");
        let job = job("job_labels", JobState::Failed, "dev_applicator");
        assert_eq!(job.device_id.as_str(), "dev_applicator");
    }
}
