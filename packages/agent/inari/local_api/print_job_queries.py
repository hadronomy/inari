from __future__ import annotations

from dataclasses import dataclass

from ..client_trust import AuthorizedRequest, ClientTrustError, Permission
from ..core.failures import DomainFailure, ProblemCode
from ..print_jobs import (
    PairedClientScope,
    PrintIntentPage,
    PrintIntentQuery,
    PrintJobReader,
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
        try:
            authorization.require(Permission.JOBS_READ)
        except ClientTrustError as error:
            raise DomainFailure(ProblemCode.PERMISSION_DENIED) from error
        business = authorization.grant.scope.business
        if business.pos_configuration_id is None:
            raise DomainFailure(ProblemCode.BINDING_REQUIRED)
        scope = PairedClientScope(
            organization_id=business.organization_id,
            site_id=business.site_id,
            pos_configuration_id=business.pos_configuration_id,
            paired_client_id=authorization.grant.pairing_id,
        )
        return await self.reader.reconcile(
            PrintIntentQuery.from_ids(print_intent_ids, scope=scope)
        )


__all__ = ["PrintJobQueries"]
