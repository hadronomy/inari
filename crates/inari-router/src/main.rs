use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use ed25519_dalek::VerifyingKey;
use inari_router::management::{ManagementAcceptor, serve};
use inari_router::supervisor::RouterSupervisor;
use inari_router::{PolicyStore, RouterConfig};
use serde::Deserialize;
use tokio::sync::watch;

#[derive(Debug, usage::Cli)]
#[usage(
    bin = "inari-router",
    version,
    about = "Signed policy and Zenoh Router supervision",
    unknown_flags = "error"
)]
struct Cli {
    /// Read the Router and management configuration from this TOML file.
    #[usage(long = "config")]
    config: PathBuf,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    fleet_id: String,
    state_directory: PathBuf,
    signing_public_key_file: PathBuf,
    zenoh_executable: PathBuf,
    management: ManagementConfig,
    router: RouterConfig,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ManagementConfig {
    address: SocketAddr,
    certificate_file: PathBuf,
    private_key_file: PathBuf,
    client_ca_file: PathBuf,
    controller_common_name: String,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    let arguments = Cli::parse();
    let config: Config = toml::from_str(&tokio::fs::read_to_string(&arguments.config).await?)?;
    let key = tokio::fs::read_to_string(&config.signing_public_key_file).await?;
    let public_key: [u8; 32] = hex::decode(key.trim())?
        .try_into()
        .map_err(|_| "Router signing public key must contain 32 bytes")?;
    let key = VerifyingKey::from_bytes(&public_key)?;
    let acceptor = ManagementAcceptor::from_files(
        &config.management.certificate_file,
        &config.management.private_key_file,
        &config.management.client_ca_file,
        &config.management.controller_common_name,
    )
    .await?;
    let (handle, supervisor) = tokio::task::spawn_blocking(move || {
        let store = PolicyStore::open(&config.state_directory)?;
        RouterSupervisor::new(store, key, config.fleet_id, config.zenoh_executable, config.router)
    })
    .await??;
    let (shutdown, receiver) = watch::channel(false);
    let server_handle = axum_server::Handle::new();
    let server = serve(config.management.address, acceptor, handle, server_handle.clone());
    tokio::pin!(server);
    let mut actor = tokio::spawn(supervisor.run(receiver));
    tokio::select! {
        result = &mut actor => { server_handle.shutdown(); result??; },
        result = &mut server => { let _ = shutdown.send(true); actor.await??; result?; },
        result = shutdown_signal() => {
            result?;
            let _ = shutdown.send(true);
            server_handle.graceful_shutdown(Some(Duration::from_secs(10)));
            actor.await??;
            server.await?;
        }
    }
    Ok(())
}

async fn shutdown_signal() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(not(unix))]
    tokio::signal::ctrl_c().await
}
