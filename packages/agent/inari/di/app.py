from __future__ import annotations

from dishka import Provider, Scope, provide

from ..config import AgentSettings
from ..db import DatabaseMigrator
from ..gateway.supervisor import GatewaySupervisor
from ..runtime.supervisor import RuntimeSupervisor
from ..supervision import ApplicationSupervisor


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def database_migrator(self, settings: AgentSettings) -> DatabaseMigrator:
        return DatabaseMigrator(settings.runtime_database_path)

    @provide
    def application_supervisor(
        self,
        runtime_supervisor: RuntimeSupervisor,
        gateway_supervisor: GatewaySupervisor,
    ) -> ApplicationSupervisor:
        return ApplicationSupervisor(
            runtime_supervisor=runtime_supervisor,
            gateway_supervisor=gateway_supervisor,
        )
