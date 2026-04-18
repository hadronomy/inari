from __future__ import annotations


class GatewayZenohKeyspace:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace.rstrip("/")

    def status_latest(self) -> str:
        return f"{self.namespace}/status/latest"

    def live_commands(self) -> str:
        return f"{self.namespace}/commands/live/**"

    def command_history(self) -> str:
        return f"{self.namespace}/commands/history"

    def presence(self) -> str:
        return f"{self.namespace}/presence/agent"

    def event(self, message_id: str) -> str:
        return f"{self.namespace}/events/{message_id}"

    def result(self, command_id: str) -> str:
        return f"{self.namespace}/results/{command_id}"

    def error(self, message_id: str) -> str:
        return f"{self.namespace}/errors/{message_id}"
