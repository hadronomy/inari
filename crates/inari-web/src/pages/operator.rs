use leptos::form::ActionForm;
use leptos::prelude::*;
use leptos_meta::Title;

use crate::components::{AppFrame, InlineNotice, StateBadge};
use crate::server_fns::{IssueInvitation, OnboardingError, RevokeInvitation, load_invitations};

#[component]
pub fn OperatorPage() -> impl IntoView {
    let issue = ServerAction::<IssueInvitation>::new();
    let revoke = ServerAction::<RevokeInvitation>::new();
    let refresh = RwSignal::new(0_u64);
    let invitations = Resource::new(
        move || (issue.version().get(), revoke.version().get(), refresh.get()),
        |_| load_invitations(),
    );

    view! {
        <Title text="Managed onboarding — Inari"/>
        <AppFrame>
            <main class="operator-page">
                <header class="page-heading">
                    <div>
                        <p class="eyebrow">"Operator workspace"</p>
                        <h1>"Invite an edge agent."</h1>
                        <p>"Invitations are short-lived, one-use, and bound to the enrolling agent identity."</p>
                    </div>
                    <span class="security-note"><span aria-hidden="true">"◆"</span>"OIDC protected"</span>
                </header>

                <section class="operator-grid">
                    <div class="panel issue-panel">
                        <div class="panel-heading">
                            <div><span class="step">"01"</span><h2>"Issue invitation"</h2></div>
                            <p>"Short-lived · one use · identity bound"</p>
                        </div>
                        <ActionForm action=issue>
                            <div class="form-stack">
                                <label>
                                    <span>"Agent label" <small>"Optional"</small></span>
                                    <input name="label" maxlength="120" placeholder="Front desk"/>
                                </label>
                                <button class="button button-primary button-wide" type="submit" disabled=move || issue.pending().get()>
                                    {move || if issue.pending().get() { "Issuing…" } else { "Issue secure invitation" }}
                                </button>
                            </div>
                        </ActionForm>
                        {move || issue.value().get().map(|result| match result {
                            Ok(invitation) => view! {
                                <div class="invitation-kit" aria-live="polite">
                                    <img class="qr" src=invitation.qr_data_uri alt="Enrollment QR code"/>
                                    <div>
                                        <p class="eyebrow">"Invitation ready"</p>
                                        <h3>"Scan or enter the code"</h3>
                                        <code>{invitation.manual_code}</code>
                                        <a class="text-link" href=invitation.invitation_url.to_string()>"Open setup page" <span aria-hidden="true">"↗"</span></a>
                                        <small class="expiry">{format!("Expires {}", invitation.expires_at.to_rfc3339())}</small>
                                    </div>
                                </div>
                            }.into_any(),
                            Err(OnboardingError::Forbidden) => view! {
                                <InlineNotice kind="error">"Sign in with an enrollment administrator role to issue invitations. "<a href="/auth/login?return_to=/onboarding">"Sign in"</a></InlineNotice>
                            }.into_any(),
                            Err(error) => view! { <InlineNotice kind="error">{error.to_string()}</InlineNotice> }.into_any(),
                        })}
                    </div>

                    <aside class="panel ledger-panel">
                        <div class="panel-heading">
                            <div><span class="step">"02"</span><h2>"Invitation ledger"</h2></div>
                            <button class="button button-quiet" type="button" on:click=move |_| refresh.update(|version| *version += 1)>"Refresh"</button>
                        </div>
                        <Suspense fallback=move || view! { <div class="empty-state" aria-busy="true"><p>"Loading invitations…"</p></div> }>
                        {move || invitations.get().map(|result| match result {
                            Ok(invitations) if invitations.is_empty() => view! {
                                <div class="empty-state"><span aria-hidden="true">"◇"</span><p>"No invitations yet."</p></div>
                            }.into_any(),
                            Ok(invitations) => view! {
                                <ul class="invitation-list">
                                    {invitations.into_iter().map(|invitation| {
                                        let invitation_id = invitation.invitation_id.clone();
                                        view! {
                                            <li>
                                                <div class="record-heading">
                                                    <strong>{invitation.label.unwrap_or_else(|| invitation_id.clone())}</strong>
                                                    <StateBadge state=invitation.state/>
                                                </div>
                                                <span class="record-id">{invitation_id.clone()}</span>
                                                <div class="record-footer">
                                                    <time>{invitation.expires_at.to_rfc3339()}</time>
                                                    <ActionForm action=revoke>
                                                        <input type="hidden" name="invitation_id" value=invitation_id/>
                                                        <button class="button button-danger" type="submit" disabled=move || revoke.pending().get()>"Revoke"</button>
                                                    </ActionForm>
                                                </div>
                                            </li>
                                        }
                                    }).collect_view()}
                                </ul>
                            }.into_any(),
                            Err(OnboardingError::Forbidden) => view! {
                                <InlineNotice kind="error">"Sign in with an enrollment administrator role to view invitations. "<a href="/auth/login?return_to=/onboarding">"Sign in"</a></InlineNotice>
                            }.into_any(),
                            Err(error) => view! { <InlineNotice kind="error">{error.to_string()}</InlineNotice> }.into_any(),
                        })}
                        </Suspense>
                        {move || revoke.value().get().map(|result| match result {
                            Ok(invitation) => view! { <InlineNotice kind="success">{format!("{} is now {}.", invitation.invitation_id, invitation.state.label())}</InlineNotice> }.into_any(),
                            Err(error) => view! { <InlineNotice kind="error">{error.to_string()}</InlineNotice> }.into_any(),
                        })}
                    </aside>
                </section>
            </main>
        </AppFrame>
    }
}
