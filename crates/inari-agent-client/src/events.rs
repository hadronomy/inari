use async_tungstenite::{
    WebSocketStream,
    tokio::{ConnectStream, client_async_tls_with_config},
    tungstenite::{
        client::IntoClientRequest as _,
        http::{HeaderValue, header::AUTHORIZATION},
    },
};
use chrono::{DateTime, Utc};
use futures_util::StreamExt as _;
use secrecy::{ExposeSecret as _, SecretString};
use serde::Deserialize;
use url::Url;

use crate::{AgentClientError, AgentClientResult, DeviceId, JobId};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EventResource {
    Device(DeviceId),
    Job(JobId),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgentEventKind {
    DeviceConnected,
    DeviceDisconnected,
    DeviceUpdated,
    JobQueued,
    JobDispatched,
    JobRunning,
    JobSucceeded,
    JobFailed,
    JobCancelled,
    JobRecovered,
    JobRetryScheduled,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgentEvent {
    pub sequence: u64,
    pub occurred_at: DateTime<Utc>,
    pub resource: EventResource,
    pub kind: AgentEventKind,
    pub summary: String,
}

pub struct AgentEventStream {
    socket: WebSocketStream<ConnectStream>,
}

impl AgentEventStream {
    pub(crate) async fn connect(endpoint: &Url, token: &SecretString) -> AgentClientResult<Self> {
        let mut stream_url = endpoint
            .join("events")
            .map_err(AgentClientError::invalid_response)?;
        let scheme = match stream_url.scheme() {
            "http" => "ws",
            "https" => "wss",
            _ => return Err(AgentClientError::EventStreamClosed),
        };
        stream_url
            .set_scheme(scheme)
            .map_err(|()| AgentClientError::EventStreamClosed)?;

        let mut request = stream_url
            .as_str()
            .into_client_request()
            .map_err(AgentClientError::EventStreamUnavailable)?;
        let mut authorization = HeaderValue::from_str(&format!("Bearer {}", token.expose_secret()))
            .map_err(AgentClientError::invalid_response)?;
        authorization.set_sensitive(true);
        request
            .headers_mut()
            .insert(AUTHORIZATION, authorization);

        let stream =
            tokio::net::TcpStream::connect(crate::local_transport::socket_address(endpoint)?)
                .await
                .map_err(|error| AgentClientError::EventStreamUnavailable(error.into()))?;
        let (socket, _) = client_async_tls_with_config(request, stream, None)
            .await
            .map_err(AgentClientError::EventStreamUnavailable)?;
        Ok(Self { socket })
    }

    pub async fn next(&mut self) -> AgentClientResult<Option<AgentEvent>> {
        while let Some(message) = self.socket.next().await {
            let message = message.map_err(AgentClientError::EventStreamUnavailable)?;
            if message.is_close() {
                return Ok(None);
            }
            let Some(payload) = message.to_text().ok() else {
                continue;
            };
            match serde_json::from_str::<LiveMessage>(payload)
                .map_err(AgentClientError::invalid_response)?
            {
                LiveMessage::Snapshot => {},
                LiveMessage::EventUpdate { event } => return event.try_into().map(Some),
            }
        }
        Ok(None)
    }
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum LiveMessage {
    Snapshot,
    EventUpdate { event: WireEvent },
}

#[derive(Deserialize)]
struct WireEvent {
    sequence: u64,
    resource_kind: WireResourceKind,
    resource_id: String,
    event_type: WireEventKind,
    occurred_at: DateTime<Utc>,
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum WireResourceKind {
    Device,
    Job,
}

#[derive(Clone, Copy, Deserialize)]
enum WireEventKind {
    #[serde(rename = "device.connected")]
    DeviceConnected,
    #[serde(rename = "device.disconnected")]
    DeviceDisconnected,
    #[serde(rename = "device.updated")]
    DeviceUpdated,
    #[serde(rename = "job.queued")]
    JobQueued,
    #[serde(rename = "job.dispatched")]
    JobDispatched,
    #[serde(rename = "job.running")]
    JobRunning,
    #[serde(rename = "job.succeeded")]
    JobSucceeded,
    #[serde(rename = "job.failed")]
    JobFailed,
    #[serde(rename = "job.cancelled")]
    JobCancelled,
    #[serde(rename = "job.recovered")]
    JobRecovered,
    #[serde(rename = "job.retry_scheduled")]
    JobRetryScheduled,
}

impl TryFrom<WireEvent> for AgentEvent {
    type Error = AgentClientError;

    fn try_from(event: WireEvent) -> Result<Self, Self::Error> {
        let resource = match event.resource_kind {
            WireResourceKind::Device => EventResource::Device(
                DeviceId::parse(event.resource_id).map_err(AgentClientError::invalid_response)?,
            ),
            WireResourceKind::Job => EventResource::Job(
                JobId::parse(event.resource_id).map_err(AgentClientError::invalid_response)?,
            ),
        };
        let (kind, summary) = match event.event_type {
            WireEventKind::DeviceConnected => (AgentEventKind::DeviceConnected, "Device connected"),
            WireEventKind::DeviceDisconnected => {
                (AgentEventKind::DeviceDisconnected, "Device disconnected")
            },
            WireEventKind::DeviceUpdated => (AgentEventKind::DeviceUpdated, "Device updated"),
            WireEventKind::JobQueued => (AgentEventKind::JobQueued, "Job queued"),
            WireEventKind::JobDispatched => (AgentEventKind::JobDispatched, "Job dispatched"),
            WireEventKind::JobRunning => (AgentEventKind::JobRunning, "Job running"),
            WireEventKind::JobSucceeded => (AgentEventKind::JobSucceeded, "Job completed"),
            WireEventKind::JobFailed => (AgentEventKind::JobFailed, "Job failed"),
            WireEventKind::JobCancelled => (AgentEventKind::JobCancelled, "Job cancelled"),
            WireEventKind::JobRecovered => {
                (AgentEventKind::JobRecovered, "Job recovered after restart")
            },
            WireEventKind::JobRetryScheduled => {
                (AgentEventKind::JobRetryScheduled, "Job retry scheduled")
            },
        };
        Ok(Self {
            sequence: event.sequence,
            occurred_at: event.occurred_at,
            resource,
            kind,
            summary: summary.into(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    #[allow(
        clippy::result_large_err,
        reason = "Tungstenite fixes the handshake callback error type."
    )]
    async fn event_stream_uses_loopback_and_keeps_the_endpoint_authority() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        let (sent, received) = tokio::sync::oneshot::channel();
        let server = tokio::spawn(async move {
            let (stream, peer) = listener.accept().await.unwrap();
            assert!(peer.ip().is_loopback());
            let mut socket = async_tungstenite::tokio::accept_hdr_async(
                stream,
                move |request: &async_tungstenite::tungstenite::handshake::server::Request,
                      response| {
                    assert_eq!(request.uri().path(), "/events");
                    assert_eq!(request.headers()[AUTHORIZATION], "Bearer event-fixture-token");
                    sent.send(
                        request.headers()["host"]
                            .to_str()
                            .unwrap()
                            .to_owned(),
                    )
                    .unwrap();
                    Ok(response)
                },
            )
            .await
            .unwrap();
            socket
                .send(async_tungstenite::tungstenite::Message::Close(None))
                .await
                .unwrap();
        });
        let endpoint = format!("http://agent.fixture.invalid:{port}/")
            .parse()
            .unwrap();
        let mut events =
            AgentEventStream::connect(&endpoint, &SecretString::from("event-fixture-token"))
                .await
                .unwrap();
        assert_eq!(received.await.unwrap(), format!("agent.fixture.invalid:{port}"));
        assert!(events.next().await.unwrap().is_none());
        server.await.unwrap();
    }

    #[test]
    fn event_fixture_maps_every_runtime_event_into_curated_domain_types() {
        let messages: Vec<LiveMessage> =
            serde_json::from_str(include_str!("../../../contracts/local-agent.events.json"))
                .expect("fixture is valid");
        let events = messages
            .into_iter()
            .map(|message| {
                let LiveMessage::EventUpdate { event } = message else {
                    panic!("expected event update");
                };
                AgentEvent::try_from(event).expect("event maps")
            })
            .collect::<Vec<_>>();

        assert_eq!(
            events
                .iter()
                .map(|event| event.kind)
                .collect::<Vec<_>>(),
            vec![
                AgentEventKind::DeviceConnected,
                AgentEventKind::DeviceDisconnected,
                AgentEventKind::DeviceUpdated,
                AgentEventKind::JobQueued,
                AgentEventKind::JobDispatched,
                AgentEventKind::JobRunning,
                AgentEventKind::JobSucceeded,
                AgentEventKind::JobFailed,
                AgentEventKind::JobCancelled,
                AgentEventKind::JobRecovered,
                AgentEventKind::JobRetryScheduled,
            ]
        );
        assert_eq!(
            events[0].resource,
            EventResource::Device(DeviceId::parse("dev_front_desk").expect("valid device id"))
        );
        assert_eq!(
            events[3].resource,
            EventResource::Job(JobId::parse("job_front_desk").expect("valid job id"))
        );
    }
}
