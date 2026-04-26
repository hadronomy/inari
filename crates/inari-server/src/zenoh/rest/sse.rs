use std::convert::Infallible;
use std::time::Duration;

use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use futures_util::stream::unfold;

use super::super::{ZenohSubscription, sample_to_json_sample};

pub(crate) fn sse_response(subscription: ZenohSubscription, keep_alive: Duration) -> Response {
    let stream = unfold(subscription, |subscription| async move {
        let sample = match subscription.recv_async().await {
            Ok(sample) => sample,
            Err(error) => {
                tracing::debug!(
                    error = %error,
                    "closing SSE stream after Zenoh subscription ended"
                );
                return None;
            },
        };

        let event = match Event::default()
            .event(sample.kind().to_string())
            .json_data(sample_to_json_sample(&sample))
        {
            Ok(event) => event,
            Err(error) => {
                tracing::error!(error = %error, "failed to serialize SSE event");
                return None;
            },
        };

        Some((Ok::<_, Infallible>(event), subscription))
    });

    Sse::new(stream)
        .keep_alive(KeepAlive::new().interval(keep_alive))
        .into_response()
}
