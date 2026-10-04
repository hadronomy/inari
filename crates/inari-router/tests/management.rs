use std::net::TcpListener;
use std::time::Duration;

use ed25519_dalek::SigningKey;
use inari_router::management::ManagementAcceptor;
use inari_router::supervisor::RouterSupervisor;
use inari_router::{PolicyStore, RouterConfig};
use rcgen::ExtendedKeyUsagePurpose;

mod support;
use support::{certificate, issuer};

fn client(root: &str, identity: Option<(String, String)>) -> reqwest::Client {
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .tls_certs_only([reqwest::Certificate::from_pem(root.as_bytes()).unwrap()]);
    if let Some((certificate, key)) = identity {
        builder = builder.identity(
            reqwest::Identity::from_pem(format!("{certificate}{key}").as_bytes()).unwrap(),
        );
    }
    builder.build().unwrap()
}

#[tokio::test]
async fn management_requires_the_dedicated_controller_certificate() {
    let directory = tempfile::tempdir().unwrap();
    let root = issuer("management-root");
    let (server_certificate, server_key) =
        certificate(&root, "router-management", ExtendedKeyUsagePurpose::ServerAuth);
    let ca_path = directory.path().join("ca.pem");
    let certificate_path = directory.path().join("server.pem");
    let key_path = directory.path().join("server.key");
    std::fs::write(&ca_path, root.pem()).unwrap();
    std::fs::write(&certificate_path, server_certificate).unwrap();
    std::fs::write(&key_path, server_key).unwrap();
    let acceptor =
        ManagementAcceptor::from_files(&certificate_path, &key_path, &ca_path, "controller-policy")
            .await
            .unwrap();
    let (handle, _supervisor) = RouterSupervisor::new(
        PolicyStore::open(&directory.path().join("state")).unwrap(),
        SigningKey::from_bytes(&[11; 32]).verifying_key(),
        "fleet-test".into(),
        "absent-zenohd".into(),
        RouterConfig {
            id: "1234567890abcdef1234567890abcdef".into(),
            listen_endpoint: "tls/[::]:7447".into(),
            probe_endpoint: "tls/localhost:7447".into(),
            peer_endpoints: vec![],
            root_ca_file: ca_path.clone(),
            certificate_file: certificate_path.clone(),
            private_key_file: key_path,
            probe_certificate_file: certificate_path.clone(),
            probe_private_key_file: ca_path.clone(),
        },
    )
    .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let endpoint = format!("https://localhost:{}/status", listener.local_addr().unwrap().port());
    let shutdown = axum_server::Handle::new();
    let server = axum_server::from_tcp(listener)
        .unwrap()
        .acceptor(acceptor)
        .handle(shutdown.clone());
    let task =
        tokio::spawn(server.serve(inari_router::management::routes(handle).into_make_service()));

    let valid = client(
        &root.pem(),
        Some(certificate(&root, "controller-policy", ExtendedKeyUsagePurpose::ClientAuth)),
    );
    let response = valid
        .get(&endpoint)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    assert_eq!(
        response
            .json::<serde_json::Value>()
            .await
            .unwrap()["ready"],
        false
    );
    assert!(
        client(&root.pem(), None)
            .get(&endpoint)
            .send()
            .await
            .is_err()
    );
    let agent = client(
        &root.pem(),
        Some(certificate(
            &root,
            "agt_aaaaaaaaaaaaaaaaaaaaaaaa",
            ExtendedKeyUsagePurpose::ClientAuth,
        )),
    );
    assert!(
        agent
            .get(&endpoint)
            .send()
            .await
            .is_err()
    );
    let wrong_subject = client(
        &root.pem(),
        Some(certificate(&root, "another-controller", ExtendedKeyUsagePurpose::ClientAuth)),
    );
    assert!(
        wrong_subject
            .get(&endpoint)
            .send()
            .await
            .is_err()
    );
    let wrong_ca = issuer("data-plane-root");
    let untrusted = client(
        &root.pem(),
        Some(certificate(&wrong_ca, "controller-policy", ExtendedKeyUsagePurpose::ClientAuth)),
    );
    assert!(
        untrusted
            .get(&endpoint)
            .send()
            .await
            .is_err()
    );
    let wrong_usage = client(
        &root.pem(),
        Some(certificate(&root, "controller-policy", ExtendedKeyUsagePurpose::ServerAuth)),
    );
    assert!(
        wrong_usage
            .get(&endpoint)
            .send()
            .await
            .is_err()
    );
    let renewed_root = issuer("renewed-management-root");
    let (renewed_certificate, renewed_key) =
        certificate(&renewed_root, "router-management", ExtendedKeyUsagePurpose::ServerAuth);
    std::fs::write(&ca_path, renewed_root.pem()).unwrap();
    std::fs::write(&certificate_path, renewed_certificate).unwrap();
    std::fs::write(directory.path().join("server.key"), &renewed_key).unwrap();
    let renewed = client(
        &renewed_root.pem(),
        Some(certificate(&renewed_root, "controller-policy", ExtendedKeyUsagePurpose::ClientAuth)),
    );
    assert_eq!(
        renewed
            .get(&endpoint)
            .send()
            .await
            .expect("management must load the new CA and certificate")
            .status(),
        reqwest::StatusCode::OK
    );
    std::fs::write(directory.path().join("server.key"), "invalid replacement").unwrap();
    let fresh = client(
        &renewed_root.pem(),
        Some(certificate(&renewed_root, "controller-policy", ExtendedKeyUsagePurpose::ClientAuth)),
    );
    assert!(
        fresh
            .get(&endpoint)
            .send()
            .await
            .is_err(),
        "invalid replacement must reject new handshakes"
    );
    std::fs::write(directory.path().join("server.key"), renewed_key).unwrap();
    assert_eq!(
        fresh
            .get(&endpoint)
            .send()
            .await
            .unwrap()
            .status(),
        reqwest::StatusCode::OK
    );
    shutdown.shutdown();
    task.await.unwrap().unwrap();
}
