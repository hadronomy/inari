use std::borrow::Cow;

use axum::Json;
use axum::body::Bytes;
use axum::http::header::{CONTENT_ENCODING, CONTENT_TYPE};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use html_escape::encode_text_to_string;
use mime::Mime;
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::query::Reply;

use super::metadata::{decode_content_type_parameters, normalize_content_codings};
use crate::zenoh::ZenohJsonSample;

pub(crate) fn json_api_response(samples: Vec<ZenohJsonSample>) -> Response {
    Json(samples).into_response()
}

pub(crate) fn html_api_response<'a>(replies: impl IntoIterator<Item = &'a Reply>) -> Response {
    let mut body = String::from("<dl>\n");

    for reply in replies {
        write_html_reply(reply, &mut body);
    }

    body.push_str("</dl>\n");

    Html(body).into_response()
}

pub(crate) fn raw_zenoh_response(reply: Option<Reply>) -> Response {
    reply
        .map(HttpReply::from)
        .map_or_else(|| StatusCode::OK.into_response(), IntoResponse::into_response)
}

fn write_html_reply(reply: &Reply, output: &mut String) {
    match reply.result() {
        Ok(sample) => {
            write_html_definition(sample.key_expr().as_str(), sample.payload(), output);
        },
        Err(error) => {
            write_html_definition("ERROR", error.payload(), output);
        },
    }
}

fn write_html_definition(label: &str, payload: &ZBytes, output: &mut String) {
    let payload = display_payload(payload);

    output.push_str("<dt>");
    encode_text_to_string(label, output);
    output.push_str("</dt>\n<dd>");
    encode_text_to_string(payload.as_ref(), output);
    output.push_str("</dd>\n");
}

fn display_payload(payload: &ZBytes) -> Cow<'_, str> {
    payload
        .try_to_string()
        .unwrap_or_else(|_| format!("[binary payload: {} bytes]", payload.len()).into())
}

#[derive(Debug, Clone)]
struct HttpReply {
    body: Bytes,
    headers: HttpReplyHeaders,
}

impl From<Reply> for HttpReply {
    fn from(reply: Reply) -> Self {
        match reply.result() {
            Ok(sample) => {
                Self::from_payload(sample.encoding(), sample.attachment(), sample.payload())
            },
            Err(error) => Self::from_payload(error.encoding(), None, error.payload()),
        }
    }
}

impl IntoResponse for HttpReply {
    fn into_response(self) -> Response {
        let mut response = (StatusCode::OK, self.body).into_response();
        self.headers
            .write_to(response.headers_mut());
        response
    }
}

impl HttpReply {
    fn from_payload(encoding: &Encoding, attachment: Option<&ZBytes>, payload: &ZBytes) -> Self {
        Self::from_parts(encoding, attachment, payload.to_bytes().into_owned().into())
    }

    fn from_parts(encoding: &Encoding, attachment: Option<&ZBytes>, body: Bytes) -> Self {
        Self { body, headers: HttpReplyHeaders::from_zenoh_metadata(encoding, attachment) }
    }
}

#[derive(Debug, Clone, Default)]
struct HttpReplyHeaders {
    content_type: Option<HeaderValue>,
    content_encoding: Option<HeaderValue>,
}

impl HttpReplyHeaders {
    fn from_zenoh_metadata(encoding: &Encoding, attachment: Option<&ZBytes>) -> Self {
        let Some(encoding) = ZenohEncoding::parse(encoding) else {
            return Self::default();
        };

        let content_type_parameters =
            decode_content_type_parameters(attachment).unwrap_or_default();

        Self {
            content_type: encoding.content_type_header(&content_type_parameters),
            content_encoding: encoding.content_encoding,
        }
    }

    fn write_to(self, headers: &mut HeaderMap) {
        if let Some(content_type) = self.content_type {
            headers.insert(CONTENT_TYPE, content_type);
        }

        if let Some(content_encoding) = self.content_encoding {
            headers.insert(CONTENT_ENCODING, content_encoding);
        }
    }
}

#[derive(Debug, Clone)]
struct ZenohEncoding {
    media_type: Mime,
    content_encoding: Option<HeaderValue>,
}

impl ZenohEncoding {
    fn parse(encoding: &Encoding) -> Option<Self> {
        let encoding = encoding.to_string();

        let (media_type, schema) = match encoding.split_once(';') {
            Some((media_type, schema)) => (media_type.trim(), Some(schema.trim())),
            None => (encoding.trim(), None),
        };

        Some(Self {
            media_type: parse_media_type(media_type)?,
            content_encoding: schema
                .and_then(|schema| parse_zenoh_content_encoding_schema(&encoding, schema)),
        })
    }

    fn content_type_header(&self, parameters: &[String]) -> Option<HeaderValue> {
        let base = format!("{}/{}", self.media_type.type_(), self.media_type.subtype());
        let value = join_content_type_parameters(&base, parameters);

        value
            .parse::<Mime>()
            .map_err(|error| {
                tracing::debug!(
                    error = %error,
                    value,
                    "ignoring invalid HTTP content type reconstructed from Zenoh sample"
                );
            })
            .ok()
            .and_then(|mime| parse_header_value(mime.as_ref(), "content type"))
    }
}

fn join_content_type_parameters(media_type: &str, parameters: &[String]) -> String {
    if parameters.is_empty() {
        media_type.to_owned()
    } else {
        format!("{media_type}; {}", parameters.join("; "))
    }
}

fn parse_media_type(value: &str) -> Option<Mime> {
    if value.is_empty() {
        return None;
    }

    value
        .parse::<Mime>()
        .map_err(|error| {
            tracing::debug!(
                error = %error,
                value,
                "ignoring invalid Zenoh media type while reconstructing HTTP headers"
            );
        })
        .ok()
}

fn parse_zenoh_content_encoding_schema(full_encoding: &str, schema: &str) -> Option<HeaderValue> {
    let schema = schema.trim();

    if schema.is_empty() {
        return None;
    }

    if schema.contains(';') {
        tracing::debug!(
            full_encoding,
            schema,
            "ignoring unsupported legacy Zenoh schema while reconstructing HTTP headers"
        );
        return None;
    }

    let Some(codings) = normalize_content_codings(schema) else {
        tracing::debug!(
            full_encoding,
            schema,
            "ignoring invalid Zenoh content-encoding schema while reconstructing HTTP headers"
        );
        return None;
    };

    parse_header_value(&codings, "content encoding")
}

fn parse_header_value(value: &str, label: &'static str) -> Option<HeaderValue> {
    HeaderValue::from_str(value).map_or_else(
        |error| {
            tracing::debug!(
                error = %error,
                label,
                value,
                "ignoring invalid Zenoh HTTP header value"
            );
            None
        },
        Some,
    )
}

#[cfg(test)]
mod tests {
    use bytes::Bytes;

    use super::{Encoding, HttpReply, ZBytes};
    use crate::zenoh::rest::metadata::encode_content_type_parameters;

    #[test]
    fn raw_reply_reads_content_encoding_from_zenoh_schema() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "gzip",
        );
    }

    #[test]
    fn raw_reply_supports_multiple_content_encoding_tokens() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;gzip, br"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "gzip, br",
        );
    }

    #[test]
    fn raw_reply_ignores_legacy_mime_parameters_in_encoding() {
        let raw = HttpReply::from_parts(
            &Encoding::from("text/html; charset=utf-8"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html",
        );
        assert!(raw.headers.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_ignores_legacy_multi_segment_encoding() {
        let raw = HttpReply::from_parts(
            &Encoding::from("text/html; charset=utf-8;gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html",
        );
        assert!(raw.headers.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_ignores_legacy_content_encoding_parameter() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;content-encoding=gzip"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert!(raw.headers.content_encoding.is_none());
    }

    #[test]
    fn raw_reply_passes_through_extension_content_encoding() {
        let raw = HttpReply::from_parts(
            &Encoding::from("application/json;x-custom-coding"),
            None,
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "application/json",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "x-custom-coding",
        );
    }

    #[test]
    fn raw_reply_rebuilds_content_type_parameters_from_attachment() {
        let attachment = encode_content_type_parameters(&["charset=utf-8"])
            .expect("attachment should serialize");

        let raw = HttpReply::from_parts(
            &Encoding::from("text/html;br"),
            Some(&ZBytes::from(attachment)),
            Bytes::from_static(b"payload"),
        );

        assert_eq!(
            raw.headers
                .content_type
                .as_ref()
                .expect("content type should exist"),
            "text/html; charset=utf-8",
        );
        assert_eq!(
            raw.headers
                .content_encoding
                .as_ref()
                .expect("content encoding should exist"),
            "br",
        );
    }
}
