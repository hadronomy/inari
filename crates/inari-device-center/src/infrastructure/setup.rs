use std::time::Duration;

use inari_agent_client::{AgentClientError, AgentClientResult, SetupSnapshot, SetupStage};
use tokio::{
    sync::mpsc,
    time::{self, Instant},
};

#[derive(Debug, thiserror::Error)]
pub enum SetupProgressError {
    #[error(transparent)]
    Agent(#[from] AgentClientError),
    #[cfg(not(windows))]
    #[error(transparent)]
    Service(#[from] inari_agent_client::ServiceControlError),
    #[error("Inari did not finish connecting. Select Check again to read its state.")]
    TimedOut,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum SetupProgressMode {
    Observe,
    Retry,
    AfterRestart,
}

pub(super) async fn follow<F>(
    mut read: impl FnMut() -> F,
    updates: mpsc::Sender<Result<SetupSnapshot, SetupProgressError>>,
    after_restart: bool,
    timeout: Duration,
    interval: Duration,
) where
    F: Future<Output = AgentClientResult<SetupSnapshot>> + Send,
{
    let deadline = Instant::now() + timeout;
    loop {
        if Instant::now() >= deadline {
            let _ = updates
                .send(Err(SetupProgressError::TimedOut))
                .await;
            return;
        }
        let result = time::timeout_at(deadline, read()).await;
        match result {
            Ok(Ok(snapshot)) => {
                let pending = pending(&snapshot, after_restart);
                if updates
                    .send(Ok(snapshot))
                    .await
                    .is_err()
                    || !pending
                {
                    return;
                }
            },
            Ok(Err(error)) if transient(&error) => {},
            Ok(Err(error)) => {
                let _ = updates.send(Err(error.into())).await;
                return;
            },
            Err(_) => {
                let _ = updates
                    .send(Err(SetupProgressError::TimedOut))
                    .await;
                return;
            },
        }
        tokio::select! {
            () = updates.closed() => return,
            () = time::sleep_until((Instant::now() + interval).min(deadline)) => {},
        }
    }
}

pub(crate) fn pending(snapshot: &SetupSnapshot, after_restart: bool) -> bool {
    snapshot.access == inari_agent_client::SetupAccess::Required
        && (snapshot.restart_required && after_restart
            || !snapshot.restart_required
                && matches!(snapshot.stage, SetupStage::Securing | SetupStage::Connecting))
}

fn transient(error: &AgentClientError) -> bool {
    if matches!(error, AgentClientError::Unavailable(_)) {
        return true;
    }
    if let AgentClientError::PairingUnavailable(source) = error {
        let mut source = Some(source.as_ref() as &(dyn std::error::Error + 'static));
        while let Some(error) = source {
            if let Some(error) = error.downcast_ref::<std::io::Error>() {
                return matches!(
                    error.kind(),
                    std::io::ErrorKind::NotFound
                        | std::io::ErrorKind::ConnectionRefused
                        | std::io::ErrorKind::TimedOut
                        | std::io::ErrorKind::WouldBlock
                        | std::io::ErrorKind::BrokenPipe
                );
            }
            source = error.source();
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use inari_agent_client::SetupAccess;
    use std::collections::VecDeque;

    fn snapshot(stage: SetupStage, restart_required: bool) -> SetupSnapshot {
        SetupSnapshot { stage, restart_required, ..SetupSnapshot::invitation() }
    }

    #[tokio::test]
    async fn restart_progress_reaches_device_selection_without_unlocking_setup() {
        let mut snapshots = VecDeque::from([
            snapshot(SetupStage::Securing, true),
            snapshot(SetupStage::Securing, false),
            snapshot(SetupStage::Connecting, false),
            snapshot(SetupStage::Devices, false),
        ]);
        let (sender, mut receiver) = mpsc::channel(8);
        follow(
            || std::future::ready(Ok(snapshots.pop_front().unwrap())),
            sender,
            true,
            Duration::from_secs(1),
            Duration::ZERO,
        )
        .await;
        let mut received = Vec::new();
        while let Some(result) = receiver.recv().await {
            received.push(result.unwrap());
        }
        assert_eq!(received.len(), 4);
        assert!(received[0].restart_required);
        assert_eq!(received.last().unwrap().stage, SetupStage::Devices);
        assert!(
            received
                .iter()
                .all(|snapshot| snapshot.access == SetupAccess::Required)
        );
    }

    #[tokio::test]
    async fn a_missing_pipe_during_restart_can_recover() {
        let mut attempts = VecDeque::from([
            Err(AgentClientError::PairingUnavailable(Box::new(std::io::Error::from(
                std::io::ErrorKind::NotFound,
            )))),
            Ok(snapshot(SetupStage::Devices, false)),
        ]);
        let (sender, mut receiver) = mpsc::channel(4);
        follow(
            || std::future::ready(attempts.pop_front().unwrap()),
            sender,
            true,
            Duration::from_secs(1),
            Duration::ZERO,
        )
        .await;
        assert_eq!(
            receiver
                .recv()
                .await
                .unwrap()
                .unwrap()
                .stage,
            SetupStage::Devices
        );
        assert!(receiver.recv().await.is_none());
    }

    #[tokio::test]
    async fn identity_errors_stop_without_an_automatic_credential_retry() {
        let mut attempts = 0;
        let (sender, mut receiver) = mpsc::channel(4);
        follow(
            || {
                attempts += 1;
                std::future::ready(Err(AgentClientError::IdentityLocked("locked".into())))
            },
            sender,
            true,
            Duration::from_secs(1),
            Duration::ZERO,
        )
        .await;
        assert!(matches!(
            receiver.recv().await.unwrap(),
            Err(SetupProgressError::Agent(AgentClientError::IdentityLocked(_)))
        ));
        assert!(receiver.recv().await.is_none());
        assert_eq!(attempts, 1);
    }

    #[tokio::test]
    async fn readiness_timeout_does_not_report_setup_completion() {
        let (sender, mut receiver) = mpsc::channel(4);
        follow(std::future::pending, sender, true, Duration::from_millis(5), Duration::ZERO).await;
        assert!(matches!(receiver.recv().await.unwrap(), Err(SetupProgressError::TimedOut)));
        assert!(receiver.recv().await.is_none());
    }

    #[tokio::test]
    async fn restart_requirement_waits_for_the_operator() {
        let (sender, mut receiver) = mpsc::channel(4);
        follow(
            || std::future::ready(Ok(snapshot(SetupStage::Securing, true))),
            sender,
            false,
            Duration::from_secs(1),
            Duration::ZERO,
        )
        .await;
        assert!(
            receiver
                .recv()
                .await
                .unwrap()
                .unwrap()
                .restart_required
        );
        assert!(receiver.recv().await.is_none());
    }

    #[tokio::test]
    async fn denied_pipe_access_stops_without_repeating_the_request() {
        let mut attempts = 0;
        let (sender, mut receiver) = mpsc::channel(4);
        follow(
            || {
                attempts += 1;
                std::future::ready(Err(AgentClientError::PairingUnavailable(Box::new(
                    std::io::Error::from(std::io::ErrorKind::PermissionDenied),
                ))))
            },
            sender,
            true,
            Duration::from_secs(1),
            Duration::ZERO,
        )
        .await;
        assert!(matches!(receiver.recv().await.unwrap(), Err(SetupProgressError::Agent(_))));
        assert_eq!(attempts, 1);
        assert!(receiver.recv().await.is_none());
    }

    #[tokio::test]
    async fn repeated_pending_snapshots_end_at_the_deadline() {
        let (sender, mut receiver) = mpsc::channel(4);
        let follow = follow(
            || std::future::ready(Ok(snapshot(SetupStage::Securing, true))),
            sender,
            true,
            Duration::from_millis(10),
            Duration::from_millis(1),
        );
        let receive = async move {
            let mut received = 0;
            while let Some(result) = receiver.recv().await {
                match result {
                    Ok(snapshot) => {
                        received += 1;
                        assert_eq!(snapshot.access, SetupAccess::Required);
                    },
                    Err(SetupProgressError::TimedOut) => {
                        assert!(received > 0);
                        assert!(receiver.recv().await.is_none());
                        return;
                    },
                    Err(error) => panic!("unexpected error: {error}"),
                }
            }
            panic!("pending progress ended without a timeout");
        };
        tokio::time::timeout(Duration::from_secs(1), async {
            tokio::join!(follow, receive);
        })
        .await
        .unwrap();
    }
}
