use sea_orm::entity::prelude::*;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "invitation_attempts")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub invitation_id: String,
    #[sea_orm(primary_key, auto_increment = false)]
    pub attempted_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
