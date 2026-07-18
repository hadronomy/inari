use std::{fmt, io};

use service_manager::{
    ServiceLabel, ServiceManager, ServiceStartCtx, ServiceStatus, ServiceStatusCtx, ServiceStopCtx,
};

use crate::ServiceState;

#[derive(Clone, Debug)]
pub struct LocalAgentService {
    label: ServiceLabel,
}

impl LocalAgentService {
    pub fn installed() -> Self {
        Self {
            label: service_label()
                .parse()
                .expect("the built-in Inari service label must be valid"),
        }
    }

    pub async fn state(&self) -> ServiceControlResult<ServiceState> {
        let label = self.label.clone();
        run_blocking(move || inspect(&label)).await
    }

    pub async fn start(&self) -> ServiceControlResult<ServiceState> {
        let label = self.label.clone();
        run_blocking(move || {
            let manager = manager()?;
            manager
                .start(ServiceStartCtx { label: label.clone() })
                .map_err(|source| ServiceControlError::Operation {
                    operation: ServiceOperation::Start,
                    source,
                })?;
            inspect_with(manager.as_ref(), &label)
        })
        .await
    }

    pub async fn stop(&self) -> ServiceControlResult<ServiceState> {
        let label = self.label.clone();
        run_blocking(move || {
            let manager = manager()?;
            manager
                .stop(ServiceStopCtx { label: label.clone() })
                .map_err(|source| ServiceControlError::Operation {
                    operation: ServiceOperation::Stop,
                    source,
                })?;
            inspect_with(manager.as_ref(), &label)
        })
        .await
    }

    pub async fn restart(&self) -> ServiceControlResult<ServiceState> {
        let label = self.label.clone();
        run_blocking(move || {
            let manager = manager()?;
            match inspect_with(manager.as_ref(), &label)? {
                ServiceState::Running => {
                    manager
                        .stop(ServiceStopCtx { label: label.clone() })
                        .map_err(|source| ServiceControlError::Operation {
                            operation: ServiceOperation::Restart,
                            source,
                        })?;
                },
                ServiceState::Stopped => {},
                ServiceState::NotInstalled => return Ok(ServiceState::NotInstalled),
                ServiceState::Checking | ServiceState::Starting | ServiceState::Unavailable => {
                    return Err(ServiceControlError::UnexpectedState);
                },
            }
            manager
                .start(ServiceStartCtx { label: label.clone() })
                .map_err(|source| ServiceControlError::Operation {
                    operation: ServiceOperation::Restart,
                    source,
                })?;
            inspect_with(manager.as_ref(), &label)
        })
        .await
    }
}

pub type ServiceControlResult<T> = Result<T, ServiceControlError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ServiceOperation {
    Inspect,
    Start,
    Stop,
    Restart,
}

impl fmt::Display for ServiceOperation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Inspect => "inspect",
            Self::Start => "start",
            Self::Stop => "stop",
            Self::Restart => "restart",
        })
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ServiceControlError {
    #[error("the native service manager is unavailable")]
    ManagerUnavailable,
    #[error("could not access the native service manager")]
    Manager(#[source] io::Error),
    #[error("could not {operation} the agent service")]
    Operation {
        operation: ServiceOperation,
        #[source]
        source: io::Error,
    },
    #[error("the agent service changed state while the request was running")]
    UnexpectedState,
    #[error("the service operation ended unexpectedly")]
    Worker(#[source] tokio::task::JoinError),
}

async fn run_blocking(
    operation: impl FnOnce() -> ServiceControlResult<ServiceState> + Send + 'static,
) -> ServiceControlResult<ServiceState> {
    tokio::task::spawn_blocking(operation)
        .await
        .map_err(ServiceControlError::Worker)?
}

fn inspect(label: &ServiceLabel) -> ServiceControlResult<ServiceState> {
    let manager = manager()?;
    inspect_with(manager.as_ref(), label)
}

fn inspect_with(
    manager: &dyn ServiceManager,
    label: &ServiceLabel,
) -> ServiceControlResult<ServiceState> {
    let status = manager
        .status(ServiceStatusCtx { label: label.clone() })
        .map_err(|source| ServiceControlError::Operation {
            operation: ServiceOperation::Inspect,
            source,
        })?;
    Ok(map_status(status))
}

fn manager() -> ServiceControlResult<Box<dyn ServiceManager>> {
    let manager = <dyn ServiceManager>::native().map_err(ServiceControlError::Manager)?;
    manager
        .available()
        .map_err(ServiceControlError::Manager)?
        .then_some(manager)
        .ok_or(ServiceControlError::ManagerUnavailable)
}

fn map_status(status: ServiceStatus) -> ServiceState {
    match status {
        ServiceStatus::NotInstalled => ServiceState::NotInstalled,
        ServiceStatus::Running => ServiceState::Running,
        ServiceStatus::Stopped(_) => ServiceState::Stopped,
    }
}

#[cfg(target_os = "windows")]
const fn service_label() -> &'static str {
    "InariAgent"
}

#[cfg(target_os = "linux")]
const fn service_label() -> &'static str {
    "inari"
}

#[cfg(target_os = "macos")]
const fn service_label() -> &'static str {
    "io.inari.service"
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
const fn service_label() -> &'static str {
    "inari"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_native_status_without_losing_not_installed() {
        assert_eq!(map_status(ServiceStatus::NotInstalled), ServiceState::NotInstalled);
        assert_eq!(map_status(ServiceStatus::Running), ServiceState::Running);
        assert_eq!(
            map_status(ServiceStatus::Stopped(Some("manual".into()))),
            ServiceState::Stopped
        );
    }
}
