use leptos::prelude::*;
use leptos_meta::Title;

use crate::components::AppFrame;

#[component]
pub fn HomePage() -> impl IntoView {
    view! {
        <Title text="Inari Controller"/>
        <AppFrame>
            <main class="home-page">
                <section class="hero">
                    <div>
                        <p class="eyebrow">"Managed edge infrastructure"</p>
                        <h1>"A calm control plane for the physical world."</h1>
                        <p class="hero-copy">"Enroll trusted agents, issue precise commands, and keep every edge connection observable over Zenoh."</p>
                    </div>
                    <a class="button button-primary" href="/onboarding">"Open onboarding" <span aria-hidden="true">"↗"</span></a>
                </section>
                <section class="capability-grid" aria-label="Controller capabilities">
                    <article>
                        <span class="capability-index">"01"</span>
                        <h2>"Verified enrollment"</h2>
                        <p>"One-use invitations bound cryptographically to each agent identity."</p>
                    </article>
                    <article>
                        <span class="capability-index">"02"</span>
                        <h2>"Durable coordination"</h2>
                        <p>"SQLite-backed command history and idempotent state transitions survive restarts."</p>
                    </article>
                    <article>
                        <span class="capability-index">"03"</span>
                        <h2>"Least privilege"</h2>
                        <p>"Typed Zenoh key expressions and explicit permissions keep the data plane narrow."</p>
                    </article>
                </section>
            </main>
        </AppFrame>
    }
}
