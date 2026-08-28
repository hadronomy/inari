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
    install_panic_hook();
    Ok(guard)
}

/// Route panics into the log.
///
/// The windowed subsystem has no stderr, so an unhooked panic vanishes — the
/// process fails fast and the event log records only an address. The hook
/// writes the message and location to the same file as everything else,
/// which is the difference between diagnosing a crash and guessing at one.
fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        let location = info
            .location()
            .map(|location| format!("{}:{}", location.file(), location.line()))
            .unwrap_or_else(|| "unknown location".into());
        let message = info
            .payload()
            .downcast_ref::<&str>()
            .map(|message| message.to_string())
            .or_else(|| {
                info.payload()
                    .downcast_ref::<String>()
                    .cloned()
            })
            .unwrap_or_else(|| "no panic message".into());
        tracing::error!(%location, %message, "panic");
    }));
}
