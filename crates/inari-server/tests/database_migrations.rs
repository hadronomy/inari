use inari_server::config::DatabaseConfig;
use inari_server::database::ControllerDatabase;
use tower_sessions::Session;

fn database_config(database_url: &str) -> (tempfile::TempDir, DatabaseConfig) {
    let directory = tempfile::tempdir().expect("temporary directory should be created");
    let url_file = directory.path().join("database-url");
    std::fs::write(&url_file, database_url).expect("database URL should be written");
    (
        directory,
        DatabaseConfig {
            url_file,
            migrate_on_startup: false,
            min_connections: 1,
            max_connections: 4,
        },
    )
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn embedded_migration_is_idempotent_and_owns_sessions() {
    let database_url = std::env::var("INARI_TEST_DATABASE_URL")
        .expect("INARI_TEST_DATABASE_URL is required for PostgreSQL integration tests");
    let (_directory, config) = database_config(&database_url);
    let database = ControllerDatabase::connect(&config)
        .await
        .expect("database should connect");

    database
        .migrate()
        .await
        .expect("first migration should succeed");
    let second = database
        .migrate()
        .await
        .expect("repeated migration should succeed");
    assert!(second.applied.is_empty());
    assert!(second.pending.is_empty());

    let session_table =
        sqlx::query_scalar::<_, bool>("SELECT to_regclass('tower_sessions.session') IS NOT NULL")
            .fetch_one(database.pool())
            .await
            .expect("session table should be inspected");
    assert!(session_table);

    let store =
        std::sync::Arc::new(tower_sessions_sqlx_store::PostgresStore::new(database.pool().clone()));
    let session = Session::new(None, store.clone(), None);
    session
        .insert("subject", "operator-test")
        .await
        .expect("session value should be inserted");
    session
        .save()
        .await
        .expect("session should be created");
    let original_id = session
        .id()
        .expect("saved session should have an ID");

    let loaded = Session::new(Some(original_id), store.clone(), None);
    assert_eq!(
        loaded
            .get::<String>("subject")
            .await
            .expect("session should load")
            .as_deref(),
        Some("operator-test"),
    );
    loaded
        .cycle_id()
        .await
        .expect("session ID should rotate");
    loaded
        .save()
        .await
        .expect("rotated session should persist");
    assert_ne!(loaded.id(), Some(original_id));
    loaded
        .delete()
        .await
        .expect("session should be deleted");
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn concurrent_migration_runners_serialize() {
    let database_url = std::env::var("INARI_TEST_DATABASE_URL")
        .expect("INARI_TEST_DATABASE_URL is required for PostgreSQL integration tests");
    let (_directory, config) = database_config(&database_url);
    let database = ControllerDatabase::connect(&config)
        .await
        .expect("database should connect");

    let (first, second) = tokio::join!(database.migrate(), database.migrate());
    first.expect("first migration runner should succeed");
    second.expect("second migration runner should succeed");
    database
        .ensure_current()
        .await
        .expect("database should be current after concurrent runners");
}
