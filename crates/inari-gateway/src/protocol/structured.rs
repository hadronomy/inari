use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// A deliberately open JSON object at an extensible protocol boundary.
///
/// Stable protocol models should prefer domain structs and enums. This type is
/// reserved for payloads whose contents are intentionally supplied by device
/// drivers or business applications, such as receipt data and redacted device
/// attributes.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct StructuredFields(BTreeMap<String, StructuredValue>);

impl StructuredFields {
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    #[must_use]
    pub fn get(&self, name: &str) -> Option<&StructuredValue> {
        self.0.get(name)
    }
}

impl From<BTreeMap<String, StructuredValue>> for StructuredFields {
    fn from(fields: BTreeMap<String, StructuredValue>) -> Self {
        Self(fields)
    }
}

impl FromIterator<(String, StructuredValue)> for StructuredFields {
    fn from_iter<T: IntoIterator<Item = (String, StructuredValue)>>(fields: T) -> Self {
        Self(fields.into_iter().collect())
    }
}

/// JSON-compatible data used only inside an explicitly extensible field.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum StructuredValue {
    Null,
    Boolean(bool),
    Unsigned(u64),
    Signed(i64),
    Decimal(f64),
    Text(String),
    List(Vec<Self>),
    Object(StructuredFields),
}

#[cfg(test)]
mod tests {
    use super::{StructuredFields, StructuredValue};

    #[test]
    fn structured_fields_preserve_json_shape_without_raw_values() {
        let fields: StructuredFields = [
            ("copies".into(), StructuredValue::Unsigned(2)),
            ("duplex".into(), StructuredValue::Boolean(true)),
        ]
        .into_iter()
        .collect();

        assert_eq!(
            serde_json::to_value(fields).unwrap(),
            serde_json::json!({"copies": 2, "duplex": true}),
        );
    }
}
