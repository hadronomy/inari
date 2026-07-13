use leptos::prelude::*;

#[derive(Clone, Copy, PartialEq, Eq)]
enum CopyOutcome {
    Copied,
    Failed,
}

impl From<bool> for CopyOutcome {
    fn from(copied: bool) -> Self {
        if copied { Self::Copied } else { Self::Failed }
    }
}

#[component]
pub(super) fn CopyButton(value: String, label: &'static str) -> impl IntoView {
    let outcome = RwSignal::new(None);

    view! {
        <button
            class="button button-quiet"
            type="button"
            on:click=move |_| copy_to_clipboard(value.clone(), outcome)
        >
            <span aria-live="polite">{move || match outcome.get() {
                None => label,
                Some(CopyOutcome::Copied) => "Copied",
                Some(CopyOutcome::Failed) => "Try again",
            }}</span>
        </button>
    }
}

fn copy_to_clipboard(value: String, outcome: RwSignal<Option<CopyOutcome>>) {
    #[cfg(feature = "hydrate")]
    {
        use wasm_bindgen_futures::JsFuture;

        let Some(window) = web_sys::window() else {
            outcome.set(Some(CopyOutcome::Failed));
            return;
        };
        let promise = window
            .navigator()
            .clipboard()
            .write_text(&value);
        leptos::task::spawn_local(async move {
            outcome.set(Some(
                JsFuture::from(promise)
                    .await
                    .is_ok()
                    .into(),
            ));
        });
    }

    #[cfg(not(feature = "hydrate"))]
    let _ = (value, outcome);
}
