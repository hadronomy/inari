from __future__ import annotations

from ..core.failures import DomainFailure


class SpoolAdmissionError(DomainFailure):
    """A fixed, content-free failure from durable spool admission."""


__all__ = ["SpoolAdmissionError"]
