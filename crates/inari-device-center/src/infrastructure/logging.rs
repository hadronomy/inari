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
/// appends to the log file directly and synchronously: the process aborts
/// when the panic unwinds, so a buffered or backgrounded writer loses the
/// message exactly when it matters.
fn install_panic_hook() {
    let directory =
        directories::ProjectDirs::from("dev", "Inari", "Inari Device Center").map(|project| {
            project
                .data_local_dir()
                .join("data")
                .join("logs")
        });
    std::panic::set_hook(Box::new(move |info| {
        let Some(directory) = &directory else {
            return;
        };
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
        let entry = format!("[{stamp}] ERROR panic at {location}: {message}\n");
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
