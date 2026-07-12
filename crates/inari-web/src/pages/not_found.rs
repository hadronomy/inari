use leptos::prelude::*;
use leptos_meta::Title;

use crate::components::AppFrame;

#[component]
pub fn NotFoundPage() -> impl IntoView {
    view! {
        <Title text="Page not found — Inari"/>
        <AppFrame>
            <main class="empty-page">
                <p class="eyebrow">"404 · Outside the mapped keyspace"</p>
                <h1>"This page does not exist."</h1>
                <p>"The controller is healthy; the address simply has no matching interface route."</p>
                <a class="button button-primary" href="/">"Return home"</a>
            </main>
        </AppFrame>
    }
}
