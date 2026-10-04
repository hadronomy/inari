//! First run, in its own window.
//!
//! Enrollment is a decision, not a place in the app. It gets a small window of
//! its own and the operations shell stays closed until it is finished, so the
//! first thing a new operator sees is one question rather than four navigation
//! items they cannot use yet.
//!
//! This window owns the invitation field. Text input and focus belong to a
//! window in GPUI, so an onboarding entity that lives in the operations window
//! would take keystrokes in the wrong place. That, and not the layout, is why
//! this is a separate entity rather than a second view of `DeviceCenter`.

use std::{cell::RefCell, collections::HashSet, rc::Rc, sync::Arc};

use gpui::{
    AnyWindowHandle, App, AppContext as _, Context, Entity, FocusHandle, Focusable,
    InteractiveElement as _, IntoElement, ParentElement as _, Render, SharedString,
    StatefulInteractiveElement as _, Styled, Subscription, Task, WeakEntity, Window, WindowHandle,
    div, px,
};
use gpui_component::{
    Root, StyledExt as _,
    input::{InputEvent, InputState},
    scroll::ScrollableElement as _,
};
use inari_agent_client::{
    DeviceId, EnrollmentPreview, InvitationLink, SetupAccess, SetupSnapshot, SetupStage,
};

use crate::{
    app::{
        BeginSetup, ConfirmDevices, ContinueWithoutDevices, PreviewInvitation, RestartAgentService,
        RetryConnection, StartOver,
    },
    features::setup::SetupView,
    infrastructure::{
        AgentRuntime, SetupProgressError, SetupProgressMode, SetupResult, agent_failure_message,
        platform, setup_progress_pending,
    },
    ui::{
        field, motion,
        theme::{ActiveTheme as _, Theme},
        titlebar::{self, WindowChrome},
    },
};

/// The widest the enrollment column is allowed to run.
///
/// Narrow on purpose: this screen is one question at a time, and a measure this
/// short keeps the review fields readable without the eye travelling.
const COLUMN: f32 = 372.0;

/// Opens the operations window once enrollment no longer blocks it.
///
/// Held by the onboarding window so the two windows never both believe they
/// own the app. It is a callback rather than a direct call because the shell it
/// opens lives in `main`, which already owns the runtime and the tray.
pub type OpenOperations = Rc<dyn Fn(SetupSnapshot, &mut App) -> Option<AnyWindowHandle>>;

/// Reopens enrollment for an `inari://` link that arrived while the operations
/// shell was up. The link is a credential the operator wants reviewed, so it
/// belongs in the window built to review one.
pub type OpenOnboarding = Rc<dyn Fn(Option<String>, &mut App)>;

pub struct Onboarding {
    runtime: Arc<AgentRuntime>,
    open_operations: OpenOperations,
    /// Set once the window has been revealed, so a later snapshot cannot show
    /// it a second time after the operator has moved on.
    revealed: Rc<RefCell<bool>>,
    handle: Option<AnyWindowHandle>,
    snapshot: SetupSnapshot,
    invitation_input: Entity<InputState>,
    preview: Option<EnrollmentPreview>,
    error: Option<String>,
    working: bool,
    /// An `inari://` link opens enrollment even when the agent believes it is
    /// already set up: the operator is holding a new invitation and means it.
    forced: bool,
    selected_devices: HashSet<DeviceId>,
    focus_handle: FocusHandle,
    _setup_task: Task<()>,
    _field_subscription: Subscription,
}

impl Onboarding {
    pub fn new(
        runtime: Arc<AgentRuntime>,
        open_operations: OpenOperations,
        revealed: Rc<RefCell<bool>>,
        invitation: Option<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let parsed = invitation
            .as_deref()
            .and_then(|value| InvitationLink::parse(value).ok());
        let setup_task = match parsed.clone() {
            Some(invitation) => Self::load_invitation_preview(runtime.clone(), invitation, cx),
            None => Self::load_setup(runtime.clone(), cx),
        };
        let invitation_input = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("Paste an invitation link")
                .default_value(invitation.clone().unwrap_or_default())
        });
        // The field drives three things besides its text: the focus chrome
        // eases in on Focus and out on Blur, a repaint on Change keeps the
        // parse check and the submit button honest, and Enter runs the same
        // action the primary button would — a one-field form submits from the
        // field. Clicking into the field is a focus flip like any other.
        let field_subscription = cx.subscribe_in(
            &invitation_input,
            window,
            |this, _, event: &InputEvent, window, cx| match event {
                InputEvent::Focus | InputEvent::Blur => {
                    if motion::hover_set(field::FADE_KEY_FOCUS, matches!(event, InputEvent::Focus))
                    {
                        window.refresh();
                    }
                },
                InputEvent::Change => cx.notify(),
                InputEvent::PressEnter { .. } => {
                    if !this.working {
                        if this.preview.is_some() {
                            this.begin_setup(&BeginSetup, window, cx);
                        } else {
                            this.preview_invitation(&PreviewInvitation, window, cx);
                        }
                    }
                },
            },
        );
        // A one-field form starts with the field focused, so the first thing
        // the operator does is paste. The subscription is armed first: the
        // focus flip below is what starts the chrome's fade.
        invitation_input.update(cx, |state, cx| state.focus(window, cx));
        let focus_handle = cx.focus_handle();
        // The dev previews render the setup stages against this window's
        // entity; debug builds only.
        #[cfg(debug_assertions)]
        crate::dev::note_onboarding(&cx.entity(), cx);
        Self {
            runtime,
            open_operations,
            revealed,
            handle: Some(window.window_handle()),
            snapshot: if invitation.is_some() {
                SetupSnapshot::invitation()
            } else {
                SetupSnapshot::unavailable()
            },
            invitation_input,
            preview: None,
            error: None,
            working: parsed.is_some(),
            forced: invitation.is_some(),
            selected_devices: HashSet::new(),
            focus_handle,
            _setup_task: setup_task,
            _field_subscription: field_subscription,
        }
    }

    pub(crate) fn set_device_selected(
        &mut self,
        id: DeviceId,
        selected: bool,
        cx: &mut Context<Self>,
    ) {
        if selected {
            self.selected_devices.insert(id);
        } else {
            self.selected_devices.remove(&id);
        }
        cx.notify();
    }

    /// Whether enrollment still stands between the operator and the devices.
    fn blocking(&self) -> bool {
        self.forced || self.snapshot.access == SetupAccess::Required
    }

    /// Show this window, or hand over to the operations shell and close.
    ///
    /// Called after every snapshot rather than only at the end, because the
    /// answer at startup is not known until the agent replies. Until then the
    /// window stays unshown, so nothing flashes on a computer that finished
    /// enrolling months ago.
    fn settle(&mut self, cx: &mut Context<Self>) {
        if self.blocking() {
            if !*self.revealed.borrow() {
                *self.revealed.borrow_mut() = true;
                if let Some(handle) = self.handle {
                    handle
                        .update(cx, |_, window, cx| platform::show_window(window, cx))
                        .ok();
                }
            }
            return;
        }
        (self.open_operations)(self.snapshot.clone(), cx);
        if let Some(handle) = self.handle.take() {
            handle
                .update(cx, |_, window, _| window.remove_window())
                .ok();
        }
    }
}

impl Focusable for Onboarding {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

impl Render for Onboarding {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        // The caption buttons ease their hover fill; this view owns them, so
        // it keeps their frames coming. See the operations root for the other
        // half of the loop.
        if motion::fades_live() {
            window.request_animation_frame();
        }
        // The scroll handle persists in keyed state, so the enrollment
        // content's offset — and its scrollbar — survive re-renders.
        let scroll = window
            .use_keyed_state(SharedString::from("onboarding-scroll"), cx, |_, _| {
                gpui::ScrollHandle::new()
            })
            .read(cx)
            .clone();
        let theme = cx.inari();
        let font = theme.font_sans.clone();
        let text = theme.text;

        div()
            .id("onboarding")
            .track_focus(&self.focus_handle)
            .on_action(cx.listener(Self::retry_connection))
            .on_action(cx.listener(Self::preview_invitation))
            .on_action(cx.listener(Self::begin_setup))
            .on_action(cx.listener(Self::confirm_devices))
            .on_action(cx.listener(Self::continue_without_devices))
            .on_action(cx.listener(Self::start_over))
            .on_action(cx.listener(Self::restart_agent_service))
            .size_full()
            .v_flex()
            .font_family(font)
            .text_color(text)
            .child(
                WindowChrome::new("onboarding-drag")
                    .leading(titlebar::title(theme, "Set up Inari")),
            )
            .child(
                div()
                    .id("onboarding-scroll")
                    .relative()
                    .flex_1()
                    .min_h(px(0.0))
                    .child(
                        div()
                            .id("onboarding-area")
                            .h_full()
                            .overflow_y_scroll()
                            .track_scroll(&scroll)
                            .child(
                                // The column is centred in both axes and the padding is
                                // the window's, not the view's: the same content then
                                // sits correctly whether it is two fields or a device
                                // list, and it never touches the window edge.
                                div()
                                    .v_flex()
                                    .h_full()
                                    .w_full()
                                    .items_center()
                                    .justify_center()
                                    .px(px(Theme::SPACE_XL))
                                    .py(px(Theme::SPACE_2XL))
                                    .child(
                                        div()
                                            .v_flex()
                                            .w_full()
                                            .max_w(px(COLUMN))
                                            .child(SetupView::new(
                                                self.snapshot.clone(),
                                                self.invitation_input.clone(),
                                                self.preview.clone(),
                                                self.error.clone(),
                                                self.working,
                                                self.selected_devices.clone(),
                                                cx.entity().downgrade(),
                                            )),
                                    ),
                            ),
                    )
                    .vertical_scrollbar(&scroll),
            )
    }
}

/// Handle for the hidden enrollment window and its persistent onboarding entity.
pub struct OnboardingWindow {
    handle: WindowHandle<Root>,
    onboarding: WeakEntity<Onboarding>,
}

impl OnboardingWindow {
    pub fn show(&self, invitation: Option<String>, cx: &mut App) -> gpui::Result<()> {
        self.handle.update(cx, |_, window, cx| {
            if let Some(invitation) = invitation {
                self.onboarding
                    .update(cx, |onboarding, cx| {
                        onboarding.receive_invitation(invitation, window, cx);
                    })
                    .ok();
            }
            platform::show_window(window, cx);
        })
    }
}

/// Open the enrollment window, unshown.
///
/// It reveals itself only once the agent has confirmed that enrollment is
/// actually required. A window that appears and then vanishes is worse than one
/// that takes a moment to appear.
pub fn open(
    runtime: Arc<AgentRuntime>,
    open_operations: OpenOperations,
    invitation: Option<String>,
    cx: &mut App,
) -> gpui::Result<OnboardingWindow> {
    let revealed = Rc::new(RefCell::new(false));
    let source = Rc::new(RefCell::new(None));
    let created_source = source.clone();
    let bounds = gpui::Bounds::centered(None, gpui::size(px(468.0), px(660.0)), cx);
    let handle = cx.open_window(
        gpui::WindowOptions {
            window_bounds: Some(gpui::WindowBounds::Windowed(bounds)),
            window_min_size: Some(gpui::size(px(420.0), px(560.0))),
            titlebar: Some(gpui::TitlebarOptions {
                title: Some("Set up Inari".into()),
                appears_transparent: true,
                traffic_light_position: Some(gpui::point(
                    px(20.0),
                    px((Theme::TITLEBAR_HEIGHT - 12.0) / 2.0),
                )),
            }),
            window_background: crate::ui::material::resolve().window_background(),
            app_id: Some("dev.inari.device-center".into()),
            show: false,
            ..gpui::WindowOptions::default()
        },
        |window, cx| {
            Theme::sync(window, cx);
            window.on_window_should_close(cx, |window, cx| {
                platform::hide_window(window, cx);
                false
            });
            let onboarding = cx.new(|cx| {
                Onboarding::new(runtime, open_operations, revealed, invitation, window, cx)
            });
            *created_source.borrow_mut() = Some(onboarding.downgrade());
            cx.new(|cx| Root::new(onboarding, window, cx))
        },
    )?;
    Ok(OnboardingWindow {
        handle,
        onboarding: source
            .borrow_mut()
            .take()
            .expect("the setup window owns its view"),
    })
}

impl Onboarding {
    fn receive_invitation(&mut self, value: String, window: &mut Window, cx: &mut Context<Self>) {
        if self.working {
            return;
        }
        self.invitation_input
            .update(cx, |input, cx| {
                input.set_value(value.clone(), window, cx);
                input.focus(window, cx);
            });
        self.forced = true;
        self.snapshot = SetupSnapshot::invitation();
        self.preview = None;
        self.error = None;
        match InvitationLink::parse(&value) {
            Ok(invitation) => {
                self.working = true;
                self._setup_task =
                    Self::load_invitation_preview(self.runtime.clone(), invitation, cx);
            },
            Err(error) => self.error = Some(error.to_string()),
        }
        cx.notify();
    }
    fn load_invitation_preview(
        runtime: Arc<AgentRuntime>,
        invitation: InvitationLink,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        let response = runtime.preview(invitation);
        cx.spawn(async move |onboarding, cx| {
            let result = response.await;
            if let Some(onboarding) = onboarding.upgrade() {
                onboarding
                    .update(cx, |onboarding, cx| {
                        onboarding.working = false;
                        match result {
                            Ok(Ok(preview)) => onboarding.preview = Some(preview),
                            Ok(Err(error)) => {
                                onboarding.error = Some(agent_failure_message(&error).into());
                            },
                            Err(_) => {
                                onboarding.error =
                                    Some("The agent stopped before it replied.".into());
                            },
                        }
                        onboarding.settle(cx);
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    fn load_setup(runtime: Arc<AgentRuntime>, cx: &mut Context<Self>) -> Task<()> {
        Self::apply_setup(runtime.setup(), cx)
    }

    /// Read setup again after clearing the cached identity.
    fn retry_setup(runtime: Arc<AgentRuntime>, cx: &mut Context<Self>) -> Task<()> {
        Self::apply_setup(runtime.retry_setup(), cx)
    }

    fn apply_setup(
        response: tokio::sync::oneshot::Receiver<SetupResult>,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        cx.spawn(async move |onboarding, cx| {
            let snapshot = response.await.ok();
            if let Some(onboarding) = onboarding.upgrade() {
                onboarding
                    .update(cx, |onboarding, cx| {
                        let snapshot = snapshot
                            .map(|result| result.snapshot)
                            .unwrap_or_else(SetupSnapshot::unavailable);
                        onboarding.snapshot =
                            if onboarding.forced { SetupSnapshot::invitation() } else { snapshot };
                        onboarding.select_all_devices();
                        onboarding.track_progress(cx);
                        onboarding.settle(cx);
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    fn retry_connection(&mut self, _: &RetryConnection, _: &mut Window, cx: &mut Context<Self>) {
        if self.working {
            return;
        }
        self.error = None;
        if self.snapshot.access == SetupAccess::Required {
            self.working = true;
            self._setup_task =
                Self::follow_setup(self.runtime.clone(), SetupProgressMode::Retry, cx);
        } else {
            self._setup_task = Self::retry_setup(self.runtime.clone(), cx);
        }
        cx.notify();
    }

    fn restart_agent_service(
        &mut self,
        _: &RestartAgentService,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.working
            || !self.snapshot.restart_required
            || self.snapshot.access != SetupAccess::Required
        {
            return;
        }
        self.working = true;
        self.error = None;
        let response = self.runtime.restart_setup();
        self._setup_task = cx.spawn(async move |onboarding, cx| {
            let result = response.await;
            if let Some(onboarding) = onboarding.upgrade() {
                onboarding
                    .update(cx, |onboarding, cx| {
                        match result {
                            Ok(Ok(())) => {
                                onboarding._setup_task = Self::follow_setup(
                                    onboarding.runtime.clone(),
                                    SetupProgressMode::AfterRestart,
                                    cx,
                                );
                            },
                            result => {
                                onboarding.working = false;
                                onboarding.error = Some(match result {
                                    Ok(Err(error)) => error.to_string(),
                                    _ => "The Agent service did not restart. Try again.".into(),
                                });
                            },
                        }
                        cx.notify();
                    })
                    .ok();
            }
        });
        cx.notify();
    }

    fn track_progress(&mut self, cx: &mut Context<Self>) {
        if setup_progress_pending(&self.snapshot, false) {
            self.working = true;
            self._setup_task =
                Self::follow_setup(self.runtime.clone(), SetupProgressMode::Observe, cx);
        }
    }

    fn follow_setup(
        runtime: Arc<AgentRuntime>,
        mode: SetupProgressMode,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        let mut updates = runtime.follow_setup(mode);
        cx.spawn(async move |onboarding, cx| {
            while let Some(result) = updates.recv().await {
                let Some(onboarding) = onboarding.upgrade() else {
                    return;
                };
                if onboarding
                    .update(cx, |onboarding, cx| {
                        match result {
                            Ok(snapshot) => {
                                onboarding.working = setup_progress_pending(
                                    &snapshot,
                                    mode == SetupProgressMode::AfterRestart,
                                );
                                onboarding.snapshot = snapshot;
                                onboarding.select_all_devices();
                                onboarding.error = None;
                            },
                            Err(error) => {
                                onboarding.working = false;
                                onboarding.error = Some(match error {
                                    SetupProgressError::Agent(error) => {
                                        agent_failure_message(&error).into()
                                    },
                                    SetupProgressError::TimedOut if onboarding.snapshot.restart_required => {
                                        "The Agent still requires a restart. Select Restart Agent to try again.".into()
                                    },
                                    error => error.to_string(),
                                });
                            },
                        }
                        onboarding.settle(cx);
                        cx.notify();
                    })
                    .is_err()
                {
                    return;
                }
            }
            if let Some(onboarding) = onboarding.upgrade() {
                onboarding
                    .update(cx, |onboarding, cx| {
                        if onboarding.working {
                            onboarding.working = false;
                            onboarding.error =
                                Some("The connection check stopped. Try again.".into());
                            cx.notify();
                        }
                    })
                    .ok();
            }
        })
    }

    fn preview_invitation(
        &mut self,
        _: &PreviewInvitation,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.working {
            return;
        }
        let value = self.invitation_input.read(cx).value();
        let invitation = match InvitationLink::parse(value.as_str()) {
            Ok(invitation) => invitation,
            Err(error) => {
                self.error = Some(error.to_string());
                self.preview = None;
                cx.notify();
                return;
            },
        };
        self.working = true;
        self.error = None;
        self.preview = None;
        self._setup_task = Self::load_invitation_preview(self.runtime.clone(), invitation, cx);
        cx.notify();
    }

    fn begin_setup(&mut self, _: &BeginSetup, window: &mut Window, cx: &mut Context<Self>) {
        if self.working {
            return;
        }
        let value = self.invitation_input.read(cx).value();
        let invitation = match InvitationLink::parse(value.as_str()) {
            Ok(invitation) => invitation,
            Err(error) => {
                self.error = Some(error.to_string());
                cx.notify();
                return;
            },
        };
        self.working = true;
        self.error = None;
        self.forced = false;
        self.invitation_input
            .update(cx, |input, cx| input.set_value("", window, cx));
        let response = self.runtime.begin_setup(invitation);
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    fn confirm_devices(&mut self, _: &ConfirmDevices, _: &mut Window, cx: &mut Context<Self>) {
        if self.working {
            return;
        }
        let device_ids = self
            .selected_devices
            .iter()
            .cloned()
            .collect();
        self.working = true;
        self.error = None;
        let response = self.runtime.confirm_devices(device_ids);
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    fn continue_without_devices(
        &mut self,
        _: &ContinueWithoutDevices,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.working {
            return;
        }
        self.working = true;
        self.error = None;
        let response = self.runtime.confirm_devices(Vec::new());
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    fn start_over(&mut self, _: &StartOver, _: &mut Window, cx: &mut Context<Self>) {
        if self.working {
            return;
        }
        self.working = true;
        self.error = None;
        self.preview = None;
        let response = self.runtime.cancel_setup();
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    fn apply_setup_response(
        response: tokio::sync::oneshot::Receiver<
            inari_agent_client::AgentClientResult<SetupSnapshot>,
        >,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        cx.spawn(async move |onboarding, cx| {
            let result = response.await;
            if let Some(onboarding) = onboarding.upgrade() {
                onboarding
                    .update(cx, |onboarding, cx| {
                        onboarding.working = false;
                        match result {
                            Ok(Ok(snapshot)) => {
                                onboarding.snapshot = snapshot;
                                onboarding.select_all_devices();
                                onboarding.preview = None;
                                onboarding.track_progress(cx);
                            },
                            Ok(Err(error)) => {
                                onboarding.error = Some(agent_failure_message(&error).into());
                            },
                            Err(_) => {
                                onboarding.error =
                                    Some("The agent stopped before it replied.".into());
                            },
                        }
                        onboarding.settle(cx);
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    fn select_all_devices(&mut self) {
        self.selected_devices = default_device_selection(&self.snapshot);
    }
}

fn default_device_selection(setup: &SetupSnapshot) -> HashSet<DeviceId> {
    if setup.stage == SetupStage::Devices {
        setup
            .devices
            .iter()
            .map(|device| device.id.clone())
            .collect()
    } else {
        HashSet::new()
    }
}

#[cfg(test)]
mod tests {
    use std::{cell::RefCell, rc::Rc, sync::Arc};

    use chrono::Utc;
    use gpui::TestAppContext;
    use inari_agent_client::{
        AgentClientError, AgentClientResult, ClientIdentity, Device, DeviceKind, DeviceState,
        IdentityStore,
    };

    use super::*;
    use crate::app::RestartAgentService;

    #[derive(Clone, Copy)]
    struct TestIdentityStore;

    impl IdentityStore for TestIdentityStore {
        fn load(&self) -> AgentClientResult<Option<ClientIdentity>> {
            Err(AgentClientError::IdentityLocked("test identity is locked".into()))
        }

        fn store(&self, _: &ClientIdentity) -> AgentClientResult<()> {
            Ok(())
        }
    }

    fn runtime() -> Arc<AgentRuntime> {
        AgentRuntime::with_identity_store(TestIdentityStore).expect("test Agent runtime starts")
    }

    fn init_test_app(cx: &mut TestAppContext) {
        cx.update(gpui_component::init);
        #[cfg(debug_assertions)]
        cx.update(crate::dev::init);
    }

    fn invitation(fragment: &str) -> String {
        format!("inari://controller.example/setup#{fragment}")
    }

    fn complete_snapshot() -> SetupSnapshot {
        SetupSnapshot {
            access: SetupAccess::Complete,
            stage: SetupStage::Complete,
            restart_required: false,
            completed_at: Some(Utc::now()),
            guidance: None,
            devices: Vec::new(),
        }
    }

    #[test]
    fn device_selection_starts_with_every_found_device() {
        let device_id = DeviceId::parse("front-desk-printer").unwrap();
        let setup = SetupSnapshot {
            access: SetupAccess::Required,
            stage: SetupStage::Devices,
            restart_required: false,
            completed_at: None,
            guidance: None,
            devices: vec![Device {
                id: device_id.clone(),
                name: "Front desk printer".into(),
                kind: DeviceKind::Printer,
                state: DeviceState::Online,
            }],
        };

        assert_eq!(default_device_selection(&setup), [device_id].into());
        assert!(default_device_selection(&SetupSnapshot::invitation()).is_empty());
    }

    #[gpui::test]
    fn completion_hands_the_authoritative_snapshot_to_operations(cx: &mut TestAppContext) {
        init_test_app(cx);
        let captured = Rc::new(RefCell::new(Vec::new()));
        let captured_by_open = captured.clone();
        let open_operations: OpenOperations = Rc::new(move |snapshot, _| {
            captured_by_open
                .borrow_mut()
                .push(snapshot);
            None
        });
        let window = cx.update(|app| {
            open(runtime(), open_operations, Some("not an invitation".into()), app)
                .expect("setup window opens")
        });
        let onboarding = window
            .onboarding
            .upgrade()
            .expect("setup view exists");
        let expected = complete_snapshot();

        cx.update(|app| {
            onboarding.update(app, |onboarding, cx| {
                onboarding.forced = false;
                onboarding.working = true;
                let (sender, receiver) = tokio::sync::oneshot::channel();
                onboarding._setup_task = Onboarding::apply_setup_response(receiver, cx);
                sender
                    .send(Ok(expected.clone()))
                    .expect("completion response is pending");
            });
        });
        cx.run_until_parked();

        assert_eq!(captured.borrow().as_slice(), &[expected]);
    }

    #[gpui::test]
    fn reopening_setup_reuses_the_window_and_input_and_rejects_duplicate_invitation(
        cx: &mut TestAppContext,
    ) {
        init_test_app(cx);
        let open_operations: OpenOperations = Rc::new(|_, _| None);
        let window = cx.update(|app| {
            open(runtime(), open_operations, Some("not an invitation".into()), app)
                .expect("setup window opens")
        });
        let onboarding_before = window
            .onboarding
            .upgrade()
            .expect("setup view exists");
        let input_before =
            onboarding_before.read_with(cx, |onboarding, _| onboarding.invitation_input.clone());
        let first = invitation("first");
        let duplicate = invitation("duplicate");

        cx.update(|app| {
            window
                .show(Some(first.clone()), app)
                .expect("setup window reopens")
        });
        cx.update(|app| {
            onboarding_before.update(app, |onboarding, _| {
                assert!(onboarding.working);
            });
            window
                .show(Some(duplicate), app)
                .expect("setup window remains available");
        });

        let onboarding_after = window
            .onboarding
            .upgrade()
            .expect("setup view remains");
        let input_after =
            onboarding_after.read_with(cx, |onboarding, _| onboarding.invitation_input.clone());
        let value = input_after.read_with(cx, |input, _| input.value());
        assert_eq!(onboarding_before, onboarding_after);
        assert_eq!(input_before, input_after);
        assert_eq!(value, first);
    }

    #[gpui::test]
    fn in_flight_setup_rejects_duplicate_mutations(cx: &mut TestAppContext) {
        init_test_app(cx);
        let open_operations: OpenOperations = Rc::new(|_, _| None);
        let window = cx.update(|app| {
            open(runtime(), open_operations, Some("not an invitation".into()), app)
                .expect("setup window opens")
        });
        let onboarding = window
            .onboarding
            .upgrade()
            .expect("setup view exists");

        cx.update(|app| {
            window
                .handle
                .update(app, |_, window, app| {
                    onboarding.update(app, |onboarding, cx| {
                        onboarding.snapshot = SetupSnapshot {
                            access: SetupAccess::Required,
                            stage: SetupStage::Securing,
                            restart_required: true,
                            completed_at: None,
                            guidance: None,
                            devices: Vec::new(),
                        };
                        onboarding.working = true;
                        onboarding.error = Some("keep this error".into());
                        onboarding.restart_agent_service(&RestartAgentService, window, cx);
                        onboarding.preview_invitation(&PreviewInvitation, window, cx);
                        onboarding.begin_setup(&BeginSetup, window, cx);
                        onboarding.confirm_devices(&ConfirmDevices, window, cx);
                        onboarding.continue_without_devices(&ContinueWithoutDevices, window, cx);
                        onboarding.start_over(&StartOver, window, cx);
                    });
                })
                .expect("setup window remains open");
        });

        let (working, error) = onboarding
            .read_with(cx, |onboarding, _| (onboarding.working, onboarding.error.clone()));
        assert!(working);
        assert_eq!(error.as_deref(), Some("keep this error"));
    }
}
