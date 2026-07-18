#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use std::{cell::RefCell, rc::Rc};

mod app;
mod assets;
mod features;
mod infrastructure;
mod ui;

use gpui::{
    AppContext as _, Application, Bounds, TitlebarOptions, WindowBounds, WindowOptions, px, size,
};
use gpui_component::{Root, Theme};

use crate::{
    app::DeviceCenter,
    assets::BrandAssets,
    infrastructure::{AgentRuntime, TrayController, initialize_logging, platform},
    ui::palette,
};

fn main() {
    let invitation = std::env::args()
        .skip(1)
        .find(|argument| argument.starts_with("inari://"));
    if platform::forward_activation(invitation.as_deref()) {
        return;
    }

    let _log_guard = initialize_logging().expect("failed to initialize Device Center logging");

    let runtime = AgentRuntime::start().expect("failed to start the local-agent runtime");
    Application::new()
        .with_assets(BrandAssets)
        .run(move |cx| {
            gpui_component::init(cx);
            app::bind_keys(cx);

            let (tray_sender, tray_commands) = async_channel::bounded(32);
            let center_slot = Rc::new(RefCell::new(None));
            let center_slot_for_window = center_slot.clone();
            let runtime = runtime.clone();
            let bounds = Bounds::centered(None, size(px(1120.), px(760.)), cx);
            cx.open_window(
                WindowOptions {
                    window_bounds: Some(WindowBounds::Windowed(bounds)),
                    window_min_size: Some(size(px(780.), px(560.))),
                    titlebar: Some(TitlebarOptions {
                        title: Some("Inari Device Center".into()),
                        ..TitlebarOptions::default()
                    }),
                    app_id: Some("dev.inari.device-center".into()),
                    ..WindowOptions::default()
                },
                |window, cx| {
                    Theme::sync_system_appearance(Some(window), cx);
                    palette::apply_brand(cx);
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
