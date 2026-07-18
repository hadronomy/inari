use std::sync::Arc;

use gpui::{AnyWindowHandle, Context, Task, Window};
#[cfg(windows)]
use inari_agent_client::SetupSnapshot;
use inari_agent_client::{AgentConnection, ServiceControlResult, ServiceState, SetupAccess};

use super::{DeviceCenter, OpenApiReference, OpenLogs};
use crate::infrastructure::{AgentRuntime, AgentRuntimeUpdate, TrayCommand, platform};

impl DeviceCenter {
    pub(super) fn refresh_operational_data(&mut self, cx: &mut Context<Self>) {
        if self.setup.access != SetupAccess::Complete {
            return;
        }

        let devices = self.runtime.devices();
        let jobs = self.runtime.jobs();
        self._data_task = cx.spawn(async move |center, cx| {
            let (devices, jobs) = futures_util::join!(devices, jobs);
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        if let Ok(Ok(devices)) = devices {
                            center
                                .device_directory
                                .update(cx, |directory, cx| {
                                    directory.replace_devices(devices.clone(), cx);
                                });
                            center.devices = devices.into();
                        }
                        if let Ok(Ok(jobs)) = jobs {
                            center.jobs = jobs.into();
                        }
                        cx.notify();
                    })
                    .ok();
            }
        });
    }

    pub(super) fn listen_for_updates(
        runtime: Arc<AgentRuntime>,
        window_handle: AnyWindowHandle,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        let mut updates = runtime.subscribe();
        cx.spawn(async move |center, cx| {
            loop {
                match updates.recv().await {
                    Ok(update) => {
                        let Some(center) = center.upgrade() else {
                            return;
                        };
                        window_handle
                            .update(cx, |_, window, cx| {
                                #[cfg(not(windows))]
                                let _ = window;
                                center.update(cx, |center, cx| {
                                    match update {
                                        AgentRuntimeUpdate::Connection(connection) => {
                                            center.connection = connection;
                                            if let Some(tray) = &center.tray {
                                                tray.set_connection(connection_label(connection));
                                            }
                                            if connection == AgentConnection::Connected
                                                && center.setup.access == SetupAccess::Unknown
                                            {
                                                center._setup_task =
                                                    Self::load_setup(center.runtime.clone(), cx);
                                            }
                                        },
                                        AgentRuntimeUpdate::Event(event) => {
                                            center.events.push(event);
                                            if center.events.len() > 100 {
                                                center.events.remove(0);
                                            }
                                            center.refresh_operational_data(cx);
                                        },
                                        #[cfg(windows)]
                                        AgentRuntimeUpdate::Activation(invitation) => {
                                            platform::show_window(window, cx);
                                            if let Some(invitation) = invitation {
                                                center
                                                    .invitation_input
                                                    .update(cx, |input, cx| {
                                                        input.set_value(invitation, window, cx);
                                                    });
                                                center.setup_forced = true;
                                                center.setup = SetupSnapshot::invitation();
                                                center.preview = None;
                                                center.setup_error = None;
                                            }
                                        },
                                    }
                                    cx.notify();
                                })
                            })
                            .ok();
                    },
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                        let Some(center) = center.upgrade() else {
                            return;
                        };
                        center
                            .update(cx, |center, cx| {
                                center.refresh_operational_data(cx);
                            })
                            .ok();
                    },
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
                }
            }
        })
    }

    pub(super) fn listen_for_tray(
        commands: async_channel::Receiver<TrayCommand>,
        window_handle: AnyWindowHandle,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        cx.spawn(async move |center, cx| {
            while let Ok(command) = commands.recv().await {
                if center.upgrade().is_none() {
                    return;
                }
                match command {
                    TrayCommand::Open | TrayCommand::ReviewSetup => {
                        window_handle
                            .update(cx, |_, window, cx| platform::show_window(window, cx))
                            .ok();
                    },
                    TrayCommand::OpenLogs => open_logs(),
                    TrayCommand::ServiceAction => {
                        let Some(center) = center.upgrade() else {
                            return;
                        };
                        center
                            .update(cx, |center, cx| {
                                center.request_service_action(cx);
                            })
                            .ok();
                    },
                    TrayCommand::Quit => {
                        cx.update(|cx| cx.quit()).ok();
                        return;
                    },
                }
            }
        })
    }

    pub(super) fn open_logs(&mut self, _: &OpenLogs, _: &mut Window, _: &mut Context<Self>) {
        open_logs();
    }

    pub(super) fn load_service_state(
        runtime: Arc<AgentRuntime>,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        let response = runtime.service_state();
        Self::apply_service_response(response, cx)
    }

    pub(super) fn refresh_agent_service(
        &mut self,
        _: &super::RefreshAgentService,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.service_state = ServiceState::Checking;
        self.service_error = None;
        self.update_tray_service_state();
        self._service_task = Self::load_service_state(self.runtime.clone(), cx);
        cx.notify();
    }

    pub(super) fn start_agent_service(
        &mut self,
        _: &super::StartAgentService,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.service_state == ServiceState::Stopped {
            self.begin_service_operation(self.runtime.start_service(), cx);
        }
    }

    pub(super) fn restart_agent_service(
        &mut self,
        _: &super::RestartAgentService,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.service_state == ServiceState::Running {
            self.begin_service_operation(self.runtime.restart_service(), cx);
        }
    }

    fn request_service_action(&mut self, cx: &mut Context<Self>) {
        let response = match self.service_state {
            ServiceState::Stopped => Some(self.runtime.start_service()),
            ServiceState::Running => Some(self.runtime.restart_service()),
            ServiceState::Checking
            | ServiceState::Starting
            | ServiceState::NotInstalled
            | ServiceState::Unavailable => None,
        };
        if let Some(response) = response {
            self.begin_service_operation(response, cx);
        }
    }

    fn begin_service_operation(
        &mut self,
        response: tokio::sync::oneshot::Receiver<ServiceControlResult<ServiceState>>,
        cx: &mut Context<Self>,
    ) {
        self.service_state = ServiceState::Starting;
        self.service_error = None;
        self.update_tray_service_state();
        self._service_task = Self::apply_service_response(response, cx);
        cx.notify();
    }

    fn apply_service_response(
        response: tokio::sync::oneshot::Receiver<ServiceControlResult<ServiceState>>,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        cx.spawn(async move |center, cx| {
            let result = response.await;
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        match result {
                            Ok(Ok(state)) => {
                                center.service_state = state;
                                center.service_error = None;
                                if state == ServiceState::Running
                                    && center.setup.access == SetupAccess::Unknown
                                {
                                    center._setup_task =
                                        Self::load_setup(center.runtime.clone(), cx);
                                }
                            },
                            Ok(Err(error)) => {
                                center.service_state = ServiceState::Unavailable;
                                center.service_error = Some(error.to_string());
                            },
                            Err(_) => {
                                center.service_state = ServiceState::Unavailable;
                                center.service_error =
                                    Some("The service request ended before it completed.".into());
                            },
                        }
                        center.update_tray_service_state();
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    fn update_tray_service_state(&self) {
        if let Some(tray) = &self.tray {
            tray.set_service_state(self.service_state);
        }
    }

    pub(super) fn open_api_reference(
        &mut self,
        _: &OpenApiReference,
        _: &mut Window,
        _: &mut Context<Self>,
    ) {
        if let Err(error) = open::that_detached("http://127.0.0.1:8765/docs") {
            tracing::warn!(%error, "could not open the local API reference");
        }
    }
}

pub(super) fn connection_label(connection: AgentConnection) -> &'static str {
    match connection {
        AgentConnection::Checking => "Checking local agent",
        AgentConnection::Connected => "Agent connected",
        AgentConnection::Reconnecting => "Reconnecting to agent",
        AgentConnection::Unavailable => "Agent unavailable",
    }
}

fn open_logs() {
    let Some(project) = directories::ProjectDirs::from("dev", "Inari", "Inari Device Center")
    else {
        tracing::warn!("could not determine the Device Center log directory");
        return;
    };
    let directory = project.data_local_dir().join("logs");
    if let Err(error) = std::fs::create_dir_all(&directory) {
        tracing::warn!(%error, "could not create the Device Center log directory");
        return;
    }
    if let Err(error) = open::that_detached(directory) {
        tracing::warn!(%error, "could not open the Device Center log directory");
    }
}
