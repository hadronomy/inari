use std::pin::Pin;
use std::task::{Context, Poll};

use axum::body::{Body, Bytes, HttpBody};
use axum::extract::Request;
use axum::http::header::CONTENT_TYPE;
use axum::middleware::Next;
use axum::response::Response;
use http_body::{Frame, SizeHint};
use leptos::reactive::diagnostics::{SpecialNonReactiveFuture, SpecialNonReactiveZone};

pub(super) async fn serve(request: Request, next: Next) -> Response {
    let response = SpecialNonReactiveFuture::new(next.run(request)).await;
    wrap_html(response)
}

fn wrap_html(response: Response<Body>) -> Response<Body> {
    let is_html = response
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.starts_with("text/html"));

    if !is_html {
        return response;
    }

    let (parts, body) = response.into_parts();
    Response::from_parts(parts, Body::new(SsrBody(body)))
}

struct SsrBody(Body);

impl HttpBody for SsrBody {
    type Data = Bytes;
    type Error = axum::Error;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>> {
        let _non_reactive = SpecialNonReactiveZone::enter();
        Pin::new(&mut self.0).poll_frame(context)
    }

    fn is_end_stream(&self) -> bool {
        self.0.is_end_stream()
    }

    fn size_hint(&self) -> SizeHint {
        self.0.size_hint()
    }
}

#[cfg(test)]
mod tests {
    use axum::body::to_bytes;
    use axum::http::header::CONTENT_TYPE;

    use super::*;

    #[tokio::test]
    async fn wrapping_html_preserves_the_response() {
        let response = Response::builder()
            .status(201)
            .header(CONTENT_TYPE, "text/html; charset=utf-8")
            .body(Body::from("<main>Inari</main>"))
            .expect("test response should be valid");

        let response = wrap_html(response);
        assert_eq!(response.status(), 201);
        assert_eq!(
            response
                .headers()
                .get(CONTENT_TYPE)
                .expect("content type should be retained"),
            "text/html; charset=utf-8"
        );
        assert_eq!(
            to_bytes(response.into_body(), usize::MAX)
                .await
                .expect("response body should be readable"),
            "<main>Inari</main>"
        );
    }
}
