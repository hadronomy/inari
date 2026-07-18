use std::fs;

use anyhow::Context as _;
use tracing_appender::non_blocking::WorkerGuard;

pub fn initialize_logging() -> anyhow::Result<WorkerGuard> {
    let project = directories::ProjectDirs::from("dev", "Inari", "Inari Device Center")
        .context("the operating system did not provide a data directory")?;
    let directory = project.data_local_dir().join("logs");
    fs::create_dir_all(&directory).context("could not create the Device Center log directory")?;

    let file = tracing_appender::rolling::daily(directory, "device-center.log");
    let (writer, guard) = tracing_appender::non_blocking(file);
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "inari_device_center=info".into()),
        )
        .with_ansi(false)
        .with_writer(writer)
        .init();
    Ok(guard)
}
