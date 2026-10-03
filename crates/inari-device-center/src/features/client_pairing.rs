use std::sync::Arc;

use chrono::{DateTime, Local, Utc};
use gpui::{
    AppContext as _, Context, Entity, IntoElement, ParentElement as _, Render, Styled,
    Subscription, Task, Window, div, px,
};
use gpui_component::{
    StyledExt as _,
    input::{Input, InputEvent, InputState},
};
use inari_agent_client::{
    AgentClientResult, PairingDecision, PairingRequest, PairingRequestId, PairingRequestState,
};

use crate::{
    infrastructure::AgentRuntime,
    ui::{
        banner::Banner,
        button::Button,
        content::{PageTitle, Section, Typography as _, page},
        readout::readout,
        status::Tone,
        theme::{ActiveTheme as _, Theme},
    },
};

enum ReviewState {
    Idle,
    Loading,
    Ready(Box<PairingRequest>),
    Deciding(Box<PairingRequest>),
    Failed(String),
}

pub struct ClientPairingView {
    input: Entity<InputState>,
    state: ReviewState,
    runtime: Arc<AgentRuntime>,
    _task: Task<()>,
    _expiry_task: Task<()>,
    _input_subscription: Subscription,
}

impl ClientPairingView {
    pub fn new(runtime: Arc<AgentRuntime>, window: &mut Window, cx: &mut Context<Self>) -> Self {
        let input = cx.new(|cx| {
            InputState::new(window, cx).placeholder("Paste the Pairing Request link or ID")
        });
        let subscription = cx.subscribe(&input, |view, _, event, cx| match event {
            InputEvent::PressEnter { .. } => view.review(cx),
            _ => cx.notify(),
        });
        Self {
            input,
            state: ReviewState::Idle,
            runtime,
            _task: Task::ready(()),
            _expiry_task: Task::ready(()),
            _input_subscription: subscription,
        }
    }

    pub fn focus(&self, window: &mut Window, cx: &mut Context<Self>) {
        self.input
            .update(cx, |input, cx| input.focus(window, cx));
    }

    pub fn open_request(
        &mut self,
        id: PairingRequestId,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.input
            .update(cx, |input, cx| input.set_value(id.to_string(), window, cx));
        self.load_request(id, cx);
    }

    pub fn show_error(&mut self, message: String, cx: &mut Context<Self>) {
        self._task = Task::ready(());
        self._expiry_task = Task::ready(());
        self.state = ReviewState::Failed(message);
        cx.notify();
    }

    fn review(&mut self, cx: &mut Context<Self>) {
        if self.working() {
            return;
        }
        match PairingRequestId::parse_input(self.input.read(cx).value().as_str()) {
            Ok(id) => self.load_request(id, cx),
            Err(error) => self.show_error(error.to_string(), cx),
        }
    }

    fn load_request(&mut self, id: PairingRequestId, cx: &mut Context<Self>) {
        let response = self.runtime.review_client_pairing(id);
        self.state = ReviewState::Loading;
        self._expiry_task = Task::ready(());
        self._task = Self::receive(response, cx);
        cx.notify();
    }

    fn decide(&mut self, decision: PairingDecision, cx: &mut Context<Self>) {
        let ReviewState::Ready(request) = &self.state else {
            return;
        };
        if !decision_allowed(request, self.input.read(cx).value().as_str(), Utc::now()) {
            return;
        }
        let response = self
            .runtime
            .decide_client_pairing(request.id.clone(), decision);
        self.state = ReviewState::Deciding(request.clone());
        self._task = Self::receive(response, cx);
        cx.notify();
    }

    fn receive(
        response: tokio::sync::oneshot::Receiver<AgentClientResult<PairingRequest>>,
        cx: &mut Context<Self>,
    ) -> Task<()> {
        cx.spawn(async move |view, cx| {
            let response = response.await;
            let _ = view.update(cx, |view, cx| {
                match response {
                    Ok(Ok(request)) => {
                        view._expiry_task = Task::ready(());
                        if matches!(request.state, PairingRequestState::Pending | PairingRequestState::Approved) {
                            let delay = (request.expires_at - Utc::now()).to_std().unwrap_or_default();
                            let timer = cx.background_executor().timer(delay);
                            view._expiry_task = cx.spawn(async move |view, cx| {
                                timer.await;
                                let _ = view.update(cx, |_, cx| cx.notify());
                            });
                        }
                        view.state = ReviewState::Ready(Box::new(request));
                    },
                    Ok(Err(error)) => {
                        tracing::warn!(%error, "Client Pairing review or decision failed");
                        view.state = ReviewState::Failed(
                            "The Agent could not complete this request. Select Review request to read its current state.".into(),
                        );
                    },
                    Err(_) => {
                        view.state = ReviewState::Failed(
                            "The Agent stopped before it replied. Select Review request to read its current state.".into(),
                        );
                    },
                }
                cx.notify();
            });
        })
    }

    fn working(&self) -> bool {
        matches!(self.state, ReviewState::Loading | ReviewState::Deciding(_))
    }
}

fn decision_allowed(request: &PairingRequest, input: &str, at: DateTime<Utc>) -> bool {
    request.can_decide_at(at)
        && PairingRequestId::parse_input(input).is_ok_and(|id| id == request.id)
}

fn request_status(
    request: &PairingRequest,
    at: DateTime<Utc>,
) -> (&'static str, &'static str, Tone) {
    match request.state {
        PairingRequestState::Pending | PairingRequestState::Approved
            if at >= request.expires_at =>
        {
            ("Expired", "Create a new Pairing Request in Odoo.", Tone::Caution)
        },
        PairingRequestState::Pending => (
            "Awaiting approval",
            "Compare the phrase and requested access with Odoo before you decide.",
            Tone::Neutral,
        ),
        PairingRequestState::Approved => {
            ("Browser approved", "Return to Odoo to complete Client Pairing.", Tone::Positive)
        },
        PairingRequestState::Completed => {
            ("Client Pairing complete", "This browser can use its approved access.", Tone::Positive)
        },
        PairingRequestState::Denied => {
            ("Request denied", "This request cannot grant access to the browser.", Tone::Neutral)
        },
        PairingRequestState::Canceled => {
            ("Request canceled", "Create a new Pairing Request in Odoo.", Tone::Neutral)
        },
        PairingRequestState::Expired => {
            ("Expired", "Create a new Pairing Request in Odoo.", Tone::Caution)
        },
    }
}

fn permission_description(permission: &str) -> &str {
    match permission {
        "device_work:receipt_image" => "Print receipts",
        "device_work:drawer" => "Open the cash drawer",
        "device_read:scale" => "Read the scale",
        "device_read:scanner" => "Read the scanner",
        "device_test:run" => "Run device tests",
        "events:read" => "Read device events",
        "jobs:read" => "Read device jobs",
        "jobs:submit" => "Submit device jobs",
        other => other,
    }
}

impl Render for ClientPairingView {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let theme = cx.inari();
        let working = self.working();
        let mut view = page("client-pairing")
            .child(PageTitle::new("Client Pairing", "Review Odoo's request to use this Agent."))
            .child(
                Section::new("Pairing Request")
                    .child(Input::new(&self.input).disabled(working))
                    .child(
                        Button::new("review-client-pairing")
                            .label("Review request")
                            .disabled(working)
                            .on_click({
                                let view = cx.entity().downgrade();
                                move |_, cx| {
                                    let _ = view.update(cx, |view, cx| view.review(cx));
                                }
                            }),
                    ),
            );
        match &self.state {
            ReviewState::Idle => {},
            ReviewState::Loading => {
                view = view.child(Banner::new(
                    "pairing-progress",
                    Tone::Busy,
                    "Read the request",
                    "The Agent is reading the Pairing Request.",
                ))
            },
            ReviewState::Failed(message) => {
                view = view.child(Banner::new(
                    "pairing-error",
                    Tone::Critical,
                    "Request unavailable",
                    message.clone(),
                ))
            },
            ReviewState::Ready(request) | ReviewState::Deciding(request) => {
                let now = Utc::now();
                let allowed = !working
                    && decision_allowed(request, self.input.read(cx).value().as_str(), now);
                let (title, message, tone) = if working {
                    (
                        "Save the decision",
                        "Wait for the Agent to reply. You can then read the request's current state.",
                        Tone::Busy,
                    )
                } else if !PairingRequestId::parse_input(self.input.read(cx).value().as_str())
                    .is_ok_and(|id| id == request.id)
                {
                    (
                        "Request changed",
                        "Select Review request to read the request in the input field.",
                        Tone::Caution,
                    )
                } else {
                    request_status(request, now)
                };
                let scope = &request.scope;
                let approve = cx.entity().downgrade();
                let deny = approve.clone();
                view = view.child(Banner::new("pairing-state", tone, title, message))
                    .child(Section::new("Compare with Odoo")
                        .child(div().text_display().text_color(theme.text).child(request.phrase.clone()))
                        .child(div().text_body().max_w(px(Theme::MEASURE))
                            .child("Approve only when every word matches the phrase in Odoo and you recognize the requested access.")))
                    .child(Section::new("Requested access").child(readout("pairing-scope")
                        .fact("Odoo origin", scope.browser_origin.to_string())
                        .fact("Agent", scope.agent_id.clone())
                        .fact("Agent Endpoint", scope.agent_endpoint.to_string())
                        .fact("Organization", scope.organization_id.clone())
                        .fact("Site", scope.site_id.clone())
                        .fact("Database", scope.database.clone())
                        .fact("Company", scope.company_id.clone())
                        .fact("POS configuration", scope.pos_configuration_id.clone().unwrap_or_else(|| "Not requested".into()))
                        .fact("Permissions", request.requested_permissions.iter().map(|permission| permission_description(permission)).collect::<Vec<_>>().join("\n"))
                        .fact("Expires", request.expires_at.with_timezone(&Local).format("%Y-%m-%d %H:%M:%S %Z").to_string())
                        .fact("Pairing Request", request.id.to_string())))
                    .child(div().h_flex().flex_wrap().gap(px(Theme::SPACE_SM))
                        .child(Button::new("approve-client-pairing").primary().label("Approve browser").disabled(!allowed)
                            .on_click(move |_, cx| { let _ = approve.update(cx, |view, cx| view.decide(PairingDecision::Approve, cx)); }))
                        .child(Button::new("deny-client-pairing").ghost().label("Deny request").disabled(!allowed)
                            .on_click(move |_, cx| { let _ = deny.update(cx, |view, cx| view.decide(PairingDecision::Deny, cx)); })));
            },
        }
        view
    }
}

#[cfg(test)]
mod tests {
    use gpui::TestAppContext;
    use inari_agent_client::PairingScope;

    use super::*;

    fn request() -> PairingRequest {
        PairingRequest {
            id: PairingRequestId::parse("req_123").unwrap(),
            scope: PairingScope {
                agent_id: "agent_1".into(),
                agent_endpoint: "https://agent.example.com:7310/"
                    .parse()
                    .unwrap(),
                browser_origin: "https://odoo.example.com/"
                    .parse()
                    .unwrap(),
                database: "odoo".into(),
                company_id: "1".into(),
                organization_id: "org_1".into(),
                site_id: "site_1".into(),
                pos_configuration_id: Some("1".into()),
            },
            phrase: "maple river cloud stone".into(),
            requested_permissions: vec!["device_work:receipt_image".into()],
            expires_at: "2026-10-03T10:05:00Z".parse().unwrap(),
            state: PairingRequestState::Pending,
        }
    }

    #[test]
    fn only_the_reviewed_pending_request_can_be_decided_before_expiry() {
        let mut request = request();
        let before = request.expires_at - chrono::Duration::seconds(1);
        assert!(decision_allowed(&request, "inari://pairing/req_123", before));
        for input in ["req_other", "", "inari://pairing/req_123?approve=true"] {
            assert!(!decision_allowed(&request, input, before));
        }
        assert!(!decision_allowed(&request, "req_123", request.expires_at));
        for state in [
            PairingRequestState::Approved,
            PairingRequestState::Denied,
            PairingRequestState::Canceled,
            PairingRequestState::Expired,
            PairingRequestState::Completed,
        ] {
            request.state = state;
            assert!(!decision_allowed(&request, "req_123", before));
        }
    }

    #[test]
    fn expired_and_approved_requests_explain_the_next_odoo_action() {
        let mut request = request();
        let (title, detail, _) = request_status(&request, request.expires_at);
        assert_eq!(title, "Expired");
        assert_eq!(detail, "Create a new Pairing Request in Odoo.");
        request.state = PairingRequestState::Approved;
        let (title, detail, _) =
            request_status(&request, request.expires_at - chrono::Duration::seconds(1));
        assert_eq!(title, "Browser approved");
        assert_eq!(detail, "Return to Odoo to complete Client Pairing.");
        assert_eq!(request_status(&request, request.expires_at).0, "Expired");
        request.state = PairingRequestState::Completed;
        assert_eq!(request_status(&request, request.expires_at).0, "Client Pairing complete");
    }

    #[gpui::test]
    fn editing_the_request_field_invalidates_the_previous_review(cx: &mut TestAppContext) {
        cx.update(gpui_component::init);
        let cx = cx.add_empty_window();
        let input = cx.update(|window, cx| cx.new(|cx| InputState::new(window, cx)));
        let request = request();
        let before = request.expires_at - chrono::Duration::seconds(1);
        for (value, allowed) in [("req_123", true), ("req_other", false), ("", false)] {
            cx.update(|window, cx| {
                input.update(cx, |input, cx| input.set_value(value, window, cx));
            });
            let value = input.read_with(cx, |input, _| input.value());
            assert_eq!(decision_allowed(&request, value.as_str(), before), allowed);
        }
    }
}
