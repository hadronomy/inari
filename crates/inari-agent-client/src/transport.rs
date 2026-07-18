//! Generated local-agent HTTP transport.
//!
//! Do not re-export this module. The public client maps every generated type
//! into Inari's domain vocabulary before it crosses the crate boundary.

progenitor::generate_api!(
    spec = { path = "local-agent.codegen.json", relative_to = OutDir },
    tags = Merged,
);
