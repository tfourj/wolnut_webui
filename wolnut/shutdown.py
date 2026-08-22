import logging

from dataclasses import dataclass

from wolnut.agent_client import AgentClient, AgentError, SecurityStore

logger = logging.getLogger("wolnut")


@dataclass(frozen=True)
class UpsSnapshot:
    mode: str
    battery_percent: int


def parse_ups_snapshot(status: dict) -> UpsSnapshot | None:
    """Return a trusted OL/OB snapshot, or None when NUT data is unsafe to act on."""
    raw_status = status.get("ups.status")
    raw_battery = status.get("battery.charge")
    if not isinstance(raw_status, str) or raw_battery is None:
        return None
    tokens = set(raw_status.upper().split())
    if "OB" in tokens:
        mode = "OB"
    elif "OL" in tokens:
        mode = "OL"
    else:
        return None
    try:
        battery = round(float(raw_battery))
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 <= battery <= 100:
        return None
    return UpsSnapshot(mode=mode, battery_percent=battery)


class ShutdownCoordinator:
    def __init__(self, security_store: SecurityStore | None = None):
        self.security_store = security_store or SecurityStore()

    def process_on_battery(
        self,
        config,
        state_tracker,
        notifications,
        battery_percent: int,
    ) -> None:
        outage_id = state_tracker.outage_id()
        if not outage_id:
            return
        retry_delay = max(int(config.poll_interval), 5)
        for client in config.clients:
            shutdown = client.shutdown
            if (
                not client.enabled
                or not shutdown.enabled
                or not shutdown.agent_id
                or battery_percent > shutdown.battery_percent
                or not state_tracker.should_attempt_shutdown(client.name, retry_delay)
            ):
                continue

            previous = state_tracker.shutdown_state(client.name)
            command_id = previous.get("command_id") or f"{outage_id}:{client.name}"
            agent = AgentClient(
                client.host,
                shutdown.agent_port,
                security_store=self.security_store,
            )
            try:
                response = agent.shutdown(
                    shutdown.agent_id,
                    command_id,
                    "automatic",
                    ups=config.nut.ups,
                    battery_percent=battery_percent,
                    threshold_percent=shutdown.battery_percent,
                )
                if response.get("status") != "accepted":
                    raise AgentError("Agent did not acknowledge the shutdown request")
            except AgentError as error:
                message = str(error)
                state_tracker.record_shutdown_attempt(
                    client.name,
                    command_id,
                    battery_percent,
                    "automatic",
                    error=message,
                )
                if not previous.get("failure_notified", False):
                    notifications.send(
                        "shutdown_failed",
                        "Device shutdown delivery failed",
                        f"Wolnut could not reach {client.name}: {message}",
                    )
                    state_tracker.mark_shutdown_failure_notified(client.name)
                logger.warning(
                    "Shutdown delivery failed for %s: %s", client.name, message
                )
                continue

            state_tracker.record_shutdown_attempt(
                client.name,
                command_id,
                battery_percent,
                "automatic",
            )
            state_tracker.acknowledge_shutdown(client.name)
            notifications.send(
                "shutdown_acknowledged",
                "Device shutdown accepted",
                (
                    f"{client.name} accepted a shutdown request at "
                    f"{battery_percent}% UPS charge."
                ),
            )
            logger.warning("Shutdown accepted by %s", client.name)
