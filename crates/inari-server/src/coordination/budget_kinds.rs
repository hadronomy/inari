use super::{BudgetKind, BudgetPermit, sealed};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ProtocolExecution {}

impl sealed::Sealed for ProtocolExecution {}

impl BudgetKind for ProtocolExecution {
    const EXHAUSTED_MESSAGE: &'static str = "The protocol execution budget is exhausted.";
    const CLOSED_MESSAGE: &'static str = "The protocol execution budget is no longer available.";
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ZenohRestRequest {}

impl sealed::Sealed for ZenohRestRequest {}

impl BudgetKind for ZenohRestRequest {
    const EXHAUSTED_MESSAGE: &'static str =
        "Too many concurrent Zenoh REST queries are already running.";

    const CLOSED_MESSAGE: &'static str = "The Zenoh REST query budget is no longer available.";
}

pub type ProtocolPermit = BudgetPermit<ProtocolExecution>;
pub type ZenohRestQueryPermit = BudgetPermit<ZenohRestRequest>;
