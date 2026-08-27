//! What the operations shell still needs to know about enrollment.
//!
//! Driving enrollment belongs to the onboarding window, which owns the
//! invitation field and the stage machine. This window only reads the result:
//! the tray badge and the Overview guidance both depend on whether setup is
//! complete, so the snapshot is loaded here too rather than passed across a
//! window boundary that may not exist yet.

use std::sync::Arc;

use gpui::{Context, Task};
use inari_agent_client::{SetupAccess, SetupSnapshot};

use super::DeviceCenter;
use crate::infrastructure::{AgentRuntime, SetupResult};

impl DeviceCenter {
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
                        center.identity_retry_available = snapshot
                            .as_ref()
                            .is_some_and(|result| result.identity_retry_available);
                        center.agent_error = snapshot
                            .as_ref()
                            .and_then(|result| result.diagnostic.clone());
                        center.setup = snapshot
                            .map(|result| result.snapshot)
                            .unwrap_or_else(SetupSnapshot::unavailable);
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
}
