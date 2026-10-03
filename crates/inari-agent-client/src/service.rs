use std::{fmt, io};

use service_manager::{ServiceLabel, ServiceManager, ServiceStartCtx, ServiceStopCtx};
#[cfg(not(windows))]
use service_manager::{ServiceStatus, ServiceStatusCtx};

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
    #[cfg(windows)]
    {
        let _ = manager;
        scm::service_state(&label.to_qualified_name()).map_err(|source| {
            ServiceControlError::Operation { operation: ServiceOperation::Inspect, source }
        })
    }
    #[cfg(not(windows))]
    {
        let status = manager
            .status(ServiceStatusCtx { label: label.clone() })
            .map_err(|source| ServiceControlError::Operation {
                operation: ServiceOperation::Inspect,
                source,
            })?;
        Ok(map_status(status))
    }
}

fn manager() -> ServiceControlResult<Box<dyn ServiceManager>> {
    let manager = <dyn ServiceManager>::native().map_err(ServiceControlError::Manager)?;
    manager
        .available()
        .map_err(ServiceControlError::Manager)?
        .then_some(manager)
        .ok_or(ServiceControlError::ManagerUnavailable)
}

#[cfg(not(windows))]
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

#[cfg(all(test, not(windows)))]
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

/// Reads agent service state straight from the Service Control Manager.
///
/// The cross-platform manager shells out to `sc.exe` and looks for a line
/// starting with `STATE` that contains `RUNNING`. Both words are English, so on
/// a Windows installed in any other language the match never lands and every
/// running service reads back as stopped. That sends operators to restart a
/// service that was healthy all along. The SCM reports a numeric state, which
/// no display language rewrites.
#[cfg(windows)]
mod scm {
    use std::{ffi::OsStr, io, iter, os::windows::ffi::OsStrExt as _, ptr};

    use windows_sys::Win32::{
        Foundation::ERROR_SERVICE_DOES_NOT_EXIST,
        System::Services::{
            CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceStatusEx,
            SC_MANAGER_CONNECT, SC_STATUS_PROCESS_INFO, SERVICE_CONTINUE_PENDING,
            SERVICE_PAUSE_PENDING, SERVICE_PAUSED, SERVICE_QUERY_STATUS, SERVICE_RUNNING,
            SERVICE_START_PENDING, SERVICE_STATUS_PROCESS, SERVICE_STOP_PENDING, SERVICE_STOPPED,
        },
    };

    use crate::ServiceState;

    pub(super) fn service_state(name: &str) -> io::Result<ServiceState> {
        let name = wide(name);
        // SAFETY: every handle opened below is closed on all paths, and the
        // status buffer is a live local for the whole call.
        unsafe {
            let manager = OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT);
            if manager.is_null() {
                return Err(io::Error::last_os_error());
            }
            let service = OpenServiceW(manager, name.as_ptr(), SERVICE_QUERY_STATUS);
            if service.is_null() {
                let error = io::Error::last_os_error();
                CloseServiceHandle(manager);
                // A package that was never installed is a state to report, not
                // a failure to raise.
                if error.raw_os_error() == Some(ERROR_SERVICE_DOES_NOT_EXIST as i32) {
                    return Ok(ServiceState::NotInstalled);
                }
                return Err(error);
            }
            let mut status: SERVICE_STATUS_PROCESS = std::mem::zeroed();
            let mut needed = 0u32;
            let queried = QueryServiceStatusEx(
                service,
                SC_STATUS_PROCESS_INFO,
                (&raw mut status).cast::<u8>(),
                std::mem::size_of::<SERVICE_STATUS_PROCESS>() as u32,
                &raw mut needed,
            );
            let result = if queried == 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(map_current_state(status.dwCurrentState))
            };
            CloseServiceHandle(service);
            CloseServiceHandle(manager);
            result
        }
    }

    fn map_current_state(state: u32) -> ServiceState {
        match state {
            SERVICE_RUNNING => ServiceState::Running,
            SERVICE_START_PENDING | SERVICE_CONTINUE_PENDING => ServiceState::Starting,
            // A paused service does no device work, so it reads the same as a
            // stopped one and offers the same recovery.
            SERVICE_STOPPED | SERVICE_STOP_PENDING | SERVICE_PAUSED | SERVICE_PAUSE_PENDING => {
                ServiceState::Stopped
            },
            _ => ServiceState::Unavailable,
        }
    }

    fn wide(value: &str) -> Vec<u16> {
        OsStr::new(value)
            .encode_wide()
            .chain(iter::once(0))
            .collect()
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn reads_every_service_state_without_reading_english() {
            assert_eq!(map_current_state(SERVICE_RUNNING), ServiceState::Running);
            assert_eq!(map_current_state(SERVICE_START_PENDING), ServiceState::Starting);
            assert_eq!(map_current_state(SERVICE_CONTINUE_PENDING), ServiceState::Starting);
            assert_eq!(map_current_state(SERVICE_STOPPED), ServiceState::Stopped);
            assert_eq!(map_current_state(SERVICE_STOP_PENDING), ServiceState::Stopped);
            assert_eq!(map_current_state(SERVICE_PAUSED), ServiceState::Stopped);
            assert_eq!(map_current_state(SERVICE_PAUSE_PENDING), ServiceState::Stopped);
        }

        #[test]
        fn reports_an_unknown_state_as_unreadable_rather_than_stopped() {
            assert_eq!(map_current_state(u32::MAX), ServiceState::Unavailable);
        }

        #[test]
        fn terminates_the_service_name_for_win32() {
            assert_eq!(wide("InariAgent").last(), Some(&0));
        }
    }
}
