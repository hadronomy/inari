use std::fmt;

use zenoh::Session;
use zenoh::config::ZenohId;

use super::SessionGeneration;

/// Snapshot of the currently connected embedded Zenoh session plus a local
/// generation counter that changes each time a new session is established.
#[derive(Clone)]
pub(crate) struct CurrentSession {
    session: Session,
    generation: SessionGeneration,
}

impl CurrentSession {
    pub(crate) fn new(session: Session, generation: SessionGeneration) -> Self {
        Self { session, generation }
    }

    #[must_use]
    pub(crate) fn session(&self) -> &Session {
        &self.session
    }

    #[must_use]
    pub(crate) fn generation(&self) -> SessionGeneration {
        self.generation
    }

    #[must_use]
    pub(crate) fn zid(&self) -> ZenohId {
        self.session.zid()
    }
}

impl fmt::Debug for CurrentSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CurrentSession")
            .field("zid", &self.session.zid())
            .field("generation", &self.generation)
            .finish_non_exhaustive()
    }
}
