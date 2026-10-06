use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use inari_gateway::device_authority::{
    AuthorityTime, CanonicalRecord, HexBytes, SignedAuthorityRevision, SignedBindingRevision,
    SignedDriverProfile, SignedHardwareCertificationMatrixRow, SignerRecord,
};
use secrecy::SecretString;
use serde::Deserialize;
use serde_json::Value;
use url::Url;
use zeroize::Zeroizing;

use super::{CachedToken, OpenBaoClient};
use crate::config::OpenBaoConfig;
use crate::openbao::AuthoritySigningKey;

#[derive(Deserialize)]
struct TestServer {
    address: Url,
    token: String,
}

#[tokio::test]
#[ignore = "Requires a disposable OpenBao 2.5.4 server and INARI_OPENBAO_TEST_STATE"]
async fn approved_signatures_match_openbao_2_5_4() {
    let path =
        std::env::var_os("INARI_OPENBAO_TEST_STATE").expect("private test server state file");
    let bytes = Zeroizing::new(std::fs::read(path).unwrap());
    let server: TestServer = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(server.address.scheme(), "http");
    assert_eq!(server.address.host_str(), Some("127.0.0.1"));
    assert!(server.address.username().is_empty());
    assert!(server.address.password().is_none());
    assert!(server.address.query().is_none());
    assert!(server.address.fragment().is_none());
    assert_eq!(server.address.path(), "/");
    let client = Arc::new(
        OpenBaoClient::load_test(OpenBaoConfig {
            address: Some(server.address),
            ..Default::default()
        })
        .await
        .unwrap(),
    );
    *client.token.lock().await = Some(CachedToken {
        value: SecretString::from(server.token),
        refresh_at: Instant::now() + Duration::from_secs(60),
    });
    let health: Value = client
        .get("v1/sys/health")
        .await
        .unwrap();
    assert_eq!(health["version"], "2.5.4");
    let vectors: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../contracts/device-authority.test-vectors.json"
    )))
    .unwrap();
    let case = &vectors["cases"][0];
    let now: AuthorityTime = serde_json::from_value(case["now"].clone()).unwrap();
    let records = case["records"].as_array().unwrap();
    for record in records.iter().take(4) {
        let purpose = record["purpose"].as_str().unwrap();
        let name = format!("inari-test-{}", purpose.replace('_', "-"));
        let metadata: Value = client
            .get(&format!("v1/transit/keys/{name}"))
            .await
            .unwrap();
        let public_key: [u8; 32] = STANDARD
            .decode(
                metadata["data"]["keys"]["1"]["public_key"]
                    .as_str()
                    .unwrap(),
            )
            .unwrap()
            .try_into()
            .unwrap();
        let mut signer: SignerRecord = serde_json::from_value(record["signer"].clone()).unwrap();
        signer.public_key = HexBytes::new(public_key);
        let key = AuthoritySigningKey::load(
            client.clone(),
            "transit".into(),
            name,
            NonZeroU32::new(1).unwrap(),
            signer.clone(),
        )
        .await
        .unwrap();
        macro_rules! verify {
            ($signed:ty, $field:ident) => {{
                let signed: $signed = serde_json::from_value(record["record"].clone()).unwrap();
                let canonical = CanonicalRecord::new(signed.$field).unwrap();
                assert_eq!(
                    canonical.as_bytes(),
                    record["canonical"]
                        .as_str()
                        .unwrap()
                        .as_bytes()
                );
                let signature = key.sign(&canonical, now).await.unwrap();
                <$signed>::from_signature(canonical, signer.key_id.clone(), signature)
                    .verify(&signer, now)
                    .unwrap();
            }};
        }
        match purpose {
            "authority_revision" => verify!(SignedAuthorityRevision, revision),
            "driver_profile" => verify!(SignedDriverProfile, profile),
            "certification_matrix" => verify!(SignedHardwareCertificationMatrixRow, row),
            "binding_revision" => verify!(SignedBindingRevision, revision),
            other => panic!("unexpected Controller purpose: {other}"),
        }
    }
}
