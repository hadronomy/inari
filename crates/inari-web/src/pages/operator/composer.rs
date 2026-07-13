use leptos::form::ActionForm;
use leptos::prelude::*;

use super::clipboard::CopyButton;
use super::format_expiry;
use crate::components::InlineNotice;
use crate::server_fns::{IssueInvitation, IssuedInvitation, OnboardingError};

#[component]
pub(super) fn InvitationComposer(
    action: ServerAction<IssueInvitation>,
    label_open: RwSignal<bool>,
) -> impl IntoView {
    view! {
        <article
            class=move || if matches!(action.value().get(), Some(Ok(_))) {
                "invitation-composer invitation-composer-ready"
            } else {
                "invitation-composer"
            }
        >
            {move || match action.value().get() {
                Some(Ok(invitation)) => view! {
                    <InvitationReady invitation action/>
                }.into_any(),
                result => {
                    let error = result.and_then(Result::err);
                    view! {
                        <div class="composer-intro">
                            <div class="composer-symbol" aria-hidden="true">
                                <img src="/inari-mark.svg" alt=""/>
                            </div>
                            <div>
                                <p class="eyebrow">"New enrollment"</p>
                                <h2>"Create an invitation"</h2>
                                <p>"Give a new edge agent a one-time path into this controller."</p>
                            </div>
                        </div>

                        <ActionForm action>
                            <div
                                class="label-disclosure t-acc"
                                data-open=move || label_open.get().to_string()
                            >
                                <button
                                    class="label-disclosure-head t-acc-head"
                                    type="button"
                                    aria-expanded=move || label_open.get().to_string()
                                    aria-controls="invitation-label"
                                    on:click=move |_| label_open.update(|open| *open = !*open)
                                >
                                    <span>
                                        <strong>"Add a label"</strong>
                                        <small>"Optional · useful when several agents are being prepared"</small>
                                    </span>
                                    <span class="t-acc-chevron" aria-hidden="true">
                                        <svg viewBox="0 0 16 16">
                                            <path d="M4 6.5L8 10.5L12 6.5"/>
                                        </svg>
                                    </span>
                                </button>
                                <div
                                    id="invitation-label"
                                    class="t-acc-panel"
                                    aria-hidden=move || (!label_open.get()).to_string()
                                    inert=move || !label_open.get()
                                >
                                    <div class="t-acc-panel-inner">
                                        <label>
                                            <span>"Agent label"</span>
                                            <input
                                                name="label"
                                                maxlength="120"
                                                autocomplete="off"
                                                placeholder="Front desk"
                                            />
                                        </label>
                                    </div>
                                </div>
                            </div>

                            <div class="composer-action">
                                <p>"One use · identity bound · expires automatically"</p>
                                <button
                                    class="button button-primary"
                                    type="submit"
                                    disabled=move || action.pending().get()
                                >
                                    {move || if action.pending().get() {
                                        "Creating…"
                                    } else {
                                        "Create invitation"
                                    }}
                                </button>
                            </div>
                        </ActionForm>

                        {error.map(|error| match error {
                            OnboardingError::Forbidden => view! {
                                <InlineNotice kind="error">
                                    "Your account cannot create invitations. "
                                    <a href="/auth/login?return_to=/onboarding">"Sign in with another account"</a>
                                </InlineNotice>
                            }.into_any(),
                            error => view! {
                                <InlineNotice kind="error">{error.to_string()}</InlineNotice>
                            }.into_any(),
                        })}
                    }.into_any()
                },
            }}
        </article>
    }
}

#[component]
fn InvitationReady(
    invitation: IssuedInvitation,
    action: ServerAction<IssueInvitation>,
) -> impl IntoView {
    let invitation_url = invitation.invitation_url.to_string();
    let manual_code = invitation.manual_code;
    let expires_at = invitation.expires_at;

    view! {
        <div class="invitation-ready">
            <p class="sr-only" role="status" aria-live="polite">
                {format!("Invitation ready. {}.", format_expiry(expires_at))}
            </p>
            <header class="ready-heading">
                <div class="ready-check" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="m6.5 12.5 3.3 3.3 7.7-8"/></svg>
                </div>
                <div>
                    <p class="eyebrow">"Invitation ready"</p>
                    <h2>"Pass it to the enrolling device"</h2>
                </div>
                <time datetime=expires_at.to_rfc3339()>{format_expiry(expires_at)}</time>
            </header>

            <div class="ready-layout">
                <div class="qr-frame">
                    <img class="qr" src=invitation.qr_data_uri alt="Enrollment QR code"/>
                    <span>"Scan with the Inari tray"</span>
                </div>
                <div class="ready-copy">
                    <p>"Share the QR, link, or code with the person setting up this agent. The invitation stops working after its first use."</p>
                    <div class="manual-code">
                        <span>"Manual code"</span>
                        <code>{manual_code.clone()}</code>
                    </div>
                    <div class="ready-actions">
                        <a class="button button-primary" href=invitation_url.clone()>"Open setup"</a>
                        <CopyButton value=invitation_url label="Copy link"/>
                        <CopyButton value=manual_code label="Copy code"/>
                    </div>
                </div>
            </div>

            <footer class="ready-footer">
                <span>"Keep this invitation within the intended enrollment handoff."</span>
                <button
                    class="button button-quiet"
                    type="button"
                    on:click=move |_| action.value().set(None)
                >
                    "Create another"
                </button>
            </footer>
        </div>
    }
}
