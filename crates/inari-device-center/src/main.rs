#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use std::{cell::RefCell, rc::Rc};

mod app;
mod assets;
#[cfg(debug_assertions)]
mod dev_tools;
mod features;
mod infrastructure;
mod onboarding;
mod ui;

use gpui::{
    AnyWindowHandle, App, AppContext as _, Application, Bounds, TitlebarOptions, WindowBounds,
    WindowOptions, point, px, size,
};
use gpui_component::Root;

use crate::{
    app::DeviceCenter,
    assets::BrandAssets,
    infrastructure::{AgentRuntime, TrayCommand, TrayController, initialize_logging, platform},
    ui::{effect, material, motion, theme::Theme},
};

/// The operations window, built at startup and shown only once enrollment is
/// out of the way.
///
/// It is created rather than deferred because it owns the `inari://` activation
/// listener, the agent update stream, and the tray. Deferring the window would
/// defer all three, and a link forwarded to an app that is running but has no
/// listener starts a second copy of it.
struct Operations {
    window: RefCell<Option<AnyWindowHandle>>,
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
                    center.update(cx, |center, _| center.install_tray(tray));
                    cx.new(|cx| Root::new(center, window, cx))
                },
            )
            .expect("failed to open Device Center");
        *self.window.borrow_mut() = Some(handle.into());
    }

    /// Show the operations window. Enrollment calls this when it is finished.
    fn reveal(self: &Rc<Self>, cx: &mut App) -> Option<AnyWindowHandle> {
        let handle = (*self.window.borrow())?;
        handle
            .update(cx, |_, window, cx| platform::show_window(window, cx))
            .ok();
        cx.activate(true);
        Some(handle)
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
            // The preview window exists only where a debugger or a fast edit
            // loop can reach it; a release build carries no dev surfaces.
            #[cfg(debug_assertions)]
            dev_tools::init(cx);

            let (tray_sender, tray_commands) = async_channel::bounded(32);
            let tray =
                TrayController::new(tray_sender).expect("failed to create the Device Center tray");
            let operations = Rc::new(Operations { window: RefCell::new(None) });

            let launcher = operations.clone();
            let open_operations: onboarding::OpenOperations =
                Rc::new(move |cx: &mut App| launcher.reveal(cx));
            let onboarding_runtime = runtime.clone();
            let onboarding_operations = open_operations.clone();
            let open_onboarding: onboarding::OpenOnboarding =
                Rc::new(move |invitation: Option<String>, cx: &mut App| {
                    onboarding::open(
                        onboarding_runtime.clone(),
                        onboarding_operations.clone(),
                        invitation,
                        cx,
                    )
                    .ok();
                });

            operations.create(runtime.clone(), tray_commands, tray, open_onboarding.clone(), cx);
            // Both windows start unshown. Enrollment reveals itself only if the
            // agent says this computer still needs it, and otherwise hands
            // straight to the operations window without either one flashing.
            open_onboarding(invitation, cx);
        });
}
