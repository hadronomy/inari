use clap::{ArgAction, Parser, Subcommand};
use secrecy::SecretString;
use toml::Value;

use crate::{AppError, AppResult, LoadedConfig};

#[derive(Debug, Parser)]
#[command(name = "inari-server", version, about = "Inari managed device controller")]
pub struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Inspect and validate the effective controller configuration.
    Config {
        #[command(subcommand)]
        command: ConfigCommand,
    },
    /// Manage the controller database lifecycle.
    Database {
        #[command(subcommand)]
        command: DatabaseCommand,
    },
}

#[derive(Debug, Subcommand)]
enum DatabaseCommand {
    /// Apply all embedded PostgreSQL migrations and exit.
    Migrate,
}

#[derive(Debug, Subcommand)]
enum ConfigCommand {
    /// Validate the complete layered configuration.
    Validate,
    /// Explain configuration sources, precedence, and secret handling.
    Explain,
    /// Print the effective configuration as TOML.
    PrintEffective {
        /// Include configured secret values. Never use this in support bundles or logs.
        #[arg(long = "no-redact", default_value_t = true, action = ArgAction::SetFalse)]
        redact: bool,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandOutcome {
    Serve,
    MigrateDatabase,
    Complete,
}

impl Cli {
    pub fn execute(self) -> AppResult<CommandOutcome> {
        let Some(command) = self.command else {
            return Ok(CommandOutcome::Serve);
        };
        match command {
            Command::Config { command } => {
                let loaded = LoadedConfig::load()?;
                match command {
                    ConfigCommand::Validate => {
                        println!("Configuration is valid ({}).", loaded.origin);
                    },
                    ConfigCommand::Explain => print_explanation(&loaded),
                    ConfigCommand::PrintEffective { redact } => {
                        if !redact {
                            eprintln!(
                                "WARNING: effective configuration output includes sensitive values; handle it as a secret."
                            );
                        }
                        println!("{}", effective_toml(&loaded, redact)?);
                    },
                }
                Ok(CommandOutcome::Complete)
            },
            Command::Database { command: DatabaseCommand::Migrate } => {
                Ok(CommandOutcome::MigrateDatabase)
            },
        }
    }
}

pub async fn migrate_database(loaded: &LoadedConfig) -> AppResult<()> {
    let path = &loaded.settings.database.url_file;
    let database_url = tokio::fs::read_to_string(path)
        .await
        .map_err(|source| {
            AppError::internal(
                "database_secret",
                "The PostgreSQL connection secret could not be read.",
            )
            .with_source(source)
        })?;
    inari_gateway::GatewayRepository::migrate(&SecretString::from(database_url.trim().to_owned()))
        .await
        .map_err(AppError::from)?;
    println!("Controller database migrations are current.");
    Ok(())
}

fn print_explanation(loaded: &LoadedConfig) {
    println!("Effective configuration source: {}", loaded.origin);
    println!(
        "Precedence (lowest to highest): built-in defaults, TOML files, INARI_SERVER_* environment variables."
    );
    println!(
        "Secret-bearing output is redacted unless `config print-effective --no-redact` is used explicitly."
    );
}

fn effective_toml(loaded: &LoadedConfig, redact: bool) -> AppResult<String> {
    let mut value = Value::try_from(&loaded.settings).map_err(|source| {
        AppError::internal(
            "effective_config_serialization",
            "The effective configuration could not be serialized.",
        )
        .with_source(source)
    })?;
    let mut resolved_secrets = toml::map::Map::new();
    for (name, path) in resolved_secret_files(loaded) {
        let secret = if redact {
            "<redacted>".to_owned()
        } else {
            std::fs::read_to_string(path)
                .map_err(|source| {
                    AppError::internal(
                        "effective_config_secret",
                        format!("The resolved secret {name:?} could not be read."),
                    )
                    .with_source(source)
                })?
                .trim()
                .to_owned()
        };
        resolved_secrets.insert(name.to_owned(), Value::String(secret));
    }
    if !resolved_secrets.is_empty() {
        value
            .as_table_mut()
            .expect("serialized application configuration must be a TOML table")
            .insert("resolved_secrets".into(), Value::Table(resolved_secrets));
    }
    toml::to_string_pretty(&value).map_err(|source| {
        AppError::internal(
            "effective_config_serialization",
            "The effective configuration could not be rendered.",
        )
        .with_source(source)
    })
}

fn resolved_secret_files(loaded: &LoadedConfig) -> Vec<(&'static str, &std::path::Path)> {
    let settings = &loaded.settings;
    let mut files = Vec::with_capacity(3);
    if settings.managed_gateway.enabled || settings.identity.oidc.enabled {
        files.push(("database_url", settings.database.url_file.as_path()));
    }
    if settings.identity.oidc.enabled
        && let Some(path) = &settings
            .identity
            .oidc
            .client_secret_file
    {
        files.push(("oidc_client_secret", path.as_path()));
    }
    if settings
        .managed_gateway
        .certificate
        .mode
        == crate::config::ManagedGatewayCertificateMode::StepCa
        && let Some(path) = &settings
            .managed_gateway
            .certificate
            .step_ca_signing_key_file
    {
        files.push(("step_ca_provisioner_key", path.as_path()));
    }
    files
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn command_definition_is_internally_consistent() {
        Cli::command().debug_assert();
    }

    fn print_effective_redaction(arguments: &[&str]) -> bool {
        let cli = Cli::try_parse_from(arguments).expect("CLI arguments should parse");
        let Some(Command::Config { command: ConfigCommand::PrintEffective { redact } }) =
            cli.command
        else {
            panic!("arguments should select config print-effective");
        };
        redact
    }

    #[test]
    fn print_effective_redacts_by_default() {
        assert!(print_effective_redaction(&["inari-server", "config", "print-effective",]));
    }

    #[test]
    fn no_redact_disables_redaction() {
        assert!(!print_effective_redaction(&[
            "inari-server",
            "config",
            "print-effective",
            "--no-redact",
        ]));
    }

    #[test]
    fn positive_redact_flag_is_not_a_parallel_interface() {
        let error = Cli::try_parse_from(["inari-server", "config", "print-effective", "--redact"])
            .expect_err("only the explicit disclosure flag should be accepted");

        assert_eq!(error.kind(), clap::error::ErrorKind::UnknownArgument);
    }

    #[test]
    fn effective_configuration_is_redacted_by_default() {
        let mut loaded = LoadedConfig::default();
        loaded.settings.managed_gateway.enabled = true;
        let rendered = effective_toml(&loaded, true).expect("config should render");
        assert!(rendered.contains("<redacted>"));
        assert!(!rendered.contains("postgresql://"));
    }

    #[test]
    fn no_redact_resolves_secret_file_values() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let database_url = directory.path().join("database-url");
        std::fs::write(&database_url, "postgresql://secret@database/inari\n")
            .expect("test secret should be written");
        let mut loaded = LoadedConfig::default();
        loaded.settings.managed_gateway.enabled = true;
        loaded.settings.database.url_file = database_url;
        let rendered = effective_toml(&loaded, false).expect("config should render");
        assert!(rendered.contains("postgresql://secret@database/inari"));
    }
}
