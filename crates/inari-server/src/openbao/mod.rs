//! Shared bounded OpenBao transport and Controller authority signing.

mod client;
mod signing;

pub use client::OpenBaoClient;
pub use signing::AuthoritySigningKey;
