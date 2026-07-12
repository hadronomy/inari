use leptos::prelude::*;

use crate::server_fns::InvitationState;

#[component]
pub fn Brand() -> impl IntoView {
    view! {
        <a class="brand" href="/" aria-label="Inari home">
            <span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
            <span>"Inari"</span>
        </a>
    }
}

#[component]
pub fn AppFrame(children: Children) -> impl IntoView {
    view! {
        <div class="app-shell">
            <header class="app-bar">
                <Brand/>
                <span class="environment">"Private device operations"</span>
            </header>
            {children()}
            <footer class="app-footer">
                <span>"Inari managed gateway"</span>
                <span>"Protocol 2026-07-12"</span>
            </footer>
        </div>
    }
}

#[component]
pub fn StateBadge(state: InvitationState) -> impl IntoView {
    view! { <span class=state.badge_class()>{state.label()}</span> }
}

#[component]
pub fn InlineNotice(kind: &'static str, children: Children) -> impl IntoView {
    view! { <div class=format!("notice notice-{kind}") role="status">{children()}</div> }
}
