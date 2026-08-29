use std::{fs, path::PathBuf};

use anyhow::Context as _;
use tracing_appender::non_blocking::WorkerGuard;

/// Where the Device Center writes its logs.
///
/// One owner for the path. The logging setup creates it, Support shows it, and
/// the tray and the Support button open it — three call sites that were each
/// deriving the same directory from the same three strings, which is a change
/// that can only ever half-land.
///
/// `None` when the operating system provides no data directory: the one case
/// where there is no answer to give rather than a wrong one.
pub fn log_directory() -> Option<PathBuf> {
    directories::ProjectDirs::from("dev", "Inari", "Inari Device Center")
        .map(|project| project.data_local_dir().join("logs"))
}

pub fn initialize_logging() -> anyhow::Result<WorkerGuard> {
    let directory =
        log_directory().context("the operating system did not provide a data directory")?;
    fs::create_dir_all(&directory).context("could not create the Device Center log directory")?;

    let file = tracing_appender::rolling::daily(&directory, "device-center.log");
    let (writer, guard) = tracing_appender::non_blocking(file);
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "inari_device_center=info".into()),
        )
        .with_ansi(false)
        .with_writer(writer)
        .init();
    install_panic_hook(directory);
    Ok(guard)
}

/// Route panics into the log.
///
/// The windowed subsystem has no stderr, so an unhooked panic vanishes — the
/// process fails fast and the event log records only an address. The hook
/// appends to the log file directly and synchronously: the process aborts
/// when the panic unwinds, so a buffered or backgrounded writer loses the
/// message exactly when it matters.
fn install_panic_hook(directory: std::path::PathBuf) {
    std::panic::set_hook(Box::new(move |info| {
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
        let stamp = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.3f");
        let entry = format!("[{stamp}] panic at {location}: {message}\n");
        if let Ok(mut file) = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(directory.join("panics.log"))
        {
            use std::io::Write as _;
            let _ = file.write_all(entry.as_bytes());
        }
    }));
}
