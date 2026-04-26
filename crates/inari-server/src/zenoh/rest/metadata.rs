use bytes::Bytes;
use mime::Mime;
use serde::{Deserialize, Serialize};
use zenoh::bytes::ZBytes;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
struct HttpMetadataAttachment {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    content_type_parameters: Vec<String>,
}

pub(super) fn split_content_type(value: &str) -> Option<(String, Vec<String>)> {
    let parsed = value.parse::<Mime>().ok()?;
    let media_type = format!("{}/{}", parsed.type_(), parsed.subtype());
    let parameters = parsed
        .params()
        .map(|(name, value)| format!("{name}={value}"))
        .collect();

    Some((media_type, parameters))
}

pub(super) fn normalize_content_codings(value: &str) -> Option<String> {
    let codings = value
        .split(',')
        .map(str::trim)
        .collect::<Vec<_>>();

    if codings
        .iter()
        .any(|coding| coding.is_empty() || !is_http_token(coding))
    {
        return None;
    }

    Some(codings.join(", "))
}

pub(super) fn encode_content_type_parameters<S: AsRef<str>>(parameters: &[S]) -> Option<Bytes> {
    if parameters.is_empty() {
        return None;
    }

    serde_json::to_vec(&HttpMetadataAttachment {
        content_type_parameters: parameters
            .iter()
            .map(|value| value.as_ref().to_owned())
            .collect(),
    })
    .map(Bytes::from)
    .map_err(|error| {
        tracing::warn!(
            error = %error,
            "failed to serialize HTTP metadata attachment for Zenoh transport"
        );
    })
    .ok()
}

pub(super) fn decode_content_type_parameters(attachment: Option<&ZBytes>) -> Option<Vec<String>> {
    let attachment = attachment?;

    serde_json::from_slice::<HttpMetadataAttachment>(attachment.to_bytes().as_ref())
        .map(|metadata| metadata.content_type_parameters)
        .map_err(|error| {
            tracing::debug!(
                error = %error,
                "ignoring invalid HTTP metadata attachment on Zenoh sample"
            );
        })
        .ok()
        .filter(|parameters| !parameters.is_empty())
}

fn is_http_token(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric()
                || matches!(
                    byte,
                    b'!' | b'#'
                        | b'$'
                        | b'%'
                        | b'&'
                        | b'\''
                        | b'*'
                        | b'+'
                        | b'-'
                        | b'.'
                        | b'^'
                        | b'_'
                        | b'`'
                        | b'|'
                        | b'~'
                )
        })
}

#[cfg(test)]
mod tests {
    use zenoh::bytes::ZBytes;

    use super::{
        decode_content_type_parameters, encode_content_type_parameters, normalize_content_codings,
        split_content_type,
    };

    #[test]
    fn split_content_type_separates_media_type_from_parameters() {
        let (media_type, parameters) = split_content_type("text/html; charset=utf-8; boundary=abc")
            .expect("content type should split");

        assert_eq!(media_type, "text/html");
        assert_eq!(parameters, vec!["charset=utf-8", "boundary=abc"]);
    }

    #[test]
    fn content_type_parameters_round_trip_through_attachment_json() {
        let attachment = encode_content_type_parameters(&["charset=utf-8", "boundary=abc"])
            .expect("attachment should serialize");
        let decoded = decode_content_type_parameters(Some(&ZBytes::from(attachment)))
            .expect("attachment should decode");

        assert_eq!(decoded, vec!["charset=utf-8", "boundary=abc"]);
    }

    #[test]
    fn normalize_content_codings_preserves_valid_tokens() {
        assert_eq!(normalize_content_codings("gzip, br"), Some(String::from("gzip, br")));
    }

    #[test]
    fn normalize_content_codings_rejects_parameter_syntax() {
        assert_eq!(normalize_content_codings("charset=utf-8"), None);
    }
}
