use std::io::{self, BufReader};
use std::net::TcpListener as StandardListener;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use chrono::Utc;
use ed25519_dalek::SigningKey;
use inari_router::supervisor::{RouterSupervisor, SupervisorHandle};
use inari_router::{AgentAdmission, Policy, PolicyStore, RouterConfig, RouterError, SignedPolicy};
use rcgen::{CertificateParams, DnType, ExtendedKeyUsagePurpose, KeyPair, KeyUsagePurpose};
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, RootCertStore};
use serde_json::json;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;
use tokio_rustls::TlsConnector;

mod support;
use support::{certificate, issuer};

const AGENT_A: &str = "agt_aaaaaaaaaaaaaaaaaaaaaaaa";
const AGENT_B: &str = "agt_bbbbbbbbbbbbbbbbbbbbbbbb";

fn policy(generation: u64, agents: &[&str]) -> Policy {
    let now = Utc::now();
    Policy {
        version: 1,
        fleet_id: "fleet-test".into(),
        generation,
        issued_at: now,
        not_before: now,
        expires_at: now + chrono::Duration::minutes(3),
        namespace_prefix: "iot/v1/agents".into(),
        trusted_peer_common_names: vec!["controller-test".into()],
        agents: agents
            .iter()
            .map(|name| AgentAdmission {
                common_name: (*name).into(),
                namespace: format!("iot/v1/agents/{name}"),
            })
            .collect(),
    }
}

async fn connect(config: &RouterConfig, name: &str, port: u16) -> zenoh::Session {
    let directory = config
        .certificate_file
        .parent()
        .unwrap();
    let config = json!({
        "mode":"client", "connect":{"endpoints":[format!("tls/127.0.0.1:{port}")],"timeout_ms":2000},
        "listen":{"endpoints":[]},"scouting":{"multicast":{"enabled":false},"gossip":{"enabled":false}},
        "transport":{"link":{"tls":{
            "root_ca_certificate":config.root_ca_file,
            "connect_certificate":directory.join(format!("{name}.pem")),
            "connect_private_key":directory.join(format!("{name}.key")),
            "enable_mtls":true,"verify_name_on_connect":true
        }}}
    });
    let config = zenoh::Config::from_json5(&config.to_string()).unwrap();
    tokio::time::timeout(Duration::from_secs(3), zenoh::open(config))
        .await
        .unwrap()
        .unwrap()
}

async fn current(handle: &SupervisorHandle, key: &SigningKey, generation: u64, agents: &[&str]) {
    let signed = SignedPolicy::sign(policy(generation, agents), key).unwrap();
    let digest = signed
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap()
        .digest()
        .to_owned();
    let status = tokio::time::timeout(Duration::from_secs(20), handle.apply(signed))
        .await
        .unwrap()
        .unwrap();
    assert!(status.ready);
    assert_eq!(status.generation, Some(generation));
    assert_eq!(status.digest.as_deref(), Some(digest.as_str()));
}

async fn server_certificate(config: &RouterConfig, port: u16) -> io::Result<Vec<u8>> {
    let mut roots = RootCertStore::empty();
    for root in rustls_pemfile::certs(&mut BufReader::new(
        std::fs::File::open(&config.root_ca_file).unwrap(),
    )) {
        roots.add(root.unwrap()).unwrap();
    }
    let chain = rustls_pemfile::certs(&mut BufReader::new(
        std::fs::File::open(&config.probe_certificate_file).unwrap(),
    ))
    .collect::<Result<Vec<_>, _>>()
    .unwrap();
    let key = rustls_pemfile::private_key(&mut BufReader::new(
        std::fs::File::open(&config.probe_private_key_file).unwrap(),
    ))
    .unwrap()
    .unwrap();
    let client =
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_root_certificates(roots)
            .with_client_auth_cert(chain, key)
            .unwrap();
    let connector = TlsConnector::from(Arc::new(client));
    let stream = TcpStream::connect(("127.0.0.1", port)).await?;
    let stream = connector
        .connect(ServerName::try_from("localhost").unwrap(), stream)
        .await?;
    Ok(stream
        .get_ref()
        .1
        .peer_certificates()
        .unwrap()[0]
        .to_vec())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires INARI_TEST_ZENOHD pointing to the stock Zenoh 1.9 executable"]
async fn stock_router_reloads_tls_material_and_stops_at_certificate_expiry() {
    let executable = std::env::var_os("INARI_TEST_ZENOHD").unwrap();
    let directory = tempfile::tempdir().unwrap();
    let root = issuer("rotation-test-root");
    let root_file = directory.path().join("root.pem");
    std::fs::write(&root_file, root.pem()).unwrap();
    for (name, usage) in [
        ("router-test", ExtendedKeyUsagePurpose::ServerAuth),
        ("controller-test", ExtendedKeyUsagePurpose::ClientAuth),
    ] {
        let (certificate, key) = certificate(&root, name, usage);
        std::fs::write(
            directory
                .path()
                .join(format!("{name}.pem")),
            certificate,
        )
        .unwrap();
        std::fs::write(
            directory
                .path()
                .join(format!("{name}.key")),
            key,
        )
        .unwrap();
    }
    let listener = StandardListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let config = RouterConfig {
        id: "1234567890abcdef1234567890abcdef".into(),
        listen_endpoint: format!("tls/127.0.0.1:{port}"),
        probe_endpoint: format!("tls/127.0.0.1:{port}"),
        peer_endpoints: vec![],
        root_ca_file: root_file,
        certificate_file: directory.path().join("router-test.pem"),
        private_key_file: directory.path().join("router-test.key"),
        probe_certificate_file: directory
            .path()
            .join("controller-test.pem"),
        probe_private_key_file: directory
            .path()
            .join("controller-test.key"),
    };
    let key = SigningKey::from_bytes(&[11; 32]);
    let (handle, supervisor) = RouterSupervisor::new(
        PolicyStore::open(&directory.path().join("state")).unwrap(),
        key.verifying_key(),
        "fleet-test".into(),
        executable.into(),
        config.clone(),
    )
    .unwrap();
    let (shutdown, receiver) = watch::channel(false);
    let task = tokio::spawn(supervisor.run(receiver));
    current(&handle, &key, 1, &[]).await;
    let old = server_certificate(&config, port)
        .await
        .unwrap();
    let (renewed, renewed_key) =
        certificate(&root, "router-test", ExtendedKeyUsagePurpose::ServerAuth);
    let expected = rustls_pemfile::certs(&mut BufReader::new(renewed.as_bytes()))
        .next()
        .unwrap()
        .unwrap()
        .to_vec();
    std::fs::write(&config.certificate_file, &renewed).unwrap();
    std::fs::write(&config.private_key_file, &renewed_key).unwrap();
    tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            if handle.status().ready
                && TcpStream::connect(("127.0.0.1", port))
                    .await
                    .is_ok()
                && server_certificate(&config, port)
                    .await
                    .is_ok_and(|actual| actual == expected)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("certificate rotation must restart the running Router without a policy update");
    assert_ne!(old, expected);
    assert_eq!(handle.status().generation, Some(1));
    std::fs::write(&config.private_key_file, "invalid replacement").unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        while handle.status().ready
            || TcpStream::connect(("127.0.0.1", port))
                .await
                .is_ok()
        {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("invalid TLS replacement must close the data plane");
    std::fs::write(&config.private_key_file, renewed_key).unwrap();
    current(&handle, &key, 2, &[]).await;
    let short_key = KeyPair::generate().unwrap();
    let mut short = CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()]).unwrap();
    short
        .distinguished_name
        .push(DnType::CommonName, "router-test");
    short.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    short.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    short.not_after = time::OffsetDateTime::now_utc() + time::Duration::seconds(4);
    let expires_at = short.not_after;
    let short_certificate = short
        .signed_by(&short_key, &root)
        .unwrap();
    let expected = short_certificate.der().to_vec();
    std::fs::write(&config.certificate_file, short_certificate.pem()).unwrap();
    std::fs::write(&config.private_key_file, short_key.serialize_pem()).unwrap();
    current(&handle, &key, 3, &[]).await;
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if handle.status().ready
                && server_certificate(&config, port)
                    .await
                    .is_ok_and(|actual| actual == expected)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("the short-lived certificate must serve a real TLS handshake before expiry");
    tokio::time::timeout(Duration::from_secs(6), async {
        while handle.status().ready
            || TcpStream::connect(("127.0.0.1", port))
                .await
                .is_ok()
        {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("a current policy cannot keep an expired TLS certificate online");
    assert!(Utc::now().timestamp() >= expires_at.unix_timestamp());
    shutdown.send(true).unwrap();
    task.await.unwrap().unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires INARI_TEST_ZENOHD pointing to the stock Zenoh 1.9 executable"]
async fn stock_router_isolates_agents_closes_old_links_and_expires_policy() {
    let executable = std::env::var_os("INARI_TEST_ZENOHD").expect("stock Zenoh executable");
    let directory = tempfile::tempdir().unwrap();
    let root = issuer("data-plane-test-root");
    let root_file = directory.path().join("root.pem");
    std::fs::write(&root_file, root.pem()).unwrap();
    for name in ["router-test", "controller-test", AGENT_A, AGENT_B] {
        let (certificate, key) = certificate(
            &root,
            name,
            if name == "router-test" {
                ExtendedKeyUsagePurpose::ServerAuth
            } else {
                ExtendedKeyUsagePurpose::ClientAuth
            },
        );
        std::fs::write(
            directory
                .path()
                .join(format!("{name}.pem")),
            certificate,
        )
        .unwrap();
        std::fs::write(
            directory
                .path()
                .join(format!("{name}.key")),
            key,
        )
        .unwrap();
    }
    let free_port = StandardListener::bind("127.0.0.1:0").unwrap();
    let port = free_port.local_addr().unwrap().port();
    drop(free_port);
    let config = RouterConfig {
        id: "1234567890abcdef1234567890abcdef".into(),
        listen_endpoint: format!("tls/127.0.0.1:{port}"),
        probe_endpoint: format!("tls/127.0.0.1:{port}"),
        peer_endpoints: vec![],
        root_ca_file: root_file,
        certificate_file: directory.path().join("router-test.pem"),
        private_key_file: directory.path().join("router-test.key"),
        probe_certificate_file: directory
            .path()
            .join("controller-test.pem"),
        probe_private_key_file: directory
            .path()
            .join("controller-test.key"),
    };
    let key = SigningKey::from_bytes(&[11; 32]);
    let state_directory = directory.path().join("state");
    let (handle, supervisor) = RouterSupervisor::new(
        PolicyStore::open(&state_directory).unwrap(),
        key.verifying_key(),
        "fleet-test".into(),
        executable.clone().into(),
        config.clone(),
    )
    .unwrap();
    let (shutdown, receiver) = watch::channel(false);
    let task = tokio::spawn(supervisor.run(receiver));
    current(&handle, &key, 1, &[AGENT_A, AGENT_B]).await;

    let proxy = TcpListener::bind("127.0.0.1:0")
        .await
        .unwrap();
    let proxy_port = proxy.local_addr().unwrap().port();
    let closed = Arc::new(AtomicUsize::new(0));
    let (proxy_shutdown, mut proxy_stop) = watch::channel(false);
    let counter = closed.clone();
    let proxy_task = tokio::spawn(async move {
        let mut links = tokio::task::JoinSet::new();
        loop {
            tokio::select! {
                _ = proxy_stop.changed() => break,
                result = proxy.accept() => {
                    let (mut downstream,_) = result.unwrap();
                    let counter=counter.clone();
                    links.spawn(async move {
                        if let Ok(mut upstream)=TcpStream::connect(("127.0.0.1",port)).await {
                            let _=tokio::io::copy_bidirectional(&mut downstream,&mut upstream).await;
                            counter.fetch_add(1,Ordering::SeqCst);
                        }
                    });
                },
                Some(result)=links.join_next(), if !links.is_empty() => { result.unwrap(); },
            }
        }
        links.abort_all();
        while links.join_next().await.is_some() {}
    });
    let controller = connect(&config, "controller-test", port).await;
    let a = connect(&config, AGENT_A, proxy_port).await;
    let b = connect(&config, AGENT_B, port).await;
    let subscriber = controller
        .declare_subscriber("iot/v1/agents/**")
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(150)).await;
    a.put(format!("iot/v1/agents/{AGENT_A}/status/latest"), "own-A")
        .await
        .unwrap();
    let sample = tokio::time::timeout(Duration::from_secs(2), subscriber.recv_async())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(sample.payload().to_bytes().as_ref(), b"own-A");
    a.put(format!("iot/v1/agents/{AGENT_B}/status/latest"), "cross-Agent")
        .await
        .unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(200), subscriber.recv_async())
            .await
            .is_err()
    );
    b.put(format!("iot/v1/agents/{AGENT_B}/status/latest"), "own-B")
        .await
        .unwrap();
    let sample = tokio::time::timeout(Duration::from_secs(2), subscriber.recv_async())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(sample.payload().to_bytes().as_ref(), b"own-B");

    let history = controller
        .declare_queryable(format!("iot/v1/agents/{AGENT_A}/commands/history"))
        .await
        .unwrap();
    let reply = tokio::spawn(async move {
        let query = history.recv_async().await.unwrap();
        query
            .reply(query.key_expr().clone(), "history-A")
            .await
            .unwrap();
    });
    let querier = a
        .declare_querier(format!("iot/v1/agents/{AGENT_A}/commands/history"))
        .timeout(Duration::from_secs(2))
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(2), async {
        while !querier
            .matching_status()
            .await
            .unwrap()
            .matching()
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the allowed history queryable must reach the Agent");
    let replies = querier.get().await.unwrap();
    let response = replies.recv_async().await.unwrap();
    assert_eq!(
        response
            .result()
            .unwrap()
            .payload()
            .to_bytes()
            .as_ref(),
        b"history-A"
    );
    reply.await.unwrap();
    let forbidden = controller
        .declare_queryable(format!("iot/v1/agents/{AGENT_B}/commands/history"))
        .await
        .unwrap();
    let denied = a
        .get(format!("iot/v1/agents/{AGENT_B}/commands/history"))
        .timeout(Duration::from_millis(200))
        .await
        .unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(300), forbidden.recv_async())
            .await
            .is_err()
    );
    assert!(denied.recv_async().await.is_err());

    let before = closed.load(Ordering::SeqCst);
    current(&handle, &key, 2, &[AGENT_A, AGENT_B]).await;
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert_eq!(
        closed.load(Ordering::SeqCst),
        before,
        "a lifetime refresh must preserve established links"
    );
    a.put(format!("iot/v1/agents/{AGENT_A}/status/latest"), "after-refresh")
        .await
        .unwrap();
    let sample = tokio::time::timeout(Duration::from_secs(2), subscriber.recv_async())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(sample.payload().to_bytes().as_ref(), b"after-refresh");
    drop(subscriber);

    let before = closed.load(Ordering::SeqCst);
    current(&handle, &key, 3, &[AGENT_B]).await;
    tokio::time::timeout(Duration::from_secs(3), async {
        while closed.load(Ordering::SeqCst) == before {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("policy replacement must close the established Agent TCP link");
    controller.close().await.unwrap();
    let observer = connect(&config, "controller-test", port).await;
    let subscriber = observer
        .declare_subscriber("iot/v1/agents/**")
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(300)).await;
    a.put(format!("iot/v1/agents/{AGENT_A}/status/latest"), "revoked-A")
        .await
        .unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(200), subscriber.recv_async())
            .await
            .is_err()
    );
    let mut expiring = policy(4, &[AGENT_B]);
    expiring.expires_at = Utc::now() + chrono::Duration::seconds(3);
    assert!(
        handle
            .apply(SignedPolicy::sign(expiring, &key).unwrap())
            .await
            .unwrap()
            .ready
    );
    tokio::time::timeout(Duration::from_secs(6), async {
        while handle.status().ready {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("expired policy must stop Router readiness");
    tokio::time::sleep(Duration::from_millis(400)).await;
    assert!(
        TcpStream::connect(("127.0.0.1", port))
            .await
            .is_err()
    );
    a.close().await.unwrap();
    b.close().await.unwrap();
    observer.close().await.unwrap();
    proxy_shutdown.send(true).unwrap();
    proxy_task.await.unwrap();
    shutdown.send(true).unwrap();
    task.await.unwrap().unwrap();

    let (recovered, supervisor) = RouterSupervisor::new(
        PolicyStore::open(&state_directory).unwrap(),
        key.verifying_key(),
        "fleet-test".into(),
        executable.into(),
        config,
    )
    .unwrap();
    let (shutdown, receiver) = watch::channel(false);
    let task = tokio::spawn(supervisor.run(receiver));
    assert_eq!(recovered.status().generation, Some(4));
    assert!(!recovered.status().ready);
    assert!(matches!(
        recovered
            .apply(SignedPolicy::sign(policy(3, &[AGENT_A, AGENT_B]), &key).unwrap())
            .await,
        Err(RouterError::GenerationConflict)
    ));
    current(&recovered, &key, 5, &[AGENT_B]).await;
    shutdown.send(true).unwrap();
    task.await.unwrap().unwrap();
}
