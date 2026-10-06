use std::time::Duration;

use chrono::{DateTime, Utc};
use eventsource_stream::Eventsource as _;
use futures_util::{StreamExt as _, stream::BoxStream};
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
    messages: BoxStream<'static, AgentClientResult<eventsource_stream::Event>>,
}

impl AgentEventStream {
    pub(crate) async fn connect(endpoint: &Url, token: &SecretString) -> AgentClientResult<Self> {
        crate::local_transport::socket_address(endpoint)?;
        let stream_url = endpoint
            .join("system/events")
            .map_err(AgentClientError::invalid_response)?;
        let http = crate::local_transport::http_client_builder()
            .read_timeout(Duration::from_secs(30))
            .build()
            .map_err(AgentClientError::Unavailable)?;
        let response = http
            .get(stream_url)
            .bearer_auth(token.expose_secret())
            .header(reqwest::header::ACCEPT, "text/event-stream")
            .send()
            .await
            .map_err(AgentClientError::Unavailable)?;
        if !response.status().is_success() {
            return Err(AgentClientError::Rejected);
        }
        let media_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split(';').next());
        if media_type != Some("text/event-stream") {
            return Err(AgentClientError::Rejected);
        }
        let messages = response
            .bytes_stream()
            .eventsource()
            .map(|event| event.map_err(AgentClientError::event_stream_unavailable))
            .boxed();
        Ok(Self { messages })
    }

    pub async fn next(&mut self) -> AgentClientResult<Option<AgentEvent>> {
        while let Some(message) = self.messages.next().await {
            let message = message?;
            let payload = message.data;
            match serde_json::from_str::<LiveMessage>(&payload)
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
    use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};

    #[tokio::test]
    async fn native_monitor_reads_sse_from_the_agent_route() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = tokio::spawn(async move {
            let (mut stream, peer) = listener.accept().await.unwrap();
            assert!(peer.ip().is_loopback());
            let mut bytes = [0; 4096];
            let count = stream.read(&mut bytes).await.unwrap();
            let request = std::str::from_utf8(&bytes[..count])
                .unwrap()
                .to_owned();
            let fixture: Vec<serde_json::Value> =
                serde_json::from_str(include_str!("../../../contracts/local-agent.events.json"))
                    .unwrap();
            let body = format!(
                ": heartbeat\n\ndata: {{\"kind\":\"snapshot\"}}\n\ndata: {}\n\n",
                fixture[0]
            );
            let headers = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            stream
                .write_all(headers.as_bytes())
                .await
                .unwrap();
            for chunk in body.as_bytes().chunks(7) {
                stream.write_all(chunk).await.unwrap();
            }
            request
        });
        let endpoint = format!("http://agent.fixture.invalid:{port}/")
            .parse()
            .unwrap();
        let mut stream = AgentEventStream::connect(&endpoint, &SecretString::from("fixture-token"))
            .await
            .unwrap();
        assert_eq!(
            stream
                .next()
                .await
                .unwrap()
                .unwrap()
                .kind,
            AgentEventKind::DeviceConnected
        );
        assert!(stream.next().await.unwrap().is_none());
        let request = server
            .await
            .unwrap()
            .to_ascii_lowercase();
        assert!(request.starts_with("get /system/events http/1.1\r\n"));
        assert!(request.contains(&format!("host: agent.fixture.invalid:{port}\r\n")));
        assert!(request.contains("authorization: bearer fixture-token\r\n"));
        assert!(request.contains("accept: text/event-stream\r\n"));
    }

    #[tokio::test]
    async fn tls_events_do_not_require_a_process_crypto_provider() {
        const CHILD: &str = "INARI_TLS_PROVIDER_TEST_CHILD";
        if std::env::var(CHILD).as_deref() != Ok("1") {
            let result = std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "events::tests::tls_events_do_not_require_a_process_crypto_provider",
                    "--nocapture",
                ])
                .env(CHILD, "1")
                .status()
                .unwrap();
            assert!(result.success());
            return;
        }
        assert!(rustls::crypto::CryptoProvider::get_default().is_none());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap();
        let endpoint =
            format!("https://agent.fixture.invalid:{}/", listener.local_addr().unwrap().port())
                .parse()
                .unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut hello = [0; 1024];
            assert!(
                tokio::io::AsyncReadExt::read(&mut stream, &mut hello)
                    .await
                    .unwrap()
                    > 0
            );
        });
        let result = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            AgentEventStream::connect(&endpoint, &SecretString::from("fixture-token")),
        )
        .await
        .unwrap();
        assert!(matches!(result, Err(AgentClientError::Unavailable(_))));
        server.await.unwrap();
        assert!(rustls::crypto::CryptoProvider::get_default().is_none());
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
