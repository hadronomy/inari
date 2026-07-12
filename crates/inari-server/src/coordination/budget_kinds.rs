use super::{BudgetKind, BudgetPermit, sealed};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum InariApiRequest {}

impl sealed::Sealed for InariApiRequest {}

impl BudgetKind for InariApiRequest {
    const EXHAUSTED_MESSAGE: &'static str = "Too many Inari API requests are already running.";
    const CLOSED_MESSAGE: &'static str = "The Inari API request budget is no longer available.";
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ZenohRestRequest {}

impl sealed::Sealed for ZenohRestRequest {}

impl BudgetKind for ZenohRestRequest {
    const EXHAUSTED_MESSAGE: &'static str =
        "Too many concurrent Zenoh REST queries are already running.";

    const CLOSED_MESSAGE: &'static str = "The Zenoh REST query budget is no longer available.";
}

pub type InariApiPermit = BudgetPermit<InariApiRequest>;
pub type ZenohRestQueryPermit = BudgetPermit<ZenohRestRequest>;
