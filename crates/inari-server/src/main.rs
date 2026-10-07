use inari_server::cli::{Cli, CommandOutcome, database_status, migrate_database};
use inari_server::{AppError, LoadedConfig, ServerBuilder, build_runtime, init_observability};

fn main() -> Result<(), AppError> {
    human_panic::setup_panic!();
    let command = Cli::parse().execute()?;
    if command == CommandOutcome::Complete {
        return Ok(());
    }
    let loaded = LoadedConfig::load()?;
    init_observability(&loaded.settings.observability)?;

    tracing::info!(component = "startup", config_origin = %loaded.origin, "configuration loaded");

    let runtime = build_runtime(&loaded.settings.runtime)?;

    runtime.block_on(async move {
        match command {
            CommandOutcome::MigrateDatabase => migrate_database(&loaded).await,
            CommandOutcome::DatabaseStatus => database_status(&loaded).await,
            CommandOutcome::SignAuthorityBundle { draft, approval, output } => {
                let digest = inari_server::device_authority::sign_bundle_files(
                    loaded.settings.openbao,
                    &draft,
                    &approval,
                    &output,
                )
                .await?;
                println!("Signed authority bundle stored ({}).", digest.as_str());
                Ok(())
            },
            CommandOutcome::Serve => {
                ServerBuilder::new()
                    .with_config(loaded)
                    .build()
                    .await?
                    .run()
                    .await
            },
            CommandOutcome::Complete => Ok(()),
        }
    })
}
