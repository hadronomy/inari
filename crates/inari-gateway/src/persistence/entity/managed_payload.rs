use sea_orm::entity::prelude::*;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "managed_payloads")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub managed_work_id: String,
    pub ciphertext: Vec<u8>,
    pub nonce: Vec<u8>,
    pub wrapped_data_key: String,
    pub wrapping_key_version: i32,
    pub authenticated_data_digest: Vec<u8>,
    pub payload_fingerprint: Vec<u8>,
    pub plaintext_bytes: i64,
    pub expires_at: DateTimeWithTimeZone,
    pub created_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
