use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use serde::Serialize;
use serde_json::Value;
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::query::Reply;
use zenoh::sample::Sample;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub(crate) struct ZenohJsonSample {
    pub key: String,
    pub value: Value,
    pub encoding: String,
    pub timestamp: Option<String>,
}

pub(crate) fn reply_to_json_sample(reply: &Reply) -> ZenohJsonSample {
    match reply.result() {
        Ok(sample) => sample_to_json_sample(sample),
        Err(error) => ZenohJsonSample {
            key: "ERROR".into(),
            value: payload_to_json(error.payload(), error.encoding()),
            encoding: error.encoding().to_string(),
            timestamp: None,
        },
    }
}

pub(crate) fn sample_to_json_sample(sample: &Sample) -> ZenohJsonSample {
    ZenohJsonSample {
        key: sample.key_expr().as_str().into(),
        value: payload_to_json(sample.payload(), sample.encoding()),
        encoding: sample.encoding().to_string(),
        timestamp: sample
            .timestamp()
            .map(ToString::to_string),
    }
}

pub(crate) fn payload_to_json(payload: &ZBytes, encoding: &Encoding) -> Value {
    if payload.is_empty() {
        return Value::Null;
    }

    match encoding {
        &Encoding::APPLICATION_JSON | &Encoding::TEXT_JSON | &Encoding::TEXT_JSON5 => {
            let bytes = payload.to_bytes();
            serde_json::from_slice(&bytes)
                .unwrap_or_else(|_| Value::String(STANDARD.encode(bytes.as_ref())))
        },
        &Encoding::TEXT_PLAIN | &Encoding::ZENOH_STRING => {
            let bytes = payload.to_bytes();
            match std::str::from_utf8(bytes.as_ref()) {
                Ok(text) => Value::String(text.into()),
                Err(_) => Value::String(STANDARD.encode(bytes.as_ref())),
            }
        },
        _ => {
            let bytes = payload.to_bytes();
            Value::String(STANDARD.encode(bytes.as_ref()))
        },
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{Encoding, ZBytes, payload_to_json};

    #[test]
    fn payload_to_json_decodes_json_payloads() {
        let payload = ZBytes::from(r#"{"ok":true}"#);
        let value = payload_to_json(&payload, &Encoding::APPLICATION_JSON);

        assert_eq!(value, json!({"ok": true}));
    }

    #[test]
    fn payload_to_json_base64_encodes_binary_payloads() {
        let payload = ZBytes::from(vec![0_u8, 159, 146, 150]);
        let value = payload_to_json(&payload, &Encoding::APPLICATION_OCTET_STREAM);

        assert_eq!(value, json!("AJ+Slg=="));
    }
}
