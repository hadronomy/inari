use leptos::prelude::*;
use leptos_meta::Title;

use crate::components::AppFrame;
use crate::server_fns::{FleetOverview, OnboardingError, load_fleet_overview};

#[component]
pub fn HomePage() -> impl IntoView {
    let overview = Resource::new(|| (), |_| load_fleet_overview());

    view! {
        <Title text="Operations — Inari"/>
        <AppFrame>
            <main class="dashboard-page">
                <header class="dashboard-heading">
                    <div>
                        <p class="eyebrow">"Operations"</p>
                        <h1>"Your physical infrastructure, clearly."</h1>
                        <p>"Sites, edge agents, attached devices, and enrollment activity in one private control plane."</p>
                    </div>
                    <a class="button button-primary" href="/onboarding">"Create invitation"</a>
                </header>

                <Suspense fallback=move || view! { <DashboardSkeleton/> }>
                    {move || overview.get().map(|result| match result {
                        Ok(overview) => view! { <Dashboard overview/> }.into_any(),
                        Err(OnboardingError::Forbidden) => view! {
                            <section class="sign-in-panel">
                                <div>
                                    <p class="eyebrow">"Organization access"</p>
                                    <h2>"Sign in to view operations."</h2>
                                    <p>"Your organization identity determines which sites, devices, jobs, and security actions are available."</p>
                                </div>
                                <a class="button button-primary" href="/auth/login?return_to=/">"Sign in"</a>
                            </section>
                        }.into_any(),
                        Err(OnboardingError::Disabled) => view! {
                            <section class="sign-in-panel">
                                <div>
                                    <p class="eyebrow">"Development profile"</p>
                                    <h2>"Managed operations are not configured."</h2>
                                    <p>"Enable PostgreSQL, OIDC, onboarding, and the Zenoh client in the server configuration to activate this console."</p>
                                </div>
                            </section>
                        }.into_any(),
                        Err(error) => view! {
                            <section class="sign-in-panel" role="status">
                                <div>
                                    <p class="eyebrow">"Operations unavailable"</p>
                                    <h2>"The controller could not load fleet state."</h2>
                                    <p>{error.to_string()}</p>
                                </div>
                            </section>
                        }.into_any(),
                    })}
                </Suspense>

                <section class="operations-grid" aria-label="Operational areas">
                    <a href="/onboarding"><span>"Enrollment"</span><strong>"Issue and revoke one-time invitations"</strong></a>
                    <div><span>"Jobs"</span><strong>"Durable device work and failures"</strong><small>"API available"</small></div>
                    <div><span>"Security"</span><strong>"Identity, policy, and audit activity"</strong><small>"API available"</small></div>
                </section>
            </main>
        </AppFrame>
    }
}

#[component]
fn Dashboard(overview: FleetOverview) -> impl IntoView {
    view! {
        <section class="metric-grid" aria-label="Fleet summary">
            <article><span>"Sites"</span><strong>{overview.site_count}</strong><small>"configured locations"</small></article>
            <article><span>"Agents"</span><strong>{overview.agent_count}</strong><small>{format!("{} online now", overview.online_agent_count)}</small></article>
            <article><span>"Devices"</span><strong>{overview.device_count}</strong><small>"attached peripherals"</small></article>
        </section>
        <section class="site-panel">
            <div class="section-heading"><div><p class="eyebrow">"Sites"</p><h2>"Operational footprint"</h2></div></div>
            {if overview.sites.is_empty() {
                view! { <div class="compact-empty">"No sites have been configured yet."</div> }.into_any()
            } else {
                view! {
                    <ul class="site-list">
                        {overview.sites.into_iter().map(|site| view! {
                            <li>
                                <div><strong>{site.name}</strong><code>{site.site_id}</code></div>
                                <span>{format!("{} agents · {} devices", site.agent_count, site.device_count)}</span>
                            </li>
                        }).collect_view()}
                    </ul>
                }.into_any()
            }}
        </section>
    }
}

#[component]
fn DashboardSkeleton() -> impl IntoView {
    view! {
        <section class="metric-grid metric-skeleton" aria-label="Loading fleet summary" aria-busy="true">
            <article></article><article></article><article></article>
        </section>
    }
}
