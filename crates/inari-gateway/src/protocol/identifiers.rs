use std::fmt;
use std::str::FromStr;

use serde::{Deserialize, Serialize};

macro_rules! resource_id {
    ($name:ident, $prefix:literal, $label:literal) => {
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        #[serde(try_from = "String", into = "String")]
        pub struct $name(String);

        impl $name {
            #[must_use]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }

        impl FromStr for $name {
            type Err = crate::GatewayError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                let suffix = value.strip_prefix($prefix).filter(|suffix| !suffix.is_empty());
                let valid = value.len() <= 96
                    && suffix.is_some_and(|suffix| {
                        suffix.bytes().all(|byte| {
                            byte.is_ascii_lowercase()
                                || byte.is_ascii_digit()
                                || matches!(byte, b'_' | b'-')
                        })
                    });
                if valid {
                    Ok(Self(value.to_owned()))
                } else {
                    Err(crate::GatewayError::InvalidInput(format!(
                        "{} must start with `{}`, use lowercase ASCII letters, digits, hyphens, or underscores, and be at most 96 characters",
                        $label, $prefix
                    )))
                }
            }
        }

        impl TryFrom<String> for $name {
            type Error = crate::GatewayError;

            fn try_from(value: String) -> Result<Self, Self::Error> {
                value.parse()
            }
        }

        impl From<$name> for String {
            fn from(value: $name) -> Self {
                value.0
            }
        }
    };
}

resource_id!(OrganizationId, "org_", "organization IDs");
resource_id!(SiteId, "site_", "site IDs");
resource_id!(DeviceId, "dev_", "device IDs");
resource_id!(JobId, "job_", "job IDs");
resource_id!(PolicyId, "policy_", "policy IDs");

#[cfg(test)]
mod tests {
    use super::{DeviceId, JobId, OrganizationId, PolicyId, SiteId};

    #[test]
    fn resource_ids_enforce_domain_prefixes() {
        assert!(
            "org_example"
                .parse::<OrganizationId>()
                .is_ok()
        );
        assert!(
            "site_headquarters"
                .parse::<SiteId>()
                .is_ok()
        );
        assert!(
            "dev_scale-01"
                .parse::<DeviceId>()
                .is_ok()
        );
        assert!("job_01j3test".parse::<JobId>().is_ok());
        assert!(
            "policy_front_desk"
                .parse::<PolicyId>()
                .is_ok()
        );
        assert!(
            "site_Headquarters"
                .parse::<SiteId>()
                .is_err()
        );
    }
}
