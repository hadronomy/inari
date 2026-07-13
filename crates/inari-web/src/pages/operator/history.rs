use leptos::form::ActionForm;
use leptos::prelude::*;

use super::format_expiry;
use crate::components::{InlineNotice, StateBadge};
use crate::server_fns::{InvitationStatus, RevokeInvitation};

#[component]
pub(super) fn InvitationHistory(
    invitations: Vec<InvitationStatus>,
    revoke: ServerAction<RevokeInvitation>,
    refresh: RwSignal<u64>,
    history_open: RwSignal<bool>,
) -> impl IntoView {
    let invitation_count = invitations.len();
    let history_summary = match invitation_count {
        0 => "No invitations have been created yet.".to_owned(),
        1 => "1 recent invitation".to_owned(),
        count => format!("{count} recent invitations"),
    };

    view! {
        <section
            class="invitation-history t-acc"
            data-open=move || history_open.get().to_string()
            aria-labelledby="invitation-history-title"
        >
            <header class="history-heading">
                <div>
                    <p class="eyebrow">"Activity"</p>
                    <h2 id="invitation-history-title">"Recent invitations"</h2>
                    <p>{history_summary}</p>
                </div>
                <div class="history-actions">
                    <button
                        class="button button-quiet button-compact"
                        type="button"
                        on:click=move |_| refresh.update(|version| *version += 1)
                    >
                        "Refresh"
                    </button>
                    {(invitation_count > 0).then(|| view! {
                        <button
                            class="button button-quiet button-compact history-toggle t-acc-head"
                            type="button"
                            aria-expanded=move || history_open.get().to_string()
                            aria-controls="recent-invitations"
                            on:click=move |_| history_open.update(|open| *open = !*open)
                        >
                            <span>{move || if history_open.get() { "Hide" } else { "Show" }}</span>
                            <span class="t-acc-chevron" aria-hidden="true">
                                <svg viewBox="0 0 16 16"><path d="M4 6.5L8 10.5L12 6.5"/></svg>
                            </span>
                        </button>
                    })}
                </div>
            </header>

            {(invitation_count == 0).then(|| view! {
                <div class="history-empty">
                    <p>"New invitations will appear here as they move through enrollment."</p>
                </div>
            })}

            {(invitation_count > 0).then(|| view! {
                <div
                    id="recent-invitations"
                    class="t-acc-panel"
                    aria-hidden=move || (!history_open.get()).to_string()
                    inert=move || !history_open.get()
                >
                    <div class="t-acc-panel-inner">
                        <ul class="invitation-list">
                            {invitations.into_iter().map(|invitation| {
                                let invitation_id = invitation.invitation_id;
                                let revocable = invitation.state.is_revocable();
                                view! {
                                    <li>
                                        <div class="record-main">
                                            <strong>{invitation.label.unwrap_or_else(|| "Unlabeled agent".to_owned())}</strong>
                                            <code>{invitation_id.clone()}</code>
                                        </div>
                                        <div class="record-state">
                                            <StateBadge state=invitation.state/>
                                            <time datetime=invitation.expires_at.to_rfc3339()>
                                                {format_expiry(invitation.expires_at)}
                                            </time>
                                        </div>
                                        {revocable.then(|| view! {
                                            <ActionForm action=revoke>
                                                <input type="hidden" name="invitation_id" value=invitation_id/>
                                                <button
                                                    class="button button-danger"
                                                    type="submit"
                                                    disabled=move || revoke.pending().get()
                                                >
                                                    "Revoke"
                                                </button>
                                            </ActionForm>
                                        })}
                                    </li>
                                }
                            }).collect_view()}
                        </ul>
                    </div>
                </div>
            })}

            {move || revoke.value().get().map(|result| match result {
                Ok(invitation) => view! {
                    <InlineNotice kind="success">
                        {format!("{} is now {}.", invitation.invitation_id, invitation.state.label())}
                    </InlineNotice>
                }.into_any(),
                Err(error) => view! {
                    <InlineNotice kind="error">{error.to_string()}</InlineNotice>
                }.into_any(),
            })}
        </section>
    }
}
