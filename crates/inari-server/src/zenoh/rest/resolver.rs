use std::{borrow::Cow, str::FromStr};

use crate::{
    error::{AppError, AppResult},
    state::AppState,
};

use super::super::KeyExpression;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AdminOperation {
    Read,
    Write,
}

pub(crate) struct KeyResolver<'a> {
    state: &'a AppState,
}

impl<'a> KeyResolver<'a> {
    pub(crate) fn new(state: &'a AppState) -> Self {
        Self { state }
    }

    pub(crate) fn resolve(
        &self,
        selector: &str,
        operation: AdminOperation,
    ) -> AppResult<KeyExpression> {
        let selector = normalize_selector(selector)?;
        let resolved = if is_admin_selector(selector) {
            self.resolve_admin_selector(selector, operation)?
        } else {
            Cow::Borrowed(selector)
        };

        KeyExpression::from_str(resolved.as_ref()).map_err(|source| {
            AppError::bad_request(format!("Invalid Zenoh key expression `{selector}`: {source}."))
        })
    }

    fn resolve_admin_selector(
        &self,
        selector: &'a str,
        operation: AdminOperation,
    ) -> AppResult<Cow<'a, str>> {
        AdminSpaceGuard::new(self.state).ensure_allowed(operation)?;
        let zid = self.connected_zid()?;

        match selector {
            "@/local" => Ok(Cow::Owned(format!("@/{zid}"))),
            value if value.starts_with("@/local/") => {
                let suffix = value.trim_start_matches("@/local/");
                Ok(Cow::Owned(format!("@/{zid}/{suffix}")))
            }
            value => Ok(Cow::Borrowed(value)),
        }
    }

    fn connected_zid(&self) -> AppResult<String> {
        self.state
            .zenoh()
            .session_snapshot()
            .map(|session| session.zid().to_owned())
            .ok_or_else(|| AppError::service_unavailable("Zenoh session is not connected."))
    }
}

struct AdminSpaceGuard<'a> {
    state: &'a AppState,
}

impl<'a> AdminSpaceGuard<'a> {
    fn new(state: &'a AppState) -> Self {
        Self { state }
    }

    fn ensure_allowed(&self, operation: AdminOperation) -> AppResult<()> {
        let settings = &self.state.loaded_config().settings;

        if !settings.http.zenoh_rest.allow_admin_space {
            return Err(AppError::forbidden(
                "Zenoh admin space access is disabled for this HTTP surface.",
            ));
        }

        if !settings.zenoh.admin_space.enabled {
            return Err(AppError::service_unavailable(
                "Zenoh admin space is not enabled in the embedded router.",
            ));
        }

        match operation {
            AdminOperation::Read if !settings.zenoh.admin_space.read => Err(AppError::forbidden(
                "Zenoh admin space reads are disabled in the embedded router.",
            )),
            AdminOperation::Write if !settings.zenoh.admin_space.write => Err(AppError::forbidden(
                "Zenoh admin space writes are disabled in the embedded router.",
            )),
            AdminOperation::Read | AdminOperation::Write => Ok(()),
        }
    }
}

fn normalize_selector(selector: &str) -> AppResult<&str> {
    let selector = selector.trim_start_matches('/');

    if selector.is_empty() {
        return Err(AppError::bad_request("Zenoh key expression cannot be empty."));
    }

    Ok(selector)
}

fn is_admin_selector(selector: &str) -> bool {
    selector.starts_with("@/")
}
