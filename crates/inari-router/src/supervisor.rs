use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use ed25519_dalek::VerifyingKey;
use serde::{Deserialize, Serialize};
use tokio::process::{Child, Command};
use tokio::sync::{mpsc, oneshot, watch};
use tokio::time::Instant;

use crate::policy::literal_name;
use crate::tls::DataTls;
use crate::{PolicyStore, RouterConfig, RouterError, RouterResult, SignedPolicy, VerifiedPolicy};

const START_TIMEOUT: Duration = Duration::from_secs(10);
const STOP_TIMEOUT: Duration = Duration::from_secs(3);
const TLS_CHECK_INTERVAL: Duration = Duration::from_secs(1);

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RouterStatus {
    pub generation: Option<u64>,
    pub digest: Option<String>,
    pub expires_at: Option<DateTime<Utc>>,
    pub ready: bool,
    pub message: String,
}

impl RouterStatus {
    #[must_use]
    pub fn is_current(&self) -> bool {
        self.ready
            && self
                .expires_at
                .is_some_and(|expires| expires > Utc::now())
    }
}

#[derive(Clone, Debug)]
pub struct SupervisorHandle {
    commands: mpsc::Sender<Apply>,
    status: watch::Receiver<RouterStatus>,
}

impl SupervisorHandle {
    /// Cancellation of the caller does not interrupt an accepted policy update.
    pub async fn apply(&self, policy: SignedPolicy) -> RouterResult<RouterStatus> {
        let (reply, result) = oneshot::channel();
        self.commands
            .try_send(Apply { policy, reply })
            .map_err(|_| RouterError::Unavailable)?;
        result
            .await
            .map_err(|_| RouterError::Unavailable)?
    }

    #[must_use]
    pub fn status(&self) -> RouterStatus {
        let mut status = self.status.borrow().clone();
        status.ready = status.is_current();
        status
    }
}

#[derive(Debug)]
struct Apply {
    policy: SignedPolicy,
    reply: oneshot::Sender<RouterResult<RouterStatus>>,
}

pub struct RouterSupervisor {
    store: Arc<PolicyStore>,
    key: VerifyingKey,
    fleet_id: String,
    executable: PathBuf,
    config: RouterConfig,
    policy: Option<VerifiedPolicy>,
    child: Option<Child>,
    tls: Option<DataTls>,
    next_tls_check: Instant,
    commands: mpsc::Receiver<Apply>,
    status: watch::Sender<RouterStatus>,
}

impl RouterSupervisor {
    pub fn new(
        store: PolicyStore,
        key: VerifyingKey,
        fleet_id: String,
        executable: PathBuf,
        config: RouterConfig,
    ) -> RouterResult<(SupervisorHandle, Self)> {
        config.validate()?;
        if key.is_weak() {
            return Err(RouterError::InvalidSignature);
        }
        if !literal_name(&fleet_id) {
            return Err(RouterError::InvalidPolicy("fleet_id must be a literal name".into()));
        }
        let policy = store.load(&key, &fleet_id)?;
        let initial = RouterStatus {
            generation: policy
                .as_ref()
                .map(|policy| policy.policy().generation),
            digest: policy
                .as_ref()
                .map(|policy| policy.digest().to_owned()),
            expires_at: policy
                .as_ref()
                .map(|policy| policy.policy().expires_at),
            ready: false,
            message: "Router awaits a current signed policy.".into(),
        };
        let (status, receiver) = watch::channel(initial);
        let (sender, commands) = mpsc::channel(16);
        let handle = SupervisorHandle { commands: sender, status: receiver };
        Ok((
            handle,
            Self {
                store: Arc::new(store),
                key,
                fleet_id,
                executable,
                config,
                policy,
                child: None,
                tls: None,
                next_tls_check: Instant::now(),
                commands,
                status,
            },
        ))
    }

    pub async fn run(mut self, mut shutdown: watch::Receiver<bool>) -> RouterResult<()> {
        if *shutdown.borrow() {
            return Ok(());
        }
        if let Some(policy) = self.policy.clone()
            && policy
                .require_current(Utc::now())
                .is_ok()
            && let Err(error) = self.activate(policy).await
        {
            self.not_ready(error.to_string());
        }
        let mut ticks = tokio::time::interval(Duration::from_millis(250));
        ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                biased;
                _ = shutdown.changed() => break,
                _ = ticks.tick() => self.observe().await?,
                command = self.commands.recv() => {
                    let Some(command) = command else { break };
                    let result = self.apply(command.policy).await;
                    let _ = command.reply.send(result);
                }
            }
        }
        self.not_ready("Router Supervisor is stopping.".into());
        self.stop().await
    }

    async fn apply(&mut self, signed: SignedPolicy) -> RouterResult<RouterStatus> {
        let verified = signed.verify(&self.key, &self.fleet_id)?;
        verified.require_current(Utc::now())?;
        if let Some(previous) = &self.policy
            && !verified.advances(previous)?
            && self.status.borrow().ready
        {
            self.observe().await?;
            if self.status.borrow().ready {
                return Ok(self.status.borrow().clone());
            }
        }
        self.observe().await?;
        if self.status.borrow().ready
            && self
                .policy
                .as_ref()
                .is_some_and(|previous| verified.has_same_authority(previous))
        {
            self.refresh(verified).await?;
        } else {
            self.activate(verified).await?;
        }
        Ok(self.status.borrow().clone())
    }

    async fn refresh(&mut self, policy: VerifiedPolicy) -> RouterResult<()> {
        let previous = self
            .policy
            .clone()
            .ok_or(RouterError::Unavailable)?;
        let store = self.store.clone();
        let durable = policy.clone();
        // Retain the higher generation even if storage fails after its rename.
        self.policy = Some(policy.clone());
        let mut write = tokio::task::spawn_blocking(move || store.persist(&durable));
        let mut stop_failure = None;
        let mut ticks = tokio::time::interval(Duration::from_millis(250));
        ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let result = loop {
            tokio::select! {
                biased;
                _ = ticks.tick() => {
                    // Slow storage cannot extend the authority of the running Router.
                    if previous.require_current(Utc::now()).is_err()
                        || self.tls.as_ref().is_some_and(|tls| tls.require_current(Utc::now()).is_err())
                    {
                        self.not_ready("Router policy or TLS certificate expired during refresh.".into());
                        if let Err(error) = self.stop().await {
                            stop_failure = Some(error);
                        }
                    }
                },
                result = &mut write => break result,
            }
        };
        if let Some(error) = stop_failure {
            return Err(error);
        }
        let result = result
            .map_err(|error| RouterError::NotReady(error.to_string()))
            .and_then(|result| result);
        if let Err(error) = result {
            self.not_ready(error.to_string());
            self.stop().await?;
            return Err(error);
        }
        self.observe().await?;
        if previous
            .require_current(Utc::now())
            .is_err()
            || !self.status.borrow().ready
        {
            return self.activate(policy).await;
        }
        policy.require_current(Utc::now())?;
        self.status.send_modify(|status| {
            status.generation = Some(policy.policy().generation);
            status.digest = Some(policy.digest().to_owned());
            status.expires_at = Some(policy.policy().expires_at);
            status.message =
                "Router refreshed the acknowledged policy without closing links.".into();
        });
        Ok(())
    }

    async fn activate(&mut self, policy: VerifiedPolicy) -> RouterResult<()> {
        let mut config = self.config.render(&policy)?;
        self.not_ready("Router applies signed policy.".into());
        // All old links close before a new ACL can become active.
        self.stop().await?;
        let store = self.store.clone();
        self.policy = Some(policy.clone());
        let durable = policy.clone();
        tokio::task::spawn_blocking(move || store.persist(&durable))
            .await
            .map_err(|error| RouterError::NotReady(error.to_string()))??;
        self.policy = Some(policy.clone());
        self.status.send_modify(|status| {
            status.generation = Some(policy.policy().generation);
            status.digest = Some(policy.digest().to_owned());
            status.expires_at = Some(policy.policy().expires_at);
        });
        let tls = DataTls::load(&self.config).await?;
        tls.configure(&mut config)?;
        let store = self.store.clone();
        let bytes = zeroize::Zeroizing::new(serde_json::to_vec(&config)?);
        tokio::task::spawn_blocking(move || store.write("router.json", &bytes))
            .await
            .map_err(|error| RouterError::NotReady(error.to_string()))??;
        policy.require_current(Utc::now())?;
        let child = Command::new(&self.executable)
            .arg("--config")
            .arg(self.store.config_path())
            // zenohd enables both surfaces after it reads the configuration file.
            .args(["--cfg", "adminspace/enabled:false", "--cfg", "plugins_loading/enabled:false"])
            .stdin(std::process::Stdio::null())
            .kill_on_drop(true)
            .spawn()?;
        self.child = Some(child);
        self.next_tls_check = Instant::now() + TLS_CHECK_INTERVAL;
        self.tls = Some(tls);
        let remaining = policy
            .policy()
            .expires_at
            .min(
                self.tls
                    .as_ref()
                    .ok_or(RouterError::Unavailable)?
                    .expires_at(),
            )
            .signed_duration_since(Utc::now())
            .to_std()
            .map_err(|_| RouterError::NotCurrent)?;
        let result = self
            .wait_ready(Instant::now() + START_TIMEOUT.min(remaining))
            .await;
        match result {
            Ok(()) => {
                policy.require_current(Utc::now())?;
                self.tls
                    .as_ref()
                    .ok_or(RouterError::Unavailable)?
                    .require_current(Utc::now())?;
                self.status.send_modify(|status| {
                    status.ready = true;
                    status.message = "Router is ready with the acknowledged policy.".into();
                });
                Ok(())
            },
            Err(error) => {
                self.not_ready(error.to_string());
                self.stop().await?;
                Err(error)
            },
        }
    }

    async fn wait_ready(&mut self, deadline: Instant) -> RouterResult<()> {
        let mut last_failure = "The mTLS probe has not connected.".to_owned();
        loop {
            if Instant::now() >= deadline {
                return Err(RouterError::NotReady(format!(
                    "Zenoh readiness deadline passed: {last_failure}"
                )));
            }
            let Some(child) = self.child.as_mut() else {
                return Err(RouterError::NotReady("Router process is absent.".into()));
            };
            if let Some(exit) = child.try_wait()? {
                return Err(RouterError::NotReady(format!("Router process exited: {exit}")));
            }
            let tls = self
                .tls
                .as_ref()
                .ok_or(RouterError::Unavailable)?;
            tls.require_current(Utc::now())?;
            let config = tls.probe_config(&self.config.probe_endpoint)?;
            let probe_deadline = deadline.min(Instant::now() + Duration::from_secs(2));
            match tokio::time::timeout_at(probe_deadline, zenoh::open(config)).await {
                Ok(Ok(session)) => {
                    let matches = session
                        .info()
                        .routers_zid()
                        .await
                        .any(|id| {
                            id.to_string()
                                .eq_ignore_ascii_case(self.config.id.trim_start_matches('0'))
                        });
                    tokio::time::timeout_at(deadline, session.close())
                        .await
                        .map_err(|_| RouterError::NotReady("The mTLS probe did not close.".into()))?
                        .map_err(|error| RouterError::NotReady(error.to_string()))?;
                    if matches && child.try_wait()?.is_none() {
                        return Ok(());
                    }
                    last_failure =
                        "The connected Router identity did not match configuration.".into();
                },
                Ok(Err(error)) => last_failure = error.to_string(),
                Err(_) => last_failure = "The mTLS connection probe timed out.".into(),
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }

    async fn observe(&mut self) -> RouterResult<()> {
        if self
            .policy
            .as_ref()
            .is_some_and(|policy| {
                policy
                    .require_current(Utc::now())
                    .is_err()
            })
        {
            self.not_ready("Router policy expired.".into());
            self.stop().await?;
        } else if let Some(child) = self.child.as_mut()
            && let Some(exit) = child.try_wait()?
        {
            self.child = None;
            self.tls = None;
            self.not_ready(format!("Router process exited: {exit}"));
        } else if self.child.is_some() {
            if let Some(tls) = &self.tls
                && let Err(error) = tls.require_current(Utc::now())
            {
                self.not_ready(error.to_string());
                self.stop().await?;
                return Ok(());
            }
            if Instant::now() >= self.next_tls_check {
                self.next_tls_check = Instant::now() + TLS_CHECK_INTERVAL;
                let material = match tokio::time::timeout(
                    Duration::from_millis(500),
                    DataTls::load(&self.config),
                )
                .await
                .map_err(|_| RouterError::NotReady("TLS credential read timed out.".into()))
                .and_then(|result| result)
                {
                    Ok(material) => material,
                    Err(error) => {
                        self.not_ready(error.to_string());
                        self.stop().await?;
                        return Ok(());
                    },
                };
                if self
                    .tls
                    .as_ref()
                    .is_none_or(|tls| !tls.matches(&material))
                {
                    let policy = self
                        .policy
                        .clone()
                        .ok_or(RouterError::Unavailable)?;
                    if let Err(error) = self.activate(policy).await {
                        self.not_ready(error.to_string());
                    }
                }
            }
        } else if Instant::now() >= self.next_tls_check
            && let Some(policy) = self.policy.clone()
            && policy
                .require_current(Utc::now())
                .is_ok()
        {
            self.next_tls_check = Instant::now() + TLS_CHECK_INTERVAL;
            if let Err(error) = self.activate(policy).await {
                self.not_ready(error.to_string());
            }
        }
        Ok(())
    }

    fn not_ready(&self, message: String) {
        self.status.send_modify(|status| {
            status.ready = false;
            status.message = message;
        });
    }

    async fn stop(&mut self) -> RouterResult<()> {
        self.tls = None;
        let Some(mut child) = self.child.take() else { return Ok(()) };
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        #[cfg(unix)]
        if let Some(id) = child
            .id()
            .and_then(|id| i32::try_from(id).ok())
        {
            let _ = nix::sys::signal::kill(
                nix::unistd::Pid::from_raw(id),
                nix::sys::signal::Signal::SIGTERM,
            );
        }
        #[cfg(not(unix))]
        child.start_kill()?;
        match tokio::time::timeout(STOP_TIMEOUT, child.wait()).await {
            Ok(result) => {
                result?;
            },
            Err(_) => {
                tokio::time::timeout(STOP_TIMEOUT, child.kill())
                    .await
                    .map_err(|_| RouterError::NotReady("Router process did not stop.".into()))??;
            },
        }
        Ok(())
    }
}
