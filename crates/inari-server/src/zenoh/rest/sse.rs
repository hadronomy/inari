use std::{convert::Infallible, time::Duration};

use axum::{
    response::sse::{Event, KeepAlive, Sse},
    response::{IntoResponse, Response},
};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use super::super::{ZenohSubscription, sample_to_json_sample};

pub(crate) fn sse_response(
    subscription: ZenohSubscription,
    keep_alive: Duration,
    buffer: usize,
) -> Response {
    let (tx, rx) = mpsc::channel::<Result<Event, Infallible>>(buffer);

    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = tx.closed() => break,
                sample = subscription.recv_async() => {
                    let sample = match sample {
                        Ok(sample) => sample,
                        Err(error) => {
                            tracing::debug!(error = %error, "closing SSE stream after Zenoh subscription ended");
                            break;
                        }
                    };

                    let event = match Event::default()
                        .event(sample.kind().to_string())
                        .json_data(sample_to_json_sample(&sample))
                    {
                        Ok(event) => event,
                        Err(error) => {
                            tracing::error!(error = %error, "failed to serialize SSE event");
                            break;
                        }
                    };

                    if tx.send(Ok(event)).await.is_err() {
                        break;
                    }
                }
            }
        }
    });

    Sse::new(ReceiverStream::new(rx))
        .keep_alive(KeepAlive::new().interval(keep_alive))
        .into_response()
}
