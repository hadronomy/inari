use std::str::FromStr;

use super::ManagedGatewayController;
use crate::error::{AppError, AppResult};
use crate::zenoh::KeyExpression;

impl ManagedGatewayController {
    pub(super) fn history_query_key(&self) -> AppResult<KeyExpression> {
        self.namespace_prefix_key()?
            .join("*")
            .and_then(|key| key.join("commands"))
            .and_then(|key| key.join("history"))
            .map_err(|source| {
                AppError::bad_request(format!("Invalid managed history key expression: {source}"))
            })
    }

    pub(super) fn publications_key(&self) -> AppResult<KeyExpression> {
        self.namespace_prefix_key()?
            .join("*")
            .and_then(|key| key.join("**"))
            .map_err(|source| {
                AppError::bad_request(format!(
                    "Invalid managed publication key expression: {source}"
                ))
            })
    }

    fn namespace_prefix_key(&self) -> AppResult<KeyExpression> {
        KeyExpression::from_str(
            self.inner
                .config
                .data_plane
                .namespace_prefix
                .trim_end_matches('/'),
        )
        .map_err(|source| {
            AppError::bad_request(format!("Invalid managed namespace prefix: {source}"))
        })
    }

    pub(super) fn agent_id_from_key(&self, key: &str) -> Option<String> {
        let prefix = self
            .inner
            .config
            .data_plane
            .namespace_prefix
            .trim_end_matches('/');
        let rest = key
            .strip_prefix(prefix)?
            .strip_prefix('/')?;
        rest.split('/')
            .next()
            .map(str::to_owned)
    }
}
