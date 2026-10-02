from __future__ import annotations

from dataclasses import dataclass

from ..client_trust import AuthorizedRequest, ClientTrustError, Permission
from ..core.failures import DomainFailure, ProblemCode
from ..print_jobs import (
    PairedClientScope,
    PrintIntentPage,
    PrintIntentQuery,
    PrintJobReader,
    PrintJob,
)


@dataclass(frozen=True, slots=True)
class PrintJobQueries:
    """Reconcile browser-owned Print Intents against Agent-owned Print Jobs."""

    reader: PrintJobReader

    async def reconcile(
        self,
        print_intent_ids: list[str],
        authorization: AuthorizedRequest,
    ) -> PrintIntentPage:
        scope = self._scope(authorization)
        return await self.reader.reconcile(
            PrintIntentQuery.from_ids(print_intent_ids, scope=scope)
        )

    async def get(self, job_id: str, authorization: AuthorizedRequest) -> PrintJob:
        job = await self.reader.get(job_id, scope=self._scope(authorization))
        if job is None:
            raise DomainFailure(ProblemCode.RESOURCE_NOT_FOUND)
        return job

    @staticmethod
    def _scope(authorization: AuthorizedRequest) -> PairedClientScope:
        try:
            authorization.require(Permission.JOBS_READ)
        except ClientTrustError as error:
            raise DomainFailure(ProblemCode.PERMISSION_DENIED) from error
        business = authorization.grant.scope.business
        if business.pos_configuration_id is None:
            raise DomainFailure(ProblemCode.BINDING_REQUIRED)
        return PairedClientScope(
            organization_id=business.organization_id,
            site_id=business.site_id,
            pos_configuration_id=business.pos_configuration_id,
            paired_client_id=authorization.grant.pairing_id,
        )


__all__ = ["PrintJobQueries"]
