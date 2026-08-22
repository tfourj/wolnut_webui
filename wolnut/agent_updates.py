import logging
import time

from wolnut.agent_client import AgentClient, AgentError, SecurityStore

logger = logging.getLogger("wolnut")


class AgentUpdateMonitor:
    def __init__(
        self,
        security_store: SecurityStore | None = None,
        check_interval: int = 60,
    ):
        self.security_store = security_store or SecurityStore()
        self.check_interval = check_interval
        self._last_checked: dict[str, float] = {}

    def poll(
        self, config, state_tracker, notifications, now: float | None = None
    ) -> None:
        if not config.notifications.events.agent_update_succeeded:
            return

        checked_at = time.time() if now is None else now
        for client in config.clients:
            shutdown = client.shutdown
            if (
                not client.enabled
                or not shutdown.agent_id
                or not shutdown.auto_update
                or checked_at - self._last_checked.get(client.name, 0)
                < self.check_interval
            ):
                continue
            self._last_checked[client.name] = checked_at
            try:
                result = AgentClient(
                    client.host,
                    shutdown.agent_port,
                    timeout=2,
                    security_store=self.security_store,
                ).status(shutdown.agent_id)
            except AgentError as error:
                logger.debug(
                    "Could not check automatic agent update status for %s: %s",
                    client.name,
                    error,
                )
                continue

            try:
                installed_at = int(result.get("update_installed_at", 0) or 0)
            except (TypeError, ValueError):
                installed_at = 0
            version = str(result.get("version", "") or "")
            if state_tracker.observe_agent_update(
                client.name,
                installed_at,
                version,
                str(result.get("update_source", "") or ""),
                str(result.get("update_status", "") or ""),
            ):
                notifications.send(
                    "agent_update_succeeded",
                    "Agent update installed",
                    f"{client.name} ({client.host}) updated to Wolnut agent {version}.",
                )
