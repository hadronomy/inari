use leptos::prelude::*;
use leptos_meta::{MetaTags, Stylesheet, provide_meta_context};
use leptos_router::components::{Route, Router, Routes};
use leptos_router::path;

use crate::pages::{HomePage, NotFoundPage, OperatorPage, SetupPage};

pub fn shell(options: LeptosOptions) -> impl IntoView {
    server::set_document_headers();
    let favicon_href = server::favicon_href();
    view! {
        <!DOCTYPE html>
        <html lang="en">
            <head>
                <meta charset="utf-8"/>
                <meta name="viewport" content="width=device-width, initial-scale=1"/>
                <meta name="description" content="Private device operations."/>
                <meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)"/>
                <meta name="theme-color" content="#0f1110" media="(prefers-color-scheme: dark)"/>
                <meta property="og:title" content="Inari"/>
                <meta property="og:description" content="Private device operations."/>
                <meta property="og:image" content="/social-preview.svg"/>
                <link rel="icon" href=favicon_href type="image/svg+xml"/>
                <link rel="manifest" href="/site.webmanifest"/>
                <link rel="apple-touch-icon" href="/inari-icon-192.png"/>
                <AutoReload options=options.clone()/>
                <HydrationScripts options/>
                <MetaTags/>
            </head>
            <body>
                <App/>
            </body>
        </html>
    }
}

#[component]
pub fn App() -> impl IntoView {
    provide_meta_context();
    view! {
        <Stylesheet id="inari-web" href="/pkg/inari-web.css"/>
        <Router>
            <Routes fallback=NotFoundPage>
                <Route path=path!("") view=HomePage/>
                <Route path=path!("onboarding") view=OperatorPage/>
                <Route path=path!("setup/:invitation_id") view=SetupPage/>
            </Routes>
        </Router>
    }
}

#[cfg(feature = "ssr")]
mod server {
    use http::HeaderValue;
    use http::header::{
        CACHE_CONTROL, CONTENT_SECURITY_POLICY, REFERRER_POLICY, X_CONTENT_TYPE_OPTIONS,
    };
    use leptos::nonce::use_nonce;
    use leptos::prelude::*;
    use leptos_axum::ResponseOptions;

    use crate::{ControllerContext, DeploymentEnvironment};

    pub(super) fn favicon_href() -> &'static str {
        use_context::<ControllerContext>()
            .map(|context| context.environment())
            .unwrap_or(DeploymentEnvironment::Development)
            .favicon_href()
    }

    pub(super) fn set_document_headers() {
        let Some(response) = use_context::<ResponseOptions>() else {
            return;
        };
        if let Some(nonce) = use_nonce() {
            let policy = format!(
                "default-src 'none'; script-src 'self' 'nonce-{nonce}' 'wasm-unsafe-eval'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self' data:; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            );
            if let Ok(value) = HeaderValue::from_str(&policy) {
                response.insert_header(CONTENT_SECURITY_POLICY, value);
            }
        }
        response.insert_header(CACHE_CONTROL, HeaderValue::from_static("no-store"));
        response.insert_header(REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
        response.insert_header(X_CONTENT_TYPE_OPTIONS, HeaderValue::from_static("nosniff"));
    }
}

#[cfg(not(feature = "ssr"))]
mod server {
    pub(super) fn favicon_href() -> &'static str {
        "/favicon-development.svg"
    }

    pub(super) fn set_document_headers() {}
}
