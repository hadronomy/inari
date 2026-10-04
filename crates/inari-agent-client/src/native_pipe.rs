use std::{io, time::Duration};

use tokio::{
    io::{AsyncReadExt as _, AsyncWriteExt as _},
    net::windows::named_pipe::{ClientOptions, NamedPipeClient},
    time::{sleep, timeout},
};
use windows_sys::Win32::Foundation::{
    ERROR_BROKEN_PIPE, ERROR_PIPE_BUSY, ERROR_PIPE_NOT_CONNECTED,
};

use crate::{AgentClientError, AgentClientResult};

const PIPE: &str = r"\\.\pipe\Inari.Agent.Pairing";
const RESPONSE_LIMIT: usize = 4_096;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);

pub(crate) async fn request(request: u8) -> AgentClientResult<String> {
    exchange(PIPE, request, REQUEST_TIMEOUT).await
}

async fn exchange(pipe_name: &str, request: u8, deadline: Duration) -> AgentClientResult<String> {
    timeout(deadline, async {
        let mut pipe = timeout(CONNECT_TIMEOUT, connect(pipe_name))
            .await
            .map_err(|_| unavailable_timeout("The Agent bootstrap pipe did not become available."))?
            .map_err(AgentClientError::pairing_unavailable)?;
        pipe.write_all(&[request])
            .await
            .map_err(AgentClientError::pairing_unavailable)?;

        let mut payload = Vec::new();
        let mut chunk = [0u8; 512];
        loop {
            match pipe.read(&mut chunk).await {
                Ok(0) => break,
                Ok(read) => {
                    payload.extend_from_slice(&chunk[..read]);
                    if payload.len() > RESPONSE_LIMIT {
                        return Err(AgentClientError::invalid_response(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "The Agent bootstrap reply exceeded 4 KiB.",
                        )));
                    }
                },
                // The service flushes the reply before disconnecting. Windows can
                // report that disconnect as an error after the full reply arrived.
                Err(error) if is_peer_closed(&error) && !payload.is_empty() => break,
                Err(error) => return Err(AgentClientError::pairing_unavailable(error)),
            }
        }
        if payload.is_empty() {
            return Err(AgentClientError::pairing_unavailable(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "The Agent bootstrap pipe closed without a reply.",
            )));
        }
        String::from_utf8(payload).map_err(AgentClientError::invalid_response)
    })
    .await
    .map_err(|_| unavailable_timeout("The Agent bootstrap pipe did not complete its reply."))?
}

async fn connect(pipe_name: &str) -> io::Result<NamedPipeClient> {
    loop {
        // Identification is sufficient for the service's package-family check.
        // ClientOptions keeps that limit on the server's access to our identity.
        match ClientOptions::new().open(pipe_name) {
            Ok(pipe) => return Ok(pipe),
            Err(error)
                if error.kind() == io::ErrorKind::NotFound
                    || error.raw_os_error() == Some(ERROR_PIPE_BUSY as i32) =>
            {
                sleep(Duration::from_millis(40)).await;
            },
            Err(error) => return Err(error),
        }
    }
}

fn unavailable_timeout(message: &'static str) -> AgentClientError {
    AgentClientError::pairing_unavailable(io::Error::new(io::ErrorKind::TimedOut, message))
}

fn is_peer_closed(error: &io::Error) -> bool {
    matches!(
        error.raw_os_error(),
        Some(code) if code == ERROR_BROKEN_PIPE as i32 || code == ERROR_PIPE_NOT_CONNECTED as i32
    )
}

#[cfg(test)]
mod tests {
    use std::os::windows::io::AsRawHandle as _;

    use tokio::{
        net::windows::named_pipe::{NamedPipeServer, PipeMode, ServerOptions},
        sync::oneshot,
    };
    use windows_sys::Win32::Storage::FileSystem::FlushFileBuffers;

    use super::*;

    fn server(name: &str) -> (String, NamedPipeServer) {
        let pipe_name = format!(r"\\.\pipe\Inari.Tests.{}.{}", std::process::id(), name);
        let pipe = ServerOptions::new()
            .first_pipe_instance(true)
            .pipe_mode(PipeMode::Message)
            .create(&pipe_name)
            .expect("create isolated pipe");
        (pipe_name, pipe)
    }

    async fn flush_and_close(server: NamedPipeServer) -> io::Result<()> {
        tokio::task::spawn_blocking(move || {
            // SAFETY: server owns this live handle until the flush completes.
            let result = unsafe { FlushFileBuffers(server.as_raw_handle().cast()) };
            if result == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        })
        .await
        .expect("join flush")
    }

    #[tokio::test]
    async fn reads_fragmented_replies_before_the_service_disconnects() {
        for request in [1, 2, 3] {
            let (pipe_name, mut pipe) = server(&format!("reply-{request}"));
            let payload = format!(r#"{{"value":"{}"}}"#, "reply".repeat(300));
            let client = exchange(&pipe_name, request, REQUEST_TIMEOUT);
            let service = async {
                pipe.connect().await.expect("connect");
                let mut received = [0];
                pipe.read_exact(&mut received)
                    .await
                    .expect("read request");
                assert_eq!(received, [request]);
                for chunk in payload.as_bytes().chunks(317) {
                    pipe.write_all(chunk)
                        .await
                        .expect("write reply");
                }
                flush_and_close(pipe)
                    .await
                    .expect("flush reply");
            };
            let (response, ()) = tokio::join!(client, service);
            assert_eq!(response.expect("complete response"), payload);
        }
    }

    #[tokio::test]
    async fn rejects_a_reply_larger_than_the_response_limit() {
        let (pipe_name, mut pipe) = server("oversized");
        let client = exchange(&pipe_name, 3, REQUEST_TIMEOUT);
        let service = async {
            pipe.connect().await.expect("connect");
            pipe.read_u8()
                .await
                .expect("read request");
            pipe.write_all(&vec![b'x'; RESPONSE_LIMIT + 1])
                .await
                .expect("write reply");
            let _ = flush_and_close(pipe).await;
        };
        let (response, ()) = tokio::join!(client, service);
        assert!(matches!(response, Err(AgentClientError::InvalidResponse(_))));
    }

    #[tokio::test]
    async fn rejects_a_disconnect_without_a_reply() {
        let (pipe_name, mut pipe) = server("empty");
        let client = exchange(&pipe_name, 3, REQUEST_TIMEOUT);
        let service = async {
            pipe.connect().await.expect("connect");
            pipe.read_u8()
                .await
                .expect("read request");
            pipe.disconnect().expect("disconnect");
        };
        let (response, ()) = tokio::join!(client, service);
        assert!(matches!(response, Err(AgentClientError::PairingUnavailable(_))));
    }

    #[tokio::test]
    async fn a_reply_deadline_closes_the_client_pipe() {
        let (pipe_name, mut pipe) = server("deadline");
        let client = exchange(&pipe_name, 3, Duration::from_millis(100));
        let service = async {
            pipe.connect().await.expect("connect");
            pipe.read_u8()
                .await
                .expect("read request");
            let closed = timeout(Duration::from_secs(2), pipe.read_u8())
                .await
                .expect("client closes at its deadline");
            assert!(closed.is_err());
        };
        let (response, ()) = tokio::join!(client, service);
        let Err(AgentClientError::PairingUnavailable(error)) = response else {
            panic!("expected deadline error");
        };
        assert_eq!(
            error
                .downcast_ref::<io::Error>()
                .expect("I/O error")
                .kind(),
            io::ErrorKind::TimedOut
        );
    }

    #[tokio::test]
    async fn cancellation_closes_the_client_pipe() {
        let (pipe_name, mut pipe) = server("cancellation");
        let (request_seen, cancel) = oneshot::channel();
        let client = async {
            let mut request = Box::pin(exchange(&pipe_name, 3, REQUEST_TIMEOUT));
            tokio::select! {
                response = &mut request => panic!("unexpected reply: {response:?}"),
                result = cancel => result.expect("service received request"),
            }
            drop(request);
        };
        let service = async {
            pipe.connect().await.expect("connect");
            pipe.read_u8()
                .await
                .expect("read request");
            request_seen
                .send(())
                .expect("notify client");
            let closed = timeout(Duration::from_secs(2), pipe.read_u8())
                .await
                .expect("cancelled client closes its pipe");
            assert!(closed.is_err());
        };
        tokio::join!(client, service);
    }

    #[tokio::test]
    async fn a_missing_service_has_a_bounded_connection_wait() {
        let (pipe_name, pipe) = server("absent");
        drop(pipe);
        let response = exchange(&pipe_name, 2, REQUEST_TIMEOUT).await;
        let Err(AgentClientError::PairingUnavailable(error)) = response else {
            panic!("expected connection deadline");
        };
        assert_eq!(
            error
                .downcast_ref::<io::Error>()
                .expect("I/O error")
                .kind(),
            io::ErrorKind::TimedOut
        );
        assert!(
            error
                .to_string()
                .contains("did not become available")
        );
    }

    #[tokio::test]
    async fn access_denied_does_not_retry_the_connection() {
        let pipe_name = format!(r"\\.\pipe\Inari.Tests.{}.denied", std::process::id());
        let _pipe = ServerOptions::new()
            .access_inbound(false)
            .first_pipe_instance(true)
            .create(&pipe_name)
            .expect("create read-only pipe");
        let response =
            timeout(Duration::from_millis(100), exchange(&pipe_name, 2, REQUEST_TIMEOUT))
                .await
                .expect("permission failure returns without a retry");
        let Err(AgentClientError::PairingUnavailable(error)) = response else {
            panic!("expected access denied");
        };
        assert_eq!(
            error
                .downcast_ref::<io::Error>()
                .expect("I/O error")
                .kind(),
            io::ErrorKind::PermissionDenied
        );
    }
}
