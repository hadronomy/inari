#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use std::{cell::RefCell, rc::Rc};

mod app;
mod assets;
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
    ui::{material, motion, theme::Theme},
};

/// What the operations window needs, held until enrollment lets it open.
///
/// The tray is created at startup because it is the app's presence on the
/// system whether or not a window is up, but it belongs to the operations
/// shell once that exists.
struct Operations {
    runtime: std::sync::Arc<AgentRuntime>,
    tray_commands: RefCell<Option<async_channel::Receiver<TrayCommand>>>,
    tray: RefCell<Option<TrayController>>,
    window: RefCell<Option<AnyWindowHandle>>,
}

impl Operations {
    /// Open the operations window, or raise it if it is already open.
    fn open(self: &Rc<Self>, cx: &mut App) -> Option<AnyWindowHandle> {
        if let Some(handle) = *self.window.borrow() {
            handle
                .update(cx, |_, window, cx| platform::show_window(window, cx))
                .ok();
            return Some(handle);
        }
        let tray_commands = self.tray_commands.borrow_mut().take()?;
        let runtime = self.runtime.clone();
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
                    ..WindowOptions::default()
                },
                |window, cx| {
                    Theme::sync(window, cx);
                    window.on_window_should_close(cx, |window, cx| {
                        platform::hide_window(window, cx);
                        false
                    });
                    let center = cx.new(|cx| DeviceCenter::new(runtime, tray_commands, window, cx));
                    if let Some(tray) = self.tray.borrow_mut().take() {
                        center.update(cx, |center, _| center.install_tray(tray));
                    }
                    cx.new(|cx| Root::new(center, window, cx))
                },
            )
            .ok()?;
        let handle = handle.into();
        *self.window.borrow_mut() = Some(handle);
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

    let runtime = AgentRuntime::start().expect("failed to start the local-agent runtime");
    Application::new()
        .with_assets(BrandAssets)
        .run(move |cx| {
            gpui_component::init(cx);
            assets::install_fonts(cx).expect("failed to load Device Center fonts");
            app::bind_keys(cx);

            let (tray_sender, tray_commands) = async_channel::bounded(32);
            let tray =
                TrayController::new(tray_sender).expect("failed to create the Device Center tray");
            let operations = Rc::new(Operations {
                runtime: runtime.clone(),
                tray_commands: RefCell::new(Some(tray_commands)),
                tray: RefCell::new(Some(tray)),
                window: RefCell::new(None),
            });

            // Enrollment opens first and unshown. It reveals itself only if the
            // agent says this computer still needs it, and otherwise hands
            // straight over to the operations window without ever appearing.
            let launcher = operations.clone();
            onboarding::open(
                runtime.clone(),
                Rc::new(move |cx: &mut App| launcher.open(cx)),
                invitation,
                cx,
            )
            .expect("failed to open Inari setup");
        });
}
