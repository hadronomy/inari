use leptos::prelude::*;
use leptos_meta::Title;

use crate::components::AppFrame;
use crate::server_fns::{
    ControllerComponentKind, ControllerComponentState, ControllerSnapshot, FleetAvailability,
    FleetOverview, OnboardingError, load_controller_snapshot, load_fleet_overview,
};

#[component]
pub fn HomePage() -> impl IntoView {
    let controller = Resource::new(|| (), |_| load_controller_snapshot());
    let fleet = Resource::new(|| (), |_| load_fleet_overview());
    let controller_summary = controller;
    let controller_services = controller;

    view! {
        <Title text="Operations — Inari"/>
        <AppFrame>
            <main class="dashboard-page">
                <header class="dashboard-heading">
                    <div>
                        <p class="eyebrow">"Dashboard"</p>
                        <h1>"Operations"</h1>
                        <p>"Live fleet activity, controller health, and the work that needs attention across your private device network."</p>
                    </div>
                </header>

                <Suspense fallback=ControllerStripSkeleton>
                    {move || controller_summary.get().map(|result| match result {
                        Ok(snapshot) => view! { <ControllerStrip snapshot/> }.into_any(),
                        Err(_) => view! { <ControllerStripUnavailable/> }.into_any(),
                    })}
                </Suspense>

                <div class="dashboard-columns">
                    <div class="dashboard-primary">
                        <Suspense fallback=FleetSkeleton>
                            {move || fleet.get().map(|result| match result {
                                Ok(FleetAvailability::Available(overview)) => view! { <FleetDashboard overview/> }.into_any(),
                                Ok(FleetAvailability::Disabled) => view! { <SetupPanel/> }.into_any(),
                                Err(OnboardingError::Forbidden) => view! { <AccessPanel/> }.into_any(),
                                Err(error) => view! { <FleetUnavailable error/> }.into_any(),
                            })}
                        </Suspense>
                    </div>

                    <Suspense fallback=ServicePanelSkeleton>
                        {move || controller_services.get().map(|result| match result {
                            Ok(snapshot) => view! { <ServicePanel snapshot/> }.into_any(),
                            Err(_) => view! { <ServicePanelUnavailable/> }.into_any(),
                        })}
                    </Suspense>
                </div>
            </main>
        </AppFrame>
    }
}

#[component]
fn ControllerStrip(snapshot: ControllerSnapshot) -> impl IntoView {
    let active_services = snapshot
        .components
        .iter()
        .filter(|component| component.state == ControllerComponentState::Ready)
        .count();
    let enrollment = snapshot
        .components
        .iter()
        .find(|component| component.kind == ControllerComponentKind::Enrollment)
        .map(|component| component.state.label())
        .unwrap_or("Unknown");
    let headline = if snapshot.ready { "Controller ready" } else { "Controller needs attention" };

    view! {
        <section class="controller-strip" aria-label="Controller summary" role="status">
            <div class="controller-summary">
                <div>
                    <strong>{headline}</strong>
                    <span>{format!("{} profile", snapshot.environment.label())}</span>
                </div>
            </div>
            <dl class="controller-facts">
                <div><dt>"Active services"</dt><dd>{format!("{active_services} of {}", snapshot.components.len())}</dd></div>
                <div><dt>"Enrollment"</dt><dd>{enrollment}</dd></div>
            </dl>
        </section>
    }
}

#[component]
fn ControllerStripUnavailable() -> impl IntoView {
    view! {
        <section class="controller-strip controller-strip-unavailable" role="status">
            <div class="controller-summary">
                <div><strong>"Status unavailable"</strong><span>"The controller summary could not be loaded"</span></div>
            </div>
            <a class="text-link" href="/readyz">"View diagnostics"</a>
        </section>
    }
}

#[component]
fn FleetDashboard(overview: FleetOverview) -> impl IntoView {
    view! {
        <section class="metric-grid" aria-label="Fleet summary">
            <article><span>"Sites"</span><strong>{overview.site_count}</strong><small>"configured locations"</small></article>
            <article><span>"Agents"</span><strong>{overview.agent_count}</strong><small>{format!("{} online now", overview.online_agent_count)}</small></article>
            <article><span>"Devices"</span><strong>{overview.device_count}</strong><small>"attached peripherals"</small></article>
        </section>
        <section class="site-panel">
            <div class="section-heading">
                <div><p class="eyebrow">"Fleet"</p><h2>"Sites"</h2></div>
                <a class="button button-primary" href="/onboarding">"Create invitation"</a>
            </div>
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
fn AccessPanel() -> impl IntoView {
    view! {
        <section class="dashboard-panel action-panel">
            <div>
                <p class="eyebrow">"Organization access"</p>
                <h2>"Sign in to see fleet operations."</h2>
                <p>"Your organization identity determines which sites, devices, jobs, and security actions are available."</p>
            </div>
            <a class="button button-primary" href="/auth/login?return_to=/">"Sign in"</a>
        </section>
    }
}

#[component]
fn SetupPanel() -> impl IntoView {
    view! {
        <section class="dashboard-panel setup-panel">
            <div>
                <p class="eyebrow">"Managed plane"</p>
                <h2>"Finish controller setup."</h2>
                <p>"The controller is reachable, but fleet operations remain inactive until persistence, identity, enrollment, and Zenoh are configured."</p>
            </div>
            <div class="setup-command">
                <span>"Start here"</span>
                <code>"inari-server config validate"</code>
            </div>
            <div class="panel-actions">
                <a class="button button-quiet" href="/readyz">"View readiness"</a>
                <span>"Then run "<code>"config explain"</code>" for field-level guidance."</span>
            </div>
        </section>
    }
}

#[component]
fn FleetUnavailable(error: OnboardingError) -> impl IntoView {
    view! {
        <section class="dashboard-panel action-panel" role="status">
            <div>
                <p class="eyebrow">"Fleet unavailable"</p>
                <h2>"Operations could not be loaded."</h2>
                <p>{error.to_string()}</p>
            </div>
            <a class="button button-quiet" href="/readyz">"View readiness"</a>
        </section>
    }
}

#[component]
fn ServicePanel(snapshot: ControllerSnapshot) -> impl IntoView {
    view! {
        <aside class="service-panel" aria-labelledby="service-heading">
            <div class="service-panel-heading">
                <div><p class="eyebrow">"System"</p><h2 id="service-heading">"Service readiness"</h2></div>
                <a class="text-link" href="/readyz">"Diagnostics"</a>
            </div>
            <ul class="service-list">
                {snapshot.components.into_iter().map(|component| {
                    let state_class = component.state.class();
                    view! {
                        <li>
                            <div class="service-copy">
                                <strong>{component.kind.label()}</strong>
                                <small>{component.summary}</small>
                            </div>
                            <span class=state_class>{component.state.label()}</span>
                        </li>
                    }
                }).collect_view()}
            </ul>
        </aside>
    }
}

#[component]
fn ServicePanelUnavailable() -> impl IntoView {
    view! {
        <aside class="service-panel" role="status">
            <div class="service-panel-heading"><div><p class="eyebrow">"System"</p><h2>"Service readiness"</h2></div></div>
            <p class="service-error">"Status details are temporarily unavailable."</p>
        </aside>
    }
}

#[component]
fn ControllerStripSkeleton() -> impl IntoView {
    view! { <section class="controller-strip controller-strip-skeleton" aria-label="Loading controller summary" aria-busy="true"></section> }
}

#[component]
fn FleetSkeleton() -> impl IntoView {
    view! {
        <section class="metric-grid metric-skeleton" aria-label="Loading fleet summary" aria-busy="true">
            <article></article><article></article><article></article>
        </section>
        <section class="site-panel panel-skeleton" aria-hidden="true"></section>
    }
}

#[component]
fn ServicePanelSkeleton() -> impl IntoView {
    view! { <aside class="service-panel service-panel-skeleton" aria-label="Loading service readiness" aria-busy="true"></aside> }
}
