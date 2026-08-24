use std::sync::Arc;

use gpui::{Context, Task, Window};
use inari_agent_client::{DeviceId, InvitationLink, SetupAccess, SetupSnapshot, SetupStage};

use super::{
    BeginSetup, ConfirmDevices, ContinueWithoutDevices, DeviceCenter, PreviewInvitation,
    RetryConnection, StartOver,
};
use crate::infrastructure::{AgentRuntime, SetupResult, agent_failure_message};

impl DeviceCenter {
    pub(super) fn load_invitation_preview(
        runtime: Arc<AgentRuntime>,
        invitation: InvitationLink,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        let response = runtime.preview(invitation);
        cx.spawn(async move |center, cx| {
            let result = response.await;
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        center.setup_working = false;
                        match result {
                            Ok(Ok(preview)) => {
                                center.agent_error = None;
                                center.preview = Some(preview);
                            },
                            Ok(Err(error)) => {
                                center.agent_error = Some(error.to_string());
                                center.setup_error = Some(agent_failure_message(&error).into());
                            },
                            Err(_) => {
                                center.setup_error =
                                    Some("The agent stopped before it replied.".into());
                            },
                        }
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    pub(super) fn load_setup(runtime: Arc<AgentRuntime>, cx: &mut Context<Self>) -> Task<()> {
        Self::apply_setup(runtime.setup(), cx)
    }

    /// Read setup again after clearing the cached identity.
    pub(super) fn retry_setup(runtime: Arc<AgentRuntime>, cx: &mut Context<Self>) -> Task<()> {
        Self::apply_setup(runtime.retry_setup(), cx)
    }

    fn apply_setup(
        response: tokio::sync::oneshot::Receiver<SetupResult>,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        cx.spawn(async move |center, cx| {
            let snapshot = response.await.ok();
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        center.agent_error = snapshot
                            .as_ref()
                            .and_then(|result| result.diagnostic.clone());
                        let snapshot = snapshot
                            .map(|result| result.snapshot)
                            .unwrap_or_else(SetupSnapshot::unavailable);
                        center.setup = if center.setup_forced {
                            SetupSnapshot::invitation()
                        } else {
                            snapshot
                        };
                        center.select_all_setup_devices();
                        if let Some(tray) = &center.tray {
                            tray.set_setup_required(center.setup.access != SetupAccess::Complete);
                        }
                        center.refresh_operational_data(cx);
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    pub(super) fn retry_connection(
        &mut self,
        _: &RetryConnection,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.setup_error = None;
        // Clears the cached identity before reading, so a denied credential
        // store is actually retried rather than answered from the same cache.
        self._setup_task = Self::retry_setup(self.runtime.clone(), cx);
        cx.notify();
    }

    pub(super) fn preview_invitation(
        &mut self,
        _: &PreviewInvitation,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let value = self.invitation_input.read(cx).value();
        let invitation = match InvitationLink::parse(value.as_str()) {
            Ok(invitation) => invitation,
            Err(error) => {
                self.setup_error = Some(error.to_string());
                self.preview = None;
                cx.notify();
                return;
            },
        };
        self.setup_working = true;
        self.setup_error = None;
        self.preview = None;
        self._setup_task = Self::load_invitation_preview(self.runtime.clone(), invitation, cx);
        cx.notify();
    }

    pub(super) fn begin_setup(
        &mut self,
        _: &BeginSetup,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let value = self.invitation_input.read(cx).value();
        let invitation = match InvitationLink::parse(value.as_str()) {
            Ok(invitation) => invitation,
            Err(error) => {
                self.setup_error = Some(error.to_string());
                cx.notify();
                return;
            },
        };
        self.setup_working = true;
        self.setup_error = None;
        self.setup_forced = false;
        self.invitation_input
            .update(cx, |input, cx| {
                input.set_value("", window, cx);
            });
        let response = self.runtime.begin_setup(invitation);
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    pub(super) fn confirm_devices(
        &mut self,
        _: &ConfirmDevices,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let device_ids = self
            .selected_setup_devices
            .iter()
            .cloned()
            .collect();
        self.setup_working = true;
        self.setup_error = None;
        let response = self.runtime.confirm_devices(device_ids);
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    pub(super) fn continue_without_devices(
        &mut self,
        _: &ContinueWithoutDevices,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.setup_working = true;
        self.setup_error = None;
        let response = self.runtime.confirm_devices(Vec::new());
        self._setup_task = Self::apply_setup_response(response, cx);
        cx.notify();
    }

    pub(super) fn start_over(&mut self, _: &StartOver, _: &mut Window, cx: &mut Context<Self>) {
        self.setup_working = true;
        self.setup_error = None;
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
        cx.spawn(async move |center, cx| {
            let result = response.await;
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        center.setup_working = false;
                        match result {
                            Ok(Ok(snapshot)) => {
                                center.agent_error = None;
                                center.setup = snapshot;
                                center.select_all_setup_devices();
                                if let Some(tray) = &center.tray {
                                    tray.set_setup_required(
                                        center.setup.access != SetupAccess::Complete,
                                    );
                                }
                                center.preview = None;
                                center.refresh_operational_data(cx);
                            },
                            Ok(Err(error)) => {
                                center.agent_error = Some(error.to_string());
                                center.setup_error = Some(agent_failure_message(&error).into());
                            },
                            Err(_) => {
                                center.setup_error =
                                    Some("The agent stopped before it replied.".into());
                            },
                        }
                        cx.notify();
                    })
                    .ok();
            }
        })
    }

    fn select_all_setup_devices(&mut self) {
        self.selected_setup_devices = default_setup_device_selection(&self.setup);
    }
}

fn default_setup_device_selection(setup: &SetupSnapshot) -> std::collections::HashSet<DeviceId> {
    if setup.stage == SetupStage::Devices {
        setup
            .devices
            .iter()
            .map(|device| device.id.clone())
            .collect()
    } else {
        std::collections::HashSet::new()
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

        assert_eq!(default_setup_device_selection(&setup), [device_id].into());

        let invitation = SetupSnapshot::invitation();
        assert!(default_setup_device_selection(&invitation).is_empty());
    }
}
