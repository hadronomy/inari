use std::net::SocketAddr;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

use chrono::Utc;
use ed25519_dalek::SigningKey;
use inari_router::supervisor::RouterStatus;
use inari_router::{Policy, SignedPolicy};
use rcgen::ExtendedKeyUsagePurpose;

mod support;
use support::{certificate, issuer};

struct Container(String);

impl Container {
    fn start(image: &str, config: &Path) -> Self {
        let output = Command::new("docker")
            .args([
                "run",
                "--detach",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs",
                "/var/lib/inari-router:rw,noexec,nosuid,nodev,size=64m,mode=1777",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                "--publish",
                "127.0.0.1::7447",
                "--publish",
                "127.0.0.1::8443",
                "--mount",
            ])
            .arg(format!("type=bind,source={},target=/etc/inari-router,readonly", config.display()))
            .args([image, "--config", "/etc/inari-router/router.toml"])
            .output()
            .unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        let id = String::from_utf8(output.stdout)
            .unwrap()
            .trim()
            .to_owned();
        assert!(
            id.len() == 64
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
        );
        Self(id)
    }

    fn address(&self, port: &str) -> SocketAddr {
        let output = Command::new("docker")
            .args(["port", &self.0, port])
            .output()
            .unwrap();
        assert!(output.status.success());
        String::from_utf8(output.stdout)
            .unwrap()
            .trim()
            .parse()
            .unwrap()
    }
}

impl Drop for Container {
    fn drop(&mut self) {
        let _ = Command::new("docker")
            .args(["rm", "--force", &self.0])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

fn policy(generation: u64, lifetime: chrono::Duration) -> SignedPolicy {
    let now = Utc::now();
    SignedPolicy::sign(
        Policy {
            version: 1,
            fleet_id: "container-test".into(),
            generation,
            issued_at: now,
            not_before: now,
            expires_at: now + lifetime,
            namespace_prefix: "iot/v1/agents".into(),
            trusted_peer_common_names: vec!["controller-test".into()],
            agents: vec![],
        },
        &SigningKey::from_bytes(&[11; 32]),
    )
    .unwrap()
}

async fn data_session(directory: &Path, port: u16) -> Option<zenoh::Session> {
    let config = serde_json::json!({
        "mode": "client",
        "connect": {"endpoints": [format!("tls/localhost:{port}")], "timeout_ms": 1000},
        "listen": {"endpoints": []},
        "scouting": {"multicast": {"enabled": false}, "gossip": {"enabled": false}},
        "transport": {"link": {"tls": {
            "root_ca_certificate": directory.join("data-ca.pem"),
            "connect_certificate": directory.join("probe.pem"),
            "connect_private_key": directory.join("probe.key"),
            "enable_mtls": true, "verify_name_on_connect": true,
        }}},
    });
    match tokio::time::timeout(
        Duration::from_secs(3),
        zenoh::open(zenoh::Config::from_json5(&config.to_string()).unwrap()),
    )
    .await
    {
        Ok(Ok(session)) => Some(session),
        _ => None,
    }
}

#[tokio::test]
#[ignore = "requires Docker and INARI_TEST_ROUTER_IMAGE with the built Linux image"]
async fn packaged_router_acknowledges_policy_and_stops_on_expiry() {
    let image = std::env::var("INARI_TEST_ROUTER_IMAGE").unwrap();
    let directory = tempfile::tempdir().unwrap();
    let data_root = issuer("data-plane-test-root");
    let management_root = issuer("management-test-root");
    let (management_cert, management_key) =
        certificate(&management_root, "router-management", ExtendedKeyUsagePurpose::ServerAuth);
    let (client_cert, client_key) =
        certificate(&management_root, "controller-policy", ExtendedKeyUsagePurpose::ClientAuth);
    let (router_cert, router_key) =
        certificate(&data_root, "router-test", ExtendedKeyUsagePurpose::ServerAuth);
    let (probe_cert, probe_key) =
        certificate(&data_root, "controller-test", ExtendedKeyUsagePurpose::ClientAuth);
    for (name, contents) in [
        ("management-ca.pem", management_root.pem()),
        ("management.pem", management_cert),
        ("management.key", management_key),
        ("data-ca.pem", data_root.pem()),
        ("router.pem", router_cert),
        ("router.key", router_key),
        ("probe.pem", probe_cert),
        ("probe.key", probe_key),
        (
            "policy-public-key",
            hex::encode(
                SigningKey::from_bytes(&[11; 32])
                    .verifying_key()
                    .as_bytes(),
            ),
        ),
    ] {
        std::fs::write(directory.path().join(name), contents).unwrap();
    }
    std::fs::write(
        directory.path().join("router.toml"),
        r#"
fleet_id = "container-test"
state_directory = "/var/lib/inari-router/state"
signing_public_key_file = "/etc/inari-router/policy-public-key"
zenoh_executable = "/usr/local/bin/zenohd"
[management]
address = "0.0.0.0:8443"
certificate_file = "/etc/inari-router/management.pem"
private_key_file = "/etc/inari-router/management.key"
client_ca_file = "/etc/inari-router/management-ca.pem"
controller_common_name = "controller-policy"
[router]
id = "1234567890abcdef1234567890abcdef"
listen_endpoint = "tls/[::]:7447"
probe_endpoint = "tls/localhost:7447"
peer_endpoints = []
root_ca_file = "/etc/inari-router/data-ca.pem"
certificate_file = "/etc/inari-router/router.pem"
private_key_file = "/etc/inari-router/router.key"
probe_certificate_file = "/etc/inari-router/probe.pem"
probe_private_key_file = "/etc/inari-router/probe.key"
"#,
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        // The nonroot container reads only these generated test credentials.
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        for entry in std::fs::read_dir(directory.path()).unwrap() {
            std::fs::set_permissions(entry.unwrap().path(), std::fs::Permissions::from_mode(0o644))
                .unwrap();
        }
    }
    let container = Container::start(&image, directory.path());
    let management = container.address("8443/tcp");
    let data = container.address("7447/tcp");
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(15))
        .tls_certs_only([reqwest::Certificate::from_pem(management_root.pem().as_bytes()).unwrap()])
        .identity(
            reqwest::Identity::from_pem(format!("{client_cert}{client_key}").as_bytes()).unwrap(),
        )
        .build()
        .unwrap();
    let origin = format!("https://localhost:{}", management.port());
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            if client
                .get(format!("{origin}/status"))
                .send()
                .await
                .is_ok()
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("the packaged management server must start");
    assert!(
        data_session(directory.path(), data.port())
            .await
            .is_none(),
        "no signed policy means no data plane"
    );
    let initial = policy(1, chrono::Duration::seconds(30));
    let expected = initial
        .verify(&SigningKey::from_bytes(&[11; 32]).verifying_key(), "container-test")
        .unwrap();
    let status = client
        .put(format!("{origin}/policy"))
        .json(&initial)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<RouterStatus>()
        .await
        .unwrap();
    assert!(status.is_current());
    assert_eq!(status.generation, Some(1));
    assert_eq!(status.digest.as_deref(), Some(expected.digest()));
    assert_eq!(status.expires_at, Some(initial.policy.expires_at));
    data_session(directory.path(), data.port())
        .await
        .expect("the packaged stock Router must accept mTLS")
        .close()
        .await
        .unwrap();
    let retry = client
        .put(format!("{origin}/policy"))
        .json(&initial)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<RouterStatus>()
        .await
        .unwrap();
    assert!(retry.is_current());
    assert_eq!(retry.digest, status.digest);
    let expiring = policy(2, chrono::Duration::seconds(2));
    assert!(
        client
            .put(format!("{origin}/policy"))
            .json(&expiring)
            .send()
            .await
            .unwrap()
            .error_for_status()
            .unwrap()
            .json::<RouterStatus>()
            .await
            .unwrap()
            .ready
    );
    tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            let status = client
                .get(format!("{origin}/status"))
                .send()
                .await
                .unwrap()
                .error_for_status()
                .unwrap()
                .json::<RouterStatus>()
                .await
                .unwrap();
            if !status.ready
                && data_session(directory.path(), data.port())
                    .await
                    .is_none()
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("expired policy must close the packaged data plane");
    assert_eq!(
        client
            .put(format!("{origin}/policy"))
            .json(&initial)
            .send()
            .await
            .unwrap()
            .status(),
        reqwest::StatusCode::CONFLICT
    );
    assert!(
        client
            .put(format!("{origin}/policy"))
            .json(&policy(3, chrono::Duration::seconds(30)))
            .send()
            .await
            .unwrap()
            .error_for_status()
            .unwrap()
            .json::<RouterStatus>()
            .await
            .unwrap()
            .is_current()
    );
}
