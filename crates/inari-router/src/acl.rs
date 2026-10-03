use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::{RouterError, RouterResult, VerifiedPolicy};

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RouterConfig {
    pub id: String,
    pub listen_endpoint: String,
    pub probe_endpoint: String,
    pub peer_endpoints: Vec<String>,
    pub root_ca_file: PathBuf,
    pub certificate_file: PathBuf,
    pub private_key_file: PathBuf,
    pub probe_certificate_file: PathBuf,
    pub probe_private_key_file: PathBuf,
}

impl RouterConfig {
    pub fn validate(&self) -> RouterResult<()> {
        if self.id.len() != 32
            || self.id.bytes().all(|byte| byte == b'0')
            || !self
                .id
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(RouterError::InvalidPolicy(
                "Router id must contain 32 hexadecimal digits".into(),
            ));
        }
        for endpoint in [&self.listen_endpoint, &self.probe_endpoint]
            .into_iter()
            .chain(self.peer_endpoints.iter())
        {
            if !endpoint.starts_with("tls/")
                || endpoint.len() > 512
                || endpoint.contains(['#', '?', '\n', '\r'])
            {
                return Err(RouterError::InvalidPolicy(
                    "Router endpoints must use TLS without endpoint overrides".into(),
                ));
            }
        }
        Ok(())
    }

    pub(crate) fn render(&self, policy: &VerifiedPolicy) -> RouterResult<Value> {
        self.validate()?;
        let value = json!({
            "id": self.id,
            "mode": "router",
            "listen": { "endpoints": [self.listen_endpoint] },
            "connect": { "endpoints": self.peer_endpoints, "exit_on_failure": false },
            "scouting": { "multicast": { "enabled": false }, "gossip": { "enabled": false } },
            "adminspace": { "enabled": false },
            "plugins_loading": { "enabled": false },
            "transport": { "link": { "tls": {
                "root_ca_certificate": self.root_ca_file,
                "listen_certificate": self.certificate_file,
                "listen_private_key": self.private_key_file,
                "connect_certificate": self.certificate_file,
                "connect_private_key": self.private_key_file,
                "enable_mtls": true,
                "verify_name_on_connect": true,
                "close_link_on_expiration": true
            } } },
            "access_control": access_control(policy)
        });
        // Stock Zenoh owns configuration semantics, including key-expression parsing.
        zenoh::Config::from_json5(&serde_json::to_string(&value)?)
            .map_err(|error| RouterError::InvalidPolicy(error.to_string()))?;
        Ok(value)
    }

    pub(crate) fn probe_config(&self) -> RouterResult<zenoh::Config> {
        let value = json!({
            "mode": "client",
            "connect": { "endpoints": [self.probe_endpoint], "timeout_ms": 2000 },
            "listen": { "endpoints": [] },
            "scouting": { "multicast": { "enabled": false }, "gossip": { "enabled": false } },
            "transport": { "link": { "tls": {
                "root_ca_certificate": self.root_ca_file,
                "connect_certificate": self.probe_certificate_file,
                "connect_private_key": self.probe_private_key_file,
                "enable_mtls": true,
                "verify_name_on_connect": true
            } } }
        });
        zenoh::Config::from_json5(&serde_json::to_string(&value)?)
            .map_err(|error| RouterError::InvalidPolicy(error.to_string()))
    }
}

#[derive(Serialize)]
struct Rule {
    id: String,
    messages: Vec<&'static str>,
    flows: Vec<&'static str>,
    permission: &'static str,
    key_exprs: Vec<String>,
}

fn access_control(verified: &VerifiedPolicy) -> Value {
    let policy = verified.policy();
    let mut rules = vec![rule(
        "trusted-authority".into(),
        vec![
            "put",
            "delete",
            "declare_subscriber",
            "query",
            "reply",
            "declare_queryable",
            "liveliness_token",
            "liveliness_query",
            "declare_liveliness_subscriber",
        ],
        vec!["ingress", "egress"],
        vec![format!("{}/**", policy.namespace_prefix)],
    )];
    let mut subjects = vec![
        json!({"id":"trusted-peers", "link_protocols":["tls"], "cert_common_names":policy.trusted_peer_common_names}),
    ];
    let mut policies = vec![
        json!({"id":"trusted-authority", "rules":["trusted-authority"], "subjects":["trusted-peers"]}),
    ];
    for agent in &policy.agents {
        let name = &agent.common_name;
        let namespace = &agent.namespace;
        let definitions = [
            (
                "publications",
                vec!["put"],
                vec!["ingress"],
                vec!["presence/agent", "status/latest", "results/*", "events/*", "errors/*"],
            ),
            ("presence", vec!["liveliness_token"], vec!["ingress"], vec!["presence/agent"]),
            ("subscription", vec!["declare_subscriber"], vec!["ingress"], vec!["commands/live/**"]),
            ("delivery", vec!["put"], vec!["egress"], vec!["commands/live/**"]),
            ("query", vec!["query"], vec!["ingress"], vec!["commands/history", "state/commit"]),
            (
                "reply",
                vec!["reply", "declare_queryable"],
                vec!["egress"],
                vec!["commands/history", "state/commit"],
            ),
        ];
        let mut agent_rules = Vec::new();
        for (suffix, messages, flows, paths) in definitions {
            let id = format!("{name}-{suffix}");
            agent_rules.push(id.clone());
            rules.push(rule(
                id,
                messages,
                flows,
                paths
                    .into_iter()
                    .map(|path| format!("{namespace}/{path}"))
                    .collect(),
            ));
        }
        subjects.push(json!({"id":name,"link_protocols":["tls"],"cert_common_names":[name]}));
        policies.push(json!({"id":name,"subjects":[name],"rules":agent_rules}));
    }
    json!({"enabled":true,"default_permission":"deny","rules":rules,"subjects":subjects,"policies":policies})
}

fn rule(
    id: String,
    messages: Vec<&'static str>,
    flows: Vec<&'static str>,
    key_exprs: Vec<String>,
) -> Rule {
    Rule { id, messages, flows, permission: "allow", key_exprs }
}
