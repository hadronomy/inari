#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use std::{cell::RefCell, rc::Rc};

mod app;
mod assets;
mod features;
mod infrastructure;
mod ui;

use gpui::{
    AppContext as _, Application, Bounds, TitlebarOptions, WindowBounds, WindowOptions, point, px,
    size,
};
use gpui_component::Root;

use crate::{
    app::DeviceCenter,
    assets::BrandAssets,
    infrastructure::{AgentRuntime, TrayController, initialize_logging, platform},
    ui::{material, motion, theme::Theme},
};

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
            let center_slot = Rc::new(RefCell::new(None));
            let center_slot_for_window = center_slot.clone();
            let runtime = runtime.clone();
            let bounds = Bounds::centered(None, size(px(1160.), px(780.)), cx);
            cx.open_window(
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
                    let center = cx.new(|cx| {
                        DeviceCenter::new(runtime, tray_commands, invitation, window, cx)
                    });
                    center_slot_for_window.replace(Some(center.clone()));
                    cx.new(|cx| Root::new(center, window, cx))
                },
            )
            .expect("failed to open Device Center");
            let tray =
                TrayController::new(tray_sender).expect("failed to create the Device Center tray");
            center_slot
                .borrow()
                .as_ref()
                .expect("Device Center window did not install its root entity")
                .update(cx, |center, _| center.install_tray(tray));
            cx.activate(true);
        });
}
