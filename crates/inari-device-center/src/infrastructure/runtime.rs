use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

use inari_agent_client::{
    AgentClient, AgentClientError, AgentClientOptions, AgentClientResult, AgentConnection,
    AgentEvent, Device, DeviceId, EnrollmentPreview, InvitationLink, Job, LocalAgentService,
    LocalIdentityStore, ServiceControlResult, ServiceState, SetupSnapshot,
};
#[cfg(windows)]
use tokio::io::AsyncReadExt as _;
use tokio::{
    runtime::Runtime,
    sync::{broadcast, oneshot},
    task::JoinSet,
    time,
};
use tokio_util::sync::CancellationToken;

#[derive(Clone)]
pub enum AgentRuntimeUpdate {
    Connection(AgentConnection),
    Event(AgentEvent),
    #[cfg(windows)]
    Activation(Option<String>),
}

pub struct AgentRuntime {
    runtime: Mutex<Option<Runtime>>,
    tasks: Mutex<Option<JoinSet<()>>>,
    client: Arc<AgentClient>,
    service: LocalAgentService,
    cancellation: CancellationToken,
    updates: broadcast::Sender<AgentRuntimeUpdate>,
}

impl AgentRuntime {
    pub fn start() -> anyhow::Result<Arc<Self>> {
        let runtime = Runtime::new()?;
        let client = AgentClient::new(AgentClientOptions::default(), LocalIdentityStore)?;
        let (updates, _) = broadcast::channel(128);
        let runtime = Arc::new(Self {
            runtime: Mutex::new(Some(runtime)),
            tasks: Mutex::new(Some(JoinSet::new())),
            client: Arc::new(client),
            service: LocalAgentService::installed(),
            cancellation: CancellationToken::new(),
            updates,
        });
        runtime.start_event_supervisor();
        runtime.start_activation_server();
        Ok(runtime)
    }

    pub fn subscribe(&self) -> broadcast::Receiver<AgentRuntimeUpdate> {
        self.updates.subscribe()
    }

    pub fn setup(&self) -> oneshot::Receiver<SetupSnapshot> {
        self.spawn(|client| async move {
            client
                .setup()
                .await
                .unwrap_or_else(setup_failure)
        })
    }

    pub fn devices(&self) -> oneshot::Receiver<AgentClientResult<Vec<Device>>> {
        self.spawn(|client| async move { client.devices().await })
    }

    pub fn jobs(&self) -> oneshot::Receiver<AgentClientResult<Vec<Job>>> {
        self.spawn(|client| async move { client.jobs().await })
    }

    pub fn preview(
        &self,
        invitation: InvitationLink,
    ) -> oneshot::Receiver<AgentClientResult<EnrollmentPreview>> {
        self.spawn(move |client| async move { client.preview(&invitation).await })
    }

    pub fn begin_setup(
        &self,
        invitation: InvitationLink,
    ) -> oneshot::Receiver<AgentClientResult<SetupSnapshot>> {
        self.spawn(move |client| async move { client.begin_setup(&invitation).await })
    }

    pub fn confirm_devices(
        &self,
        device_ids: Vec<DeviceId>,
    ) -> oneshot::Receiver<AgentClientResult<SetupSnapshot>> {
        self.spawn(move |client| async move { client.confirm_devices(device_ids).await })
    }

    pub fn cancel_setup(&self) -> oneshot::Receiver<AgentClientResult<SetupSnapshot>> {
        self.spawn(|client| async move { client.cancel_setup().await })
    }

    pub fn service_state(&self) -> oneshot::Receiver<ServiceControlResult<ServiceState>> {
        let service = self.service.clone();
        self.spawn_future(async move { service.state().await })
    }

    pub fn start_service(&self) -> oneshot::Receiver<ServiceControlResult<ServiceState>> {
        let service = self.service.clone();
        self.spawn_future(async move { service.start().await })
    }

    pub fn restart_service(&self) -> oneshot::Receiver<ServiceControlResult<ServiceState>> {
        let service = self.service.clone();
        self.spawn_future(async move { service.restart().await })
    }

    fn spawn<T, F, Fut>(&self, operation: F) -> oneshot::Receiver<T>
    where
        T: Send + 'static,
        F: FnOnce(Arc<AgentClient>) -> Fut + Send + 'static,
        Fut: Future<Output = T> + Send + 'static,
    {
        let client = self.client.clone();
        self.spawn_future(operation(client))
    }

    fn spawn_future<T>(
        &self,
        future: impl Future<Output = T> + Send + 'static,
    ) -> oneshot::Receiver<T>
    where
        T: Send + 'static,
    {
        let (sender, receiver) = oneshot::channel();
        let cancellation = self.cancellation.clone();
        self.spawn_owned(async move {
            tokio::select! {
                () = cancellation.cancelled() => {}
                value = future => {
                    let _ = sender.send(value);
                }
            }
        });
        receiver
    }

    fn start_event_supervisor(&self) {
        let client = self.client.clone();
        let cancellation = self.cancellation.clone();
        let updates = self.updates.clone();
        self.spawn_owned(async move {
            let mut attempt = 0_u32;
            loop {
                if cancellation.is_cancelled() {
                    return;
                }

                match client.events().await {
                    Ok(mut stream) => {
                        attempt = 0;
                        let _ = updates
                            .send(AgentRuntimeUpdate::Connection(AgentConnection::Connected));
                        loop {
                            tokio::select! {
                                () = cancellation.cancelled() => return,
                                message = stream.next() => {
                                    match message {
                                        Ok(Some(event)) => {
                                            let _ = updates.send(AgentRuntimeUpdate::Event(event));
                                        },
                                        Ok(None) | Err(_) => break,
                                    }
                                }
                            }
                        }
                    },
                    Err(_) => {
                        let state = if attempt == 0 {
                            AgentConnection::Unavailable
                        } else {
                            AgentConnection::Reconnecting
                        };
                        let _ = updates.send(AgentRuntimeUpdate::Connection(state));
                    },
                }

                attempt = attempt.saturating_add(1);
                let base = 250_u64.saturating_mul(1_u64 << attempt.min(5));
                let jitter = fastrand::u64(0..=base / 3);
                tokio::select! {
                    () = cancellation.cancelled() => return,
                    () = time::sleep(Duration::from_millis((base + jitter).min(10_000))) => {}
                }
            }
        });
    }

    fn start_activation_server(&self) {
        #[cfg(windows)]
        {
            let cancellation = self.cancellation.clone();
            let updates = self.updates.clone();
            self.spawn_owned(async move {
                if let Err(error) = serve_activations(cancellation, updates).await {
                    tracing::warn!(%error, "Device Center activation server stopped");
                }
            });
        }
    }

    fn spawn_owned(&self, future: impl Future<Output = ()> + Send + 'static) {
        let runtime = self
            .runtime
            .lock()
            .expect("runtime lock poisoned");
        let mut tasks = self
            .tasks
            .lock()
            .expect("task lock poisoned");
        if let (Some(runtime), Some(tasks)) = (runtime.as_ref(), tasks.as_mut()) {
            tasks.spawn_on(future, runtime.handle());
        }
    }
}

fn setup_failure(error: AgentClientError) -> SetupSnapshot {
    let guidance = match error {
        AgentClientError::MalformedIdentity => {
            "The protected Device Center identity is damaged. Ask an administrator to reset local trust before trying again."
        },
        AgentClientError::IdentityUnavailable(_) => {
            "Device Center cannot use the protected credential store. Unlock it or ask an administrator for help."
        },
        AgentClientError::IdentityRequired | AgentClientError::PairingUnavailable(_) => {
            "Device Center could not establish protected local trust. Restart the installed agent service, then try again."
        },
        AgentClientError::InvalidResponse(_) => {
            "The installed agent and Device Center do not understand the same local contract. Update the Inari installation, then try again."
        },
        AgentClientError::Unavailable(_)
        | AgentClientError::Rejected
        | AgentClientError::EventStreamUnavailable(_)
        | AgentClientError::InvalidInvitation
        | AgentClientError::EventStreamClosed => {
            "Device Center could not reach the local agent. Start the service, then try again."
        },
    };
    SetupSnapshot::unavailable_with(guidance)
}

#[cfg(windows)]
async fn serve_activations(
    cancellation: CancellationToken,
    updates: broadcast::Sender<AgentRuntimeUpdate>,
) -> std::io::Result<()> {
    use tokio::net::windows::named_pipe::ServerOptions;

    const PIPE: &str = r"\\.\pipe\Inari.DeviceCenter.Activation";
    const MAX_MESSAGE: u64 = 4_097;

    let mut first = true;
    loop {
        let server = ServerOptions::new()
            .access_inbound(true)
            .access_outbound(false)
            .first_pipe_instance(first)
            .create(PIPE)?;
        first = false;
        tokio::select! {
            () = cancellation.cancelled() => return Ok(()),
            result = server.connect() => result?,
        }

        let mut bytes = Vec::new();
        server
            .take(MAX_MESSAGE)
            .read_to_end(&mut bytes)
            .await?;
        let activation = match bytes.split_first() {
            Some((&0, _)) => Some(None),
            Some((&1, invitation)) => std::str::from_utf8(invitation)
                .ok()
                .map(|value| Some(value.to_owned())),
            _ => None,
        };
        if let Some(activation) = activation {
            let _ = updates.send(AgentRuntimeUpdate::Activation(activation));
        }
    }
}

impl Drop for AgentRuntime {
    fn drop(&mut self) {
        self.cancellation.cancel();
        let tasks = self
            .tasks
            .lock()
            .expect("task lock poisoned")
            .take();
        if let Some(runtime) = self
            .runtime
            .lock()
            .expect("runtime lock poisoned")
            .take()
        {
            if let Some(mut tasks) = tasks {
                runtime.block_on(async {
                    let joined = async {
                        while let Some(result) = tasks.join_next().await {
                            if let Err(error) = result {
                                tracing::warn!(%error, "local-agent task ended unexpectedly");
                            }
                        }
                    };
                    if time::timeout(Duration::from_secs(3), joined)
                        .await
                        .is_err()
                    {
                        tasks.abort_all();
                    }
                });
            }
            runtime.shutdown_timeout(Duration::from_secs(3));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use inari_agent_client::SetupAccess;

    #[test]
    fn damaged_identity_fails_closed_with_recovery_guidance() {
        let snapshot = setup_failure(AgentClientError::MalformedIdentity);

        assert_eq!(snapshot.access, SetupAccess::Unknown);
        assert!(
            snapshot
                .guidance
                .as_deref()
                .is_some_and(|guidance| guidance.contains("protected Device Center identity"))
        );
    }

    #[test]
    fn unavailable_agent_never_grants_setup_access() {
        let snapshot = setup_failure(AgentClientError::InvalidInvitation);

        assert_eq!(snapshot.access, SetupAccess::Unknown);
        assert!(snapshot.completed_at.is_none());
    }
}
