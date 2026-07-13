mod clipboard;
mod composer;
mod history;

use chrono::{DateTime, Utc};
use leptos::prelude::*;
use leptos_meta::Title;

use self::composer::InvitationComposer;
use self::history::InvitationHistory;
use crate::components::AppFrame;
use crate::server_fns::{IssueInvitation, OnboardingError, RevokeInvitation, load_invitations};

#[component]
fn OnboardingUnavailable(error: OnboardingError, refresh: RwSignal<u64>) -> impl IntoView {
    let (eyebrow, title, description) = match error {
        OnboardingError::Disabled => (
            "Enrollment unavailable",
            "This controller is not accepting new agents",
            "Ask an administrator to enable managed enrollment, then return here.",
        ),
        OnboardingError::Forbidden => (
            "Access required",
            "Your account cannot issue invitations",
            "Use an account with enrollment administration access.",
        ),
        _ => (
            "Service unavailable",
            "Invitations could not be loaded",
            "The controller did not complete the request. Try again in a moment.",
        ),
    };

    view! {
        <article class="onboarding-unavailable">
            <div>
                <p class="eyebrow">{eyebrow}</p>
                <h2>{title}</h2>
                <p>{description}</p>
            </div>
            <div class="unavailable-actions">
                {(matches!(error, OnboardingError::Forbidden)).then(|| view! {
                    <a class="button button-primary" href="/auth/login?return_to=/onboarding">"Sign in"</a>
                })}
                <button
                    class="button button-quiet"
                    type="button"
                    on:click=move |_| refresh.update(|version| *version += 1)
                >
                    "Try again"
                </button>
            </div>
        </article>
    }
}

#[component]
pub fn OperatorPage() -> impl IntoView {
    let issue = ServerAction::<IssueInvitation>::new();
    let revoke = ServerAction::<RevokeInvitation>::new();
    let refresh = RwSignal::new(0_u64);
    let label_open = RwSignal::new(false);
    let history_open = RwSignal::new(false);
    let invitations = Resource::new(
        move || (issue.version().get(), revoke.version().get(), refresh.get()),
        |_| load_invitations(),
    );

    view! {
        <Title text="Invite an agent — Inari"/>
        <AppFrame>
            <main class="operator-page">
                <header class="page-heading">
                    <div>
                        <p class="eyebrow">"Enrollment"</p>
                        <h1>"Invite an edge agent"</h1>
                        <p>"Create a trusted handoff for a new device, then follow its progress from this controller."</p>
                    </div>
                </header>

                <div class="onboarding-workspace">
                    <Suspense fallback=move || view! {
                        <div class="onboarding-skeleton" aria-busy="true" aria-label="Loading invitation workspace">
                            <div></div><div></div><div></div>
                        </div>
                    }>
                        {move || invitations.get().map(|result| match result {
                            Ok(invitations) => view! {
                                <InvitationComposer action=issue label_open/>
                                <InvitationHistory invitations revoke refresh history_open/>
                            }.into_any(),
                            Err(error) => view! {
                                <OnboardingUnavailable error refresh/>
                            }.into_any(),
                        })}
                    </Suspense>
                </div>
            </main>
        </AppFrame>
    }
}

fn format_expiry(expires_at: DateTime<Utc>) -> String {
    format!("Expires {} UTC", expires_at.format("%d %b · %H:%M"))
}
