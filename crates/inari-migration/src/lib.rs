#![forbid(unsafe_code)]

use sea_orm_migration::MigrationTrait;
pub use sea_orm_migration::MigratorTrait;

mod m20260712_223023_create_fleet;
mod m20260712_223024_create_enrollment;
mod m20260712_223026_create_gateway_data;
mod m20260712_223027_create_sessions;

pub struct Migrator;

#[sea_orm_migration::async_trait::async_trait]
impl MigratorTrait for Migrator {
    fn migrations() -> Vec<Box<dyn MigrationTrait>> {
        vec![
            Box::new(m20260712_223023_create_fleet::Migration),
            Box::new(m20260712_223024_create_enrollment::Migration),
            Box::new(m20260712_223026_create_gateway_data::Migration),
            Box::new(m20260712_223027_create_sessions::Migration),
        ]
    }
}
