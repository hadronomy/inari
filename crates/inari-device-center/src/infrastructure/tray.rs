use anyhow::Context as _;
use async_channel::Sender;
use inari_agent_client::ServiceState;
use tray_icon::{
    Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent,
    menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem},
};

use crate::assets::BrandAssets;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TrayCommand {
    Open,
    ReviewSetup,
    ServiceAction,
    OpenLogs,
    Quit,
}

pub struct TrayController {
    _icon: TrayIcon,
    status: MenuItem,
    setup: MenuItem,
    service_action: MenuItem,
}

impl TrayController {
    pub fn new(sender: Sender<TrayCommand>) -> anyhow::Result<Self> {
        let menu = Menu::new();
        let status = MenuItem::new("Checking local agent", false, None);
        let open = MenuItem::new("Open Device Center", true, None);
        let setup = MenuItem::new("Review setup", true, None);
        let service_action = MenuItem::new("Checking agent service", false, None);
        let logs = MenuItem::new("Open logs", true, None);
        let quit = MenuItem::new("Quit Device Center", true, None);
        menu.append_items(&[
            &status,
            &PredefinedMenuItem::separator(),
            &open,
            &setup,
            &service_action,
            &logs,
            &PredefinedMenuItem::separator(),
            &quit,
        ])?;

        let icon = load_icon()?;
        let icon = TrayIconBuilder::new()
            .with_menu(Box::new(menu))
            .with_menu_on_left_click(false)
            .with_tooltip("Inari Device Center")
            .with_icon(icon)
            .build()?;

        install_menu_handler(
            sender.clone(),
            open.id().clone(),
            setup.id().clone(),
            service_action.id().clone(),
            logs.id().clone(),
            quit.id().clone(),
        );
        install_tray_handler(sender);

        Ok(Self { _icon: icon, status, setup, service_action })
    }

    pub fn set_connection(&self, label: &str) {
        self.status.set_text(label);
    }

    pub fn set_setup_required(&self, required: bool) {
        self.setup.set_enabled(required);
        self.setup
            .set_text(if required { "Continue setup" } else { "Setup complete" });
    }

    pub fn set_service_state(&self, state: ServiceState) {
        let (label, enabled) = match state {
            ServiceState::Checking => ("Checking agent service", false),
            ServiceState::Starting => ("Starting agent service", false),
            ServiceState::Running => ("Restart agent service", true),
            ServiceState::Stopped => ("Start agent service", true),
            ServiceState::NotInstalled => ("Agent service is not installed", false),
            ServiceState::Unavailable => ("Service controls unavailable", false),
        };
        self.service_action.set_text(label);
        self.service_action.set_enabled(enabled);
    }
}

fn install_menu_handler(
    sender: Sender<TrayCommand>,
    open: tray_icon::menu::MenuId,
    setup: tray_icon::menu::MenuId,
    service_action: tray_icon::menu::MenuId,
    logs: tray_icon::menu::MenuId,
    quit: tray_icon::menu::MenuId,
) {
    MenuEvent::set_event_handler(Some(move |event: MenuEvent| {
        let command = if event.id == open {
            Some(TrayCommand::Open)
        } else if event.id == setup {
            Some(TrayCommand::ReviewSetup)
        } else if event.id == service_action {
            Some(TrayCommand::ServiceAction)
        } else if event.id == logs {
            Some(TrayCommand::OpenLogs)
        } else if event.id == quit {
            Some(TrayCommand::Quit)
        } else {
            None
        };
        if let Some(command) = command {
            let _ = sender.try_send(command);
        }
    }));
}

fn install_tray_handler(sender: Sender<TrayCommand>) {
    TrayIconEvent::set_event_handler(Some(move |event: TrayIconEvent| {
        let opens_window = matches!(
            event,
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } | TrayIconEvent::DoubleClick { button: MouseButton::Left, .. }
        );
        if opens_window {
            let _ = sender.try_send(TrayCommand::Open);
        }
    }));
}

fn load_icon() -> anyhow::Result<Icon> {
    let asset = BrandAssets::get("inari-icon-64.png").context("tray icon asset is missing")?;
    let image = image::load_from_memory(&asset.data)
        .context("tray icon asset is invalid")?
        .into_rgba8();
    let (width, height) = image.dimensions();
    Icon::from_rgba(image.into_raw(), width, height).context("tray icon pixels are invalid")
}
