use std::borrow::Cow;
use std::fmt;
use std::marker::PhantomData;
use std::ops::Deref;
use std::str::FromStr;

use axum::extract::{FromRef, FromRequestParts, Path};
use axum::http::request::Parts;

use super::super::KeyExpression;
use crate::config::ZenohAdminSpaceConfig;
use crate::error::{AppError, AppResult};
use crate::state::AppState;

const EMPTY_SELECTOR_MESSAGE: &str = "Zenoh key expression cannot be empty.";

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct NormalizedSelector(String);

impl FromStr for NormalizedSelector {
    type Err = AppError;

    fn from_str(selector: &str) -> Result<Self, Self::Err> {
        let selector = selector.trim_start_matches('/');

        if selector.is_empty() {
            return Err(AppError::bad_request(EMPTY_SELECTOR_MESSAGE));
        }

        Ok(Self(selector.to_owned()))
    }
}

impl Deref for NormalizedSelector {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl fmt::Display for NormalizedSelector {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl NormalizedSelector {
    fn is_admin(&self) -> bool {
        self.starts_with("@/")
    }
}

pub(crate) type ReadSelector = Selector<Read>;
pub(crate) type WriteSelector = Selector<Write>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Selector<R> {
    key: KeyExpression,
    _role: PhantomData<R>,
}

pub(crate) enum Read {}
pub(crate) enum Write {}

pub(crate) trait AdminRole: private::Sealed {
    fn check_admin_perms(admin_space: &ZenohAdminSpaceConfig) -> bool;
    fn operation_name() -> &'static str;
}

mod private {
    pub trait Sealed {}
    impl Sealed for super::Read {}
    impl Sealed for super::Write {}
}

impl AdminRole for Read {
    fn check_admin_perms(admin_space: &ZenohAdminSpaceConfig) -> bool {
        admin_space.read
    }

    fn operation_name() -> &'static str {
        "reads"
    }
}

impl AdminRole for Write {
    fn check_admin_perms(admin_space: &ZenohAdminSpaceConfig) -> bool {
        admin_space.write
    }

    fn operation_name() -> &'static str {
        "writes"
    }
}

impl<S, R> FromRequestParts<S> for Selector<R>
where
    S: Send + Sync,
    AppState: FromRef<S>,
    R: AdminRole,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let state = AppState::from_ref(state);
        let key = extract_selector::<R>(parts, &state).await?;
        Ok(Self { key, _role: PhantomData })
    }
}

impl<R> Deref for Selector<R> {
    type Target = KeyExpression;

    fn deref(&self) -> &Self::Target {
        &self.key
    }
}

async fn extract_selector<R>(parts: &mut Parts, state: &AppState) -> AppResult<KeyExpression>
where
    R: AdminRole,
{
    let Path(selector) = Path::<String>::from_request_parts(parts, state)
        .await
        .map_err(|source| AppError::bad_request(source.to_string()))?;

    let normalized = selector.parse::<NormalizedSelector>()?;
    KeyResolver::<R>::new(state).resolve(&normalized)
}

struct KeyResolver<'a, R> {
    state: &'a AppState,
    _role: PhantomData<R>,
}

impl<'a, R: AdminRole> KeyResolver<'a, R> {
    fn new(state: &'a AppState) -> Self {
        Self { state, _role: PhantomData }
    }

    fn resolve(&self, selector: &NormalizedSelector) -> AppResult<KeyExpression> {
        let resolved = if selector.is_admin() {
            self.resolve_admin_selector(selector)?
        } else {
            Cow::Borrowed(selector.deref())
        };

        KeyExpression::from_str(resolved.as_ref()).map_err(|source| {
            AppError::bad_request(format!("Invalid Zenoh key expression `{selector}`: {source}."))
        })
    }

    fn resolve_admin_selector<'b>(
        &self,
        selector: &'b NormalizedSelector,
    ) -> AppResult<Cow<'b, str>> {
        ensure_admin_space_allowed::<R>(self.state)?;
        let zid = self.connected_zid()?;

        match selector.deref() {
            "@/local" => Ok(Cow::Owned(format!("@/{zid}"))),
            value if value.starts_with("@/local/") => {
                let suffix = value.trim_start_matches("@/local/");
                Ok(Cow::Owned(format!("@/{zid}/{suffix}")))
            },
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

fn ensure_admin_space_allowed<R: AdminRole>(state: &AppState) -> AppResult<()> {
    let settings = &state.loaded_config().settings;

    if !settings
        .http
        .zenoh_rest
        .allow_admin_space
    {
        return Err(AppError::forbidden(
            "Zenoh admin space access is disabled for this HTTP surface.",
        ));
    }

    if !settings.zenoh.admin_space.enabled {
        return Err(AppError::service_unavailable(
            "Zenoh admin space is not enabled in the embedded router.",
        ));
    }

    if !R::check_admin_perms(&settings.zenoh.admin_space) {
        return Err(AppError::forbidden(format!(
            "Zenoh admin space {} are disabled in the embedded router.",
            R::operation_name()
        )));
    }

    Ok(())
}
