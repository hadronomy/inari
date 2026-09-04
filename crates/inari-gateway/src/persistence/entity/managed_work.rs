use sea_orm::entity::prelude::*;

use super::value::{
    ManagedDocumentOperationValue, ManagedWorkStateValue, StoredReportBindingClaim,
    StoredSealedManagedDispatch,
};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "managed_work")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub managed_work_id: String,
    pub preflight_id: String,
    pub organization_id: String,
    pub database_name: String,
    pub company_id: String,
    pub site_id: String,
    pub agent_id: String,
    pub device_id: String,
    #[sea_orm(unique)]
    pub print_intent_id: String,
    pub operation: ManagedDocumentOperationValue,
    pub media_type: String,
    pub state: ManagedWorkStateValue,
    #[sea_orm(column_type = "JsonBinary")]
    pub binding_claim: StoredReportBindingClaim,
    pub payload_fingerprint: Vec<u8>,
    pub request_fingerprint: Vec<u8>,
    #[sea_orm(column_type = "JsonBinary")]
    pub sealed_document: StoredSealedManagedDispatch,
    pub payload_bytes: i64,
    #[sea_orm(column_type = "Text", nullable)]
    pub print_job_id: Option<String>,
    #[sea_orm(column_type = "Text", nullable)]
    pub error_code: Option<String>,
    pub message_key: String,
    pub expires_at: DateTimeWithTimeZone,
    pub idempotency_expires_at: DateTimeWithTimeZone,
    pub admitted_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
