mod principal;
mod service;

pub use self::principal::{Principal, SESSION_IDENTITY_KEY};
pub use self::service::{IdentityService, LoginChallenge, PendingLogin};
pub use inari_gateway::identity::{AccessRole, ActorId, Permission, SessionIdentity};

#[derive(Clone, Debug)]
pub struct IdentityRuntime {
    service: IdentityService,
    sessions: tower_sessions_sqlx_store::PostgresStore,
}

impl IdentityRuntime {
    #[must_use]
    pub fn new(
        service: IdentityService,
        sessions: tower_sessions_sqlx_store::PostgresStore,
    ) -> Self {
        Self { service, sessions }
    }

    #[must_use]
    pub fn service(&self) -> &IdentityService {
        &self.service
    }

    #[must_use]
    pub fn sessions(&self) -> &tower_sessions_sqlx_store::PostgresStore {
        &self.sessions
    }
}
