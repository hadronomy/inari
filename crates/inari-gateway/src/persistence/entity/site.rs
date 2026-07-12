use sea_orm::entity::prelude::*;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "sites")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub site_id: String,
    pub organization_id: String,
    pub name: String,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
    #[sea_orm(has_many)]
    pub agents: HasMany<super::agent::Entity>,
    #[sea_orm(has_many)]
    pub devices: HasMany<super::device::Entity>,
}

impl ActiveModelBehavior for ActiveModel {}
