use std::sync::Arc;

use gpui::{Context, Task, Window};
use inari_agent_client::{InvitationLink, SetupAccess, SetupSnapshot};

use super::{
    BeginSetup, ConfirmDevices, ContinueWithoutDevices, DeviceCenter, PreviewInvitation,
    RetryConnection, StartOver,
};
use crate::infrastructure::AgentRuntime;

impl DeviceCenter {
    pub(super) fn load_setup(runtime: Arc<AgentRuntime>, cx: &mut Context<Self>) -> Task<()> {
        let response = runtime.setup();
        cx.spawn(async move |center, cx| {
            let snapshot = response
                .await
                .unwrap_or_else(|_| SetupSnapshot::unavailable());
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        center.setup = if center.setup_forced {
                            SetupSnapshot::invitation()
                        } else {
                            snapshot
                        };
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
        self._setup_task = Self::load_setup(self.runtime.clone(), cx);
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
        let response = self.runtime.preview(invitation);
        self._setup_task = cx.spawn(async move |center, cx| {
            let result = response.await;
            if let Some(center) = center.upgrade() {
                center
                    .update(cx, |center, cx| {
                        center.setup_working = false;
                        match result {
                            Ok(Ok(preview)) => center.preview = Some(preview),
                            Ok(Err(error)) => center.setup_error = Some(error.to_string()),
                            Err(_) => {
                                center.setup_error =
                                    Some("The local agent stopped before it could reply.".into());
                            },
                        }
                        cx.notify();
                    })
                    .ok();
            }
        });
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
            .setup
            .devices
            .iter()
            .map(|device| device.id.clone())
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
                                center.setup = snapshot;
                                if let Some(tray) = &center.tray {
                                    tray.set_setup_required(
                                        center.setup.access != SetupAccess::Complete,
                                    );
                                }
                                center.preview = None;
                                center.refresh_operational_data(cx);
                            },
                            Ok(Err(error)) => center.setup_error = Some(error.to_string()),
                            Err(_) => {
                                center.setup_error =
                                    Some("The local agent stopped before it could reply.".into());
                            },
                        }
                        cx.notify();
                    })
                    .ok();
            }
        })
    }
}
