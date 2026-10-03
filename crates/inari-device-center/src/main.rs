#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use std::{cell::RefCell, rc::Rc};

mod app;
mod assets;
#[cfg(debug_assertions)]
mod dev;
mod features;
mod infrastructure;
mod onboarding;
mod ui;

use gpui::{
    AnyWindowHandle, App, AppContext as _, Application, Bounds, TitlebarOptions, WeakEntity,
    WindowBounds, WindowOptions, point, px, size,
};
use gpui_component::Root;

use crate::{
    app::DeviceCenter,
    assets::BrandAssets,
    infrastructure::{AgentRuntime, TrayCommand, TrayController, initialize_logging, platform},
    ui::{effect, material, motion, theme::Theme},
};

/// The operations window, also used to review browser access before enrollment.
///
/// It is created rather than deferred because it owns the `inari://` activation
/// listener, the agent update stream, and the tray. Deferring the window would
/// defer all three, and a link forwarded to an app that is running but has no
/// listener starts a second copy of it.
struct Operations {
    window: RefCell<Option<AnyWindowHandle>>,
    center: RefCell<Option<WeakEntity<DeviceCenter>>>,
}

impl Operations {
    /// Build the window, unshown.
    fn create(
        self: &Rc<Self>,
        runtime: std::sync::Arc<AgentRuntime>,
        tray_commands: async_channel::Receiver<TrayCommand>,
        tray: TrayController,
        open_onboarding: onboarding::OpenOnboarding,
        cx: &mut App,
    ) {
        let bounds = Bounds::centered(None, size(px(1160.), px(780.)), cx);
        let handle = cx
            .open_window(
                WindowOptions {
                    window_bounds: Some(WindowBounds::Windowed(bounds)),
                    // Below this the rail and the content panel start fighting
                    // for the same pixels and the device list stops being
                    // readable beside its detail pane.
                    window_min_size: Some(size(px(880.), px(600.))),
                    titlebar: Some(TitlebarOptions {
                        title: Some("Inari Device Center".into()),
                        // The shell draws its own titlebar so the rail and the
                        // content panel share one continuous glass plane.
                        appears_transparent: true,
                        // Center AppKit's 12px control frames in the titlebar's
                        // design height.
                        traffic_light_position: Some(point(
                            px(20.),
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
                    window.on_window_should_close(cx, |window, cx| {
                        platform::hide_window(window, cx);
                        false
                    });
                    let center = cx.new(|cx| {
                        DeviceCenter::new(runtime, tray_commands, open_onboarding, window, cx)
                    });
                    *self.center.borrow_mut() = Some(center.downgrade());
                    center.update(cx, |center, _| center.install_tray(tray));
                    cx.new(|cx| Root::new(center, window, cx))
                },
            )
            .expect("failed to open Device Center");
        *self.window.borrow_mut() = Some(handle.into());
    }

    /// Show the operations window. Enrollment calls this when it is finished.
    fn reveal(
        self: &Rc<Self>,
        snapshot: inari_agent_client::SetupSnapshot,
        cx: &mut App,
    ) -> Option<AnyWindowHandle> {
        if let Some(center) = self
            .center
            .borrow()
            .as_ref()
            .and_then(WeakEntity::upgrade)
        {
            center.update(cx, |center, cx| center.accept_setup(snapshot, cx));
        }
        let handle = (*self.window.borrow())?;
        handle
            .update(cx, |_, window, cx| platform::show_window(window, cx))
            .ok();
        cx.activate(true);
        Some(handle)
    }

    fn open_link(&self, value: &str, cx: &mut App) {
        let Some(handle) = *self.window.borrow() else {
            return;
        };
        let Some(center) = self
            .center
            .borrow()
            .as_ref()
            .and_then(WeakEntity::upgrade)
        else {
            return;
        };
        if let Err(error) = handle.update(cx, |_, window, cx| {
            center.update(cx, |center, cx| center.open_link(value, window, cx));
        }) {
            tracing::warn!(%error, "Could not open the Inari link");
        }
    }
}

fn main() {
    let invitation = std::env::args()
        .skip(1)
        .find(|argument| argument.starts_with("inari://"));
    if platform::forward_activation(invitation.as_deref()) {
        return;
    }

    let _log_guard = initialize_logging().expect("failed to initialize Device Center logging");
    material::init_from_environment();
    motion::init_from_environment();
    // Registering up front means the renderer never compiles a shader during
    // the first frame that draws one.
    effect::register_all();

    let runtime = AgentRuntime::start().expect("failed to start the local-agent runtime");
    Application::new()
        .with_assets(BrandAssets)
        .run(move |cx| {
            gpui_component::init(cx);
            assets::install_fonts(cx).expect("failed to load Device Center fonts");
            app::bind_keys(cx);
            // The Bench and the devtools exist only where a debugger or a
            // fast edit loop can reach them; a release build carries no dev
            // surfaces. After `gpui_component::init`, whose inspector renderer
            // ours replaces.
            #[cfg(debug_assertions)]
            dev::init(cx);

            let (tray_sender, tray_commands) = async_channel::bounded(32);
            let tray =
                TrayController::new(tray_sender).expect("failed to create the Device Center tray");
            let operations =
                Rc::new(Operations { window: RefCell::new(None), center: RefCell::new(None) });

            let launcher = operations.clone();
            let open_operations: onboarding::OpenOperations =
                Rc::new(move |snapshot, cx: &mut App| launcher.reveal(snapshot, cx));
            let onboarding_runtime = runtime.clone();
            let onboarding_operations = open_operations.clone();
            let setup_window = RefCell::new(None::<onboarding::OnboardingWindow>);
            let open_onboarding: onboarding::OpenOnboarding =
                Rc::new(move |invitation: Option<String>, cx: &mut App| {
                    if let Some(existing) = setup_window.borrow().as_ref()
                        && existing
                            .show(invitation.clone(), cx)
                            .is_ok()
                    {
                        return;
                    }
                    *setup_window.borrow_mut() = onboarding::open(
                        onboarding_runtime.clone(),
                        onboarding_operations.clone(),
                        invitation,
                        cx,
                    )
                    .ok();
                });

            operations.create(runtime.clone(), tray_commands, tray, open_onboarding.clone(), cx);
            // Links choose their own window. A normal launch lets enrollment
            // read the Agent's state before either window becomes visible.
            match invitation {
                Some(link) => operations.open_link(&link, cx),
                None => open_onboarding(None, cx),
            }
        });
}
