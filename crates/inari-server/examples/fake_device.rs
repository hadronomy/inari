use std::error::Error as StdError;
use std::time::Duration;
use std::{env, fmt, process};

use chrono::{DateTime, Utc};
use inari_server::{LogFormat, ObservabilityConfig, init_observability};
use serde::Serialize;
use serde_json::json;
use thiserror::Error;
use tokio::signal;
use tokio::sync::watch;
use tokio::task::JoinSet;
use tokio::time::{self, MissedTickBehavior};
use tracing::{debug, info};
use zenoh::bytes::Encoding;
use zenoh::config::Config;
use zenoh::key_expr::OwnedKeyExpr;

const DEFAULT_CONNECT_ENDPOINT: &str = "tcp/127.0.0.1:7449";
const DEFAULT_NAMESPACE: &str = "iot/v1/agents/agt_demo";
const DEFAULT_INTERVAL_MS: u64 = 1_000;
const DEFAULT_TEMP_C: f64 = 21.4;
const DEFAULT_TEMP_STEP_C: f64 = 0.2;
const HELP: &str = "\
Run a tiny fake device against the embedded inari-server Zenoh router.

Usage:
  cargo run -p inari-server --example fake_device -- [options]

Options:
  --connect <endpoint>      Zenoh router endpoint to connect to
                            default: tcp/127.0.0.1:7449
  --namespace <keyexpr>     Protocol namespace root for the fake agent
                            default: iot/v1/agents/agt_demo
  --device-id <id>          Device id included in the JSON payloads
                            default: last namespace segment
  --interval-ms <ms>        Publish interval for telemetry and status updates
                            default: 1000
  --temp-c <value>          Starting temperature in Celsius
                            default: 21.4
  --temp-step-c <value>     Per-sample temperature delta
                            default: 0.2
  -h, --help                Show this help text

What it exposes:
  <namespace>/presence/agent Zenoh liveliness token
  <namespace>/status/latest JSON status publisher + queryable
  <namespace>/telemetry     JSON telemetry publisher

Try it with:
  cargo run -p inari-server --example fake_device -- --namespace iot/v1/agents/agt_123
  curl -s http://127.0.0.1:8080/api/zenoh/v1/iot/v1/agents/agt_123/status/latest | jq
  curl -N -H 'accept: text/event-stream' http://127.0.0.1:8080/api/zenoh/v1/iot/v1/agents/agt_123/telemetry
  curl -N -g -H 'accept: text/event-stream' 'http://127.0.0.1:8080/api/zenoh/v1/iot/v1/agents/**/presence/agent?_liveliness&_history'
";

type AnyError = Box<dyn StdError + Send + Sync + 'static>;
type ExampleResult<T> = Result<T, ExampleError>;

#[derive(Debug, Error)]
enum ExampleError {
    #[error("{0}")]
    Usage(String),
    #[error("failed to initialize observability")]
    Observability(#[from] inari_server::AppError),
    #[error("failed to serialize fake device payload")]
    Serialize(#[from] serde_json::Error),
    #[error("failed to wait for shutdown signal")]
    Signal(#[from] std::io::Error),
    #[error("Zenoh operation failed")]
    Zenoh(#[from] zenoh::Error),
    #[error("background task failed")]
    Task(#[from] tokio::task::JoinError),
    #[error("invalid value for {flag}: {value}")]
    InvalidValue {
        flag: &'static str,
        value: String,
        #[source]
        source: AnyError,
    },
    #[error("failed to configure Zenoh for the fake device")]
    Config {
        #[source]
        source: AnyError,
    },
}

#[derive(Debug, Clone)]
struct FakeDeviceConfig {
    connect_endpoint: String,
    namespace: String,
    device_id: String,
    interval: Duration,
    base_temp_c: f64,
    temp_step_c: f64,
    presence_key: OwnedKeyExpr,
    status_key: OwnedKeyExpr,
    telemetry_key: OwnedKeyExpr,
}

impl Default for FakeDeviceConfig {
    fn default() -> Self {
        let namespace = DEFAULT_NAMESPACE.to_owned();
        let device_id = namespace
            .rsplit('/')
            .next()
            .map(str::to_owned)
            .unwrap_or_else(|| "agt_demo".into());

        Self {
            connect_endpoint: DEFAULT_CONNECT_ENDPOINT.into(),
            presence_key: parse_key(format!("{namespace}/presence/agent"), "--namespace")
                .expect("default presence key should be valid"),
            status_key: parse_key(format!("{namespace}/status/latest"), "--namespace")
                .expect("default status key should be valid"),
            telemetry_key: parse_key(format!("{namespace}/telemetry"), "--namespace")
                .expect("default telemetry key should be valid"),
            namespace,
            device_id,
            interval: Duration::from_millis(DEFAULT_INTERVAL_MS),
            base_temp_c: DEFAULT_TEMP_C,
            temp_step_c: DEFAULT_TEMP_STEP_C,
        }
    }
}

impl FakeDeviceConfig {
    fn parse_from_environment() -> ExampleResult<Self> {
        let mut config = Self::default();
        let mut args = env::args().skip(1);

        while let Some(argument) = args.next() {
            match argument.as_str() {
                "-h" | "--help" => {
                    print!("{HELP}");
                    process::exit(0);
                },
                "--connect" => {
                    config.connect_endpoint = next_value(&mut args, "--connect")?;
                },
                "--namespace" => {
                    config.namespace = next_value(&mut args, "--namespace")?;
                },
                "--device-id" => {
                    config.device_id = next_value(&mut args, "--device-id")?;
                },
                "--interval-ms" => {
                    let value = next_value(&mut args, "--interval-ms")?;
                    config.interval = Duration::from_millis(parse_u64("--interval-ms", &value)?);
                },
                "--temp-c" => {
                    let value = next_value(&mut args, "--temp-c")?;
                    config.base_temp_c = parse_f64("--temp-c", &value)?;
                },
                "--temp-step-c" => {
                    let value = next_value(&mut args, "--temp-step-c")?;
                    config.temp_step_c = parse_f64("--temp-step-c", &value)?;
                },
                other => {
                    return Err(ExampleError::Usage(format!(
                        "Unknown argument `{other}`.\n\n{HELP}"
                    )));
                },
            }
        }

        if config.device_id.is_empty() {
            config.device_id = config
                .namespace
                .rsplit('/')
                .next()
                .map(str::to_owned)
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| "agt_demo".into());
        }

        config.presence_key =
            parse_key(format!("{}/presence/agent", config.namespace), "--namespace")?;
        config.status_key =
            parse_key(format!("{}/status/latest", config.namespace), "--namespace")?;
        config.telemetry_key = parse_key(format!("{}/telemetry", config.namespace), "--namespace")?;

        Ok(config)
    }

    fn snapshot_for_sequence(&self, seq: u64) -> FakeDeviceSnapshot {
        FakeDeviceSnapshot {
            kind: "agent.status.snapshot",
            device_id: self.device_id.clone(),
            online: true,
            seq,
            temp_c: self.base_temp_c + (seq as f64 * self.temp_step_c),
            reported_at: Utc::now(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct FakeDeviceSnapshot {
    #[serde(rename = "type")]
    kind: &'static str,
    device_id: String,
    online: bool,
    seq: u64,
    temp_c: f64,
    reported_at: DateTime<Utc>,
}

#[tokio::main]
async fn main() -> ExampleResult<()> {
    let config = FakeDeviceConfig::parse_from_environment()?;
    init_logging()?;

    let session = zenoh::open(build_zenoh_config(&config)?).await?;
    let presence_token = session
        .liveliness()
        .declare_token(config.presence_key.clone())
        .await?;

    let initial = config.snapshot_for_sequence(0);
    let (snapshot_tx, snapshot_rx) = watch::channel(initial);
    let mut tasks = JoinSet::new();

    tasks.spawn(run_status_queryable(session.clone(), config.status_key.clone(), snapshot_rx));
    tasks.spawn(run_publish_loop(session.clone(), config.clone(), snapshot_tx));

    info!(
        connect_endpoint = %config.connect_endpoint,
        namespace = %config.namespace,
        device_id = %config.device_id,
        presence_key = %config.presence_key,
        status_key = %config.status_key,
        telemetry_key = %config.telemetry_key,
        interval_ms = config.interval.as_millis(),
        "fake device ready"
    );
    println!("Press CTRL-C to drop the liveliness token and stop the fake device.");

    signal::ctrl_c().await?;
    info!("shutdown requested");

    tasks.abort_all();
    while let Some(result) = tasks.join_next().await {
        match result {
            Ok(Ok(())) => {},
            Ok(Err(error)) => return Err(error),
            Err(error) if error.is_cancelled() => {},
            Err(error) => return Err(error.into()),
        }
    }

    drop(presence_token);
    session.close().await?;
    info!("fake device stopped");

    Ok(())
}

async fn run_publish_loop(
    session: zenoh::Session,
    config: FakeDeviceConfig,
    snapshot_tx: watch::Sender<FakeDeviceSnapshot>,
) -> ExampleResult<()> {
    let telemetry_publisher = session
        .declare_publisher(config.telemetry_key.clone())
        .await?;
    let status_publisher = session
        .declare_publisher(config.status_key.clone())
        .await?;
    let mut interval = time::interval(config.interval);
    interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let mut sequence = 0_u64;

    loop {
        interval.tick().await;
        sequence += 1;

        let snapshot = config.snapshot_for_sequence(sequence);
        let payload = serde_json::to_vec(&snapshot)?;

        snapshot_tx.send_replace(snapshot.clone());
        telemetry_publisher
            .put(payload.clone())
            .encoding(Encoding::APPLICATION_JSON)
            .await?;
        status_publisher
            .put(payload)
            .encoding(Encoding::APPLICATION_JSON)
            .await?;

        debug!(
            seq = snapshot.seq,
            temp_c = snapshot.temp_c,
            telemetry_key = %config.telemetry_key,
            status_key = %config.status_key,
            "published fake device sample"
        );
    }
}

async fn run_status_queryable(
    session: zenoh::Session,
    status_key: OwnedKeyExpr,
    snapshot_rx: watch::Receiver<FakeDeviceSnapshot>,
) -> ExampleResult<()> {
    let queryable = session
        .declare_queryable(status_key.clone())
        .await?;
    info!(status_key = %status_key, "status queryable declared");

    while let Ok(query) = queryable.recv_async().await {
        let snapshot = snapshot_rx.borrow().clone();
        let payload = serde_json::to_vec(&snapshot)?;
        debug!(selector = %query.selector(), status_key = %status_key, "replying to status query");
        query
            .reply(status_key.clone(), payload)
            .encoding(Encoding::APPLICATION_JSON)
            .await?;
    }

    Ok(())
}

fn init_logging() -> ExampleResult<()> {
    let config = ObservabilityConfig {
        service_name: "inari-fake-device".into(),
        filter: "info,zenoh=warn".into(),
        format: LogFormat::Pretty,
        include_targets: false,
        include_thread_ids: false,
        include_thread_names: false,
    };

    init_observability(&config)?;
    Ok(())
}

fn build_zenoh_config(config: &FakeDeviceConfig) -> ExampleResult<Config> {
    let mut zenoh_config = Config::default();
    zenoh_config
        .insert_json5("mode", &json!("client").to_string())
        .map_err(|source| ExampleError::Config { source })?;
    zenoh_config
        .insert_json5("connect/endpoints", &json!([config.connect_endpoint]).to_string())
        .map_err(|source| ExampleError::Config { source })?;
    zenoh_config
        .insert_json5("scouting/multicast/enabled", "false")
        .map_err(|source| ExampleError::Config { source })?;

    Ok(zenoh_config)
}

fn next_value(
    args: &mut impl Iterator<Item = String>,
    flag: &'static str,
) -> ExampleResult<String> {
    args.next()
        .ok_or_else(|| ExampleError::Usage(format!("Missing value for `{flag}`.\n\n{HELP}")))
}

fn parse_key(value: String, flag: &'static str) -> ExampleResult<OwnedKeyExpr> {
    value
        .parse::<OwnedKeyExpr>()
        .map_err(|source| ExampleError::InvalidValue { flag, value, source })
}

fn parse_u64(flag: &'static str, value: &str) -> ExampleResult<u64> {
    value
        .parse::<u64>()
        .map_err(|source| ExampleError::InvalidValue {
            flag,
            value: value.into(),
            source: Box::new(source),
        })
}

fn parse_f64(flag: &'static str, value: &str) -> ExampleResult<f64> {
    value
        .parse::<f64>()
        .map_err(|source| ExampleError::InvalidValue {
            flag,
            value: value.into(),
            source: Box::new(source),
        })
}

impl fmt::Display for FakeDeviceSnapshot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "device_id={}, seq={}, temp_c={:.1}, reported_at={}",
            self.device_id, self.seq, self.temp_c, self.reported_at
        )
    }
}
