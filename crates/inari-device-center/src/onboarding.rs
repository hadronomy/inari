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
    StatefulInteractiveElement as _, Styled, Subscription, Task, Window, WindowHandle, div, px,
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
        BeginSetup, ConfirmDevices, ContinueWithoutDevices, PreviewInvitation, RetryConnection,
        StartOver,
    },
    features::setup::SetupView,
    infrastructure::{AgentRuntime, SetupResult, agent_failure_message, platform},
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
pub type OpenOperations = Rc<dyn Fn(&mut App) -> Option<AnyWindowHandle>>;

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
        (self.open_operations)(cx);
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
) -> gpui::Result<WindowHandle<Root>> {
    let revealed = Rc::new(RefCell::new(false));
    let bounds = gpui::Bounds::centered(None, gpui::size(px(468.0), px(660.0)), cx);
    cx.open_window(
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
            let onboarding = cx.new(|cx| {
                Onboarding::new(runtime, open_operations, revealed, invitation, window, cx)
            });
            cx.new(|cx| Root::new(onboarding, window, cx))
        },
    )
}

impl Onboarding {
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
                        onboarding.settle(cx);
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    fn retry_connection(&mut self, _: &RetryConnection, _: &mut Window, cx: &mut Context<Self>) {
        self.error = None;
        self._setup_task = Self::retry_setup(self.runtime.clone(), cx);
        cx.notify();
    }

    fn preview_invitation(
        &mut self,
        _: &PreviewInvitation,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
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
        self.working = true;
        self.error = None;
        let response = self.runtime.confirm_devices(Vec::new());
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    fn start_over(&mut self, _: &StartOver, _: &mut Window, cx: &mut Context<Self>) {
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
    use super::*;
    use inari_agent_client::{Device, DeviceKind, DeviceState};

    #[test]
    fn device_selection_starts_with_every_found_device() {
        let device_id = DeviceId::parse("front-desk-printer").unwrap();
        let setup = SetupSnapshot {
            access: SetupAccess::Required,
            stage: SetupStage::Devices,
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
}
