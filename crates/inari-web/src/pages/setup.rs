use leptos::prelude::*;
use leptos_meta::Title;
use leptos_router::hooks::use_params;
use leptos_router::params::Params;

use crate::components::{Brand, StateBadge};
use crate::server_fns::{InvitationPreview, load_invitation};

#[derive(Clone, Debug, PartialEq, Params)]
struct SetupParams {
    invitation_id: Option<String>,
}

#[component]
pub fn SetupPage() -> impl IntoView {
    let params = use_params::<SetupParams>();
    let invitation_id = move || {
        params
            .read()
            .as_ref()
            .ok()
            .and_then(|params| params.invitation_id.clone())
            .unwrap_or_default()
    };
    let preview = Resource::new(invitation_id, load_invitation);
    let deep_link = RwSignal::new(None::<String>);

    Effect::new(move |_| deep_link.set(browser::enrollment_link(&invitation_id())));

    view! {
        <Title text="Connect this agent — Inari"/>
        <main class="setup-page">
            <div class="setup-brand"><Brand/></div>
            <Suspense fallback=SetupSkeleton>
                {move || Suspend::new(async move {
                    match preview.await {
                        Ok(invitation) => view! { <SetupCard invitation deep_link/> }.into_any(),
                        Err(error) => view! { <SetupFailure detail=error.to_string()/> }.into_any(),
                    }
                })}
            </Suspense>
            <p class="setup-footnote">"The invitation secret remains in this page’s URL fragment and is never sent during preview."</p>
        </main>
    }
}

#[component]
fn SetupCard(invitation: InvitationPreview, deep_link: RwSignal<Option<String>>) -> impl IntoView {
    let controller_name = invitation
        .controller_name
        .clone()
        .unwrap_or_else(|| "Inari Controller".into());
    let protocol_versions = invitation
        .supported_protocol_versions
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    view! {
        <section class="setup-card">
            <div class="setup-status"><StateBadge state=invitation.state/><span>"Secure enrollment"</span></div>
            <h1>"Connect this Inari agent"</h1>
            <p class="setup-intro">{format!("{controller_name} is ready to establish a verified connection with this device.")}</p>
            {move || match deep_link.get() {
                Some(link) => view! { <a class="button button-primary button-wide" href=link>"Open Inari setup" <span aria-hidden="true">"→"</span></a> }.into_any(),
                None => view! { <span class="button button-primary button-wide button-disabled" aria-disabled="true">"Preparing secure link…"</span> }.into_any(),
            }}
            <dl class="trust-grid">
                <div><dt>"Controller"</dt><dd>{controller_name}</dd></div>
                <div><dt>"Certificate"</dt><dd>{invitation.certificate_mode.label()}</dd></div>
                <div><dt>"Mutual TLS"</dt><dd>{if invitation.requires_mutual_tls_after_issuance { "Required after issuance" } else { "Optional" }}</dd></div>
                <div><dt>"Protocol"</dt><dd>{protocol_versions}</dd></div>
                <div><dt>"Expires"</dt><dd>{invitation.expires_at.to_rfc3339()}</dd></div>
            </dl>
            <div class="invitation-reference"><span>"Invitation"</span><code>{invitation.invitation_id}</code></div>
        </section>
    }
}

#[component]
fn SetupSkeleton() -> impl IntoView {
    view! { <section class="setup-card setup-skeleton" aria-busy="true"><div></div><div></div><div></div><div></div></section> }
}

#[component]
fn SetupFailure(detail: String) -> impl IntoView {
    view! {
        <section class="setup-card setup-failure">
            <span class="failure-mark" aria-hidden="true">"×"</span>
            <p class="eyebrow">"Invitation unavailable"</p>
            <h1>"This connection cannot be started."</h1>
            <p>{detail}</p>
            <a class="button button-quiet" href="/">"Return to controller"</a>
        </section>
    }
}

#[cfg(feature = "hydrate")]
mod browser {
    use leptos::prelude::window;

    pub(super) fn enrollment_link(invitation_id: &str) -> Option<String> {
        let location = window().location();
        let fragment = location.hash().ok()?;
        let code = fragment.strip_prefix("#code=")?;
        let origin = location.origin().ok()?;
        let mut url = url::Url::parse("inari://enroll").ok()?;
        url.query_pairs_mut()
            .append_pair("controller", &origin)
            .append_pair("invite_id", invitation_id);
        url.set_fragment(Some(&format!("code={code}")));
        Some(url.into())
    }
}

#[cfg(not(feature = "hydrate"))]
mod browser {
    pub(super) fn enrollment_link(_invitation_id: &str) -> Option<String> {
        None
    }
}
