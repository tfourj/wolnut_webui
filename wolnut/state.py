import json
import logging
import time
import uuid

from hashlib import md5
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("wolnut")

DEFAULT_STATE_FILEPATH = "/config/wolnut_state.json"
ASSUME_UNINITIALIZED_ONLINE = False  # Assume clients are online if no state file exists


class ClientStateTracker:
    """
    Tracks the online/offline state of client systems and Wake-on-LAN activity
    during and after a UPS battery event.

    Attributes:
        _clients (list): List of clients being tracked.
        _status_file (str): Path to the JSON file for persisting state.
        _client_states (dict): Tracks per-client status info.
        _meta_state (dict): Tracks global UPS-related status.

    Methods:
        update(client_name, online): Updates online status.
        mark_wol_sent(client_name): Marks a client as having been sent a WOL packet.
        reset(): Clears all stored state information.
        ...
    """

    def __init__(self, clients: List[Any], status_file: str):
        # Search default locations for existing state file
        if not status_file:
            raise ValueError("A status file must be specified.")

        self._status_file = Path(status_file)  # Filename for storing state data
        self._status_hash = None  # Hash of the current/previous status file contents
        self._dirty = False  # Whether the state has changed since last save
        self._meta_state: Dict[str, Any] = {
            "ups_on_battery": False,
            "battery_percent_at_shutdown": 100,
            "outage_id": None,
        }
        self._client_states: Dict[str, Dict[str, Any]] = {}

        # Load existing state from file first
        if self._status_file.exists():
            self._load_state()

        # Initialize any clients not in the loaded state
        for client in clients:
            if client.name not in self._client_states:
                self._client_states[client.name] = {
                    "was_online_before_battery": ASSUME_UNINITIALIZED_ONLINE,
                    "is_online": False,
                    "wol_sent": False,
                    "wol_sent_at": 0,
                    "skip": False,
                    "shutdown": self._new_shutdown_state(),
                    "agent_update": self._new_agent_update_state(),
                }

    @staticmethod
    def _new_shutdown_state() -> Dict[str, Any]:
        return {
            "command_id": None,
            "last_attempt_at": 0,
            "acknowledged": False,
            "acknowledged_at": 0,
            "last_error": None,
            "battery_percent": None,
            "source": None,
            "failure_notified": False,
            "offline_after_ack": False,
            "delivery_sequence": 0,
        }

    @staticmethod
    def _new_agent_update_state() -> Dict[str, Any]:
        return {
            "initialized": False,
            "installed_at": 0,
            "version": "",
        }

    def _load_state(self):
        """
        Loads the state from the JSON file, if it exists.
        """
        try:
            with open(self._status_file, "r") as f:
                raw_data = f.read()

            # Remember the hash of the loaded data to avoid unnecessary writes later
            # I'm aware that md5 is not cryptographically secure, but this is not a security use case.
            status_hash = md5(raw_data.encode("utf-8")).hexdigest()
            save_data = json.loads(raw_data)
            self._meta_state.update(save_data["meta"])
            self._client_states = save_data.get("clients", {})
            for client_state in self._client_states.values():
                shutdown = self._new_shutdown_state()
                shutdown.update(client_state.get("shutdown", {}))
                client_state["shutdown"] = shutdown
                client_state.setdefault("agent_update", self._new_agent_update_state())
            self._status_hash = status_hash
            logger.info("State loaded from %s", self._status_file)
        except Exception as e:
            logger.warning("Failed to load state from file: %s", e)

    def save_state(self):
        """
        Saves the current state to the JSON file, if it has changed since the last save.
        The number of try/except blocks is intentional to ensure that errors are well
        documentented for debugging.
        """
        if not self._dirty:
            return

        save_data = {"meta": self._meta_state, "clients": self._client_states}
        try:
            # Make it pretty for humans
            raw_data = json.dumps(
                save_data, indent=4, separators=(",", ": "), sort_keys=True
            )
            new_hash = md5(raw_data.encode("utf-8")).hexdigest()
            if self._status_hash == new_hash:
                logging.debug("State unchanged, skipping save.")
                return
        except Exception:
            logger.exception("Failed to serialize state to JSON.")
            return

        temp_state_file = self._status_file.with_suffix(".json.tmp")
        try:
            with temp_state_file.open("w") as f:
                f.write(raw_data)
        except Exception:
            logger.exception(
                "Failed to write temporary state file: '%s'", temp_state_file
            )
            return

        try:
            temp_state_file.replace(self._status_file)
        except Exception:
            logger.exception(
                "Failed to move temporary state to permanent: '%s' to '%s'",
                temp_state_file,
                self._status_file,
            )

        self._status_hash = new_hash
        self._dirty = False
        logger.debug("State saved to %s", self._status_file)

    def update(self, client_name: str, online: bool):
        if client_name not in self._client_states:
            return
        state = self._client_states[client_name]
        was_online = state["is_online"]
        if was_online == online:
            return

        state["is_online"] = online
        shutdown = state.setdefault("shutdown", self._new_shutdown_state())
        if shutdown.get("acknowledged") and was_online and not online:
            shutdown["offline_after_ack"] = True
        elif (
            shutdown.get("acknowledged")
            and not was_online
            and online
            and shutdown.get("offline_after_ack")
        ):
            delivery_sequence = int(shutdown.get("delivery_sequence", 0) or 0) + 1
            state["shutdown"] = self._new_shutdown_state()
            state["shutdown"]["delivery_sequence"] = delivery_sequence
            logger.info(
                "Re-arming shutdown for %s after it came back online during the outage",
                client_name,
            )
        self._dirty = True

    def mark_wol_sent(self, client_name: str):
        if client_name in self._client_states:
            self._client_states[client_name]["wol_sent"] = True
            self._client_states[client_name]["wol_sent_at"] = int(time.time())
            self._dirty = True

    def mark_skip(self, client_name: str):
        if client_name in self._client_states and not self._client_states[
            client_name
        ].get("skip", False):
            self._client_states[client_name]["skip"] = True
            self._dirty = True

    def mark_all_online_clients(self):
        for name, state in self._client_states.items():
            state["was_online_before_battery"] = state["is_online"]
            self._dirty = True

    def is_online(self, client_name: str) -> bool:
        return self._client_states.get(client_name, {}).get("is_online", False)

    def was_online_before_shutdown(self, client_name: str) -> bool:
        return self._client_states.get(client_name, {}).get(
            "was_online_before_battery", False
        )

    def has_been_wol_sent(self, client_name: str) -> bool:
        return self._client_states.get(client_name, {}).get("wol_sent", False)

    def should_attempt_wol(self, client_name: str, reattempt_delay: int) -> bool:
        state = self._client_states.get(client_name, {})
        last = state.get("wol_sent_at", 0)
        return time.time() - last >= reattempt_delay

    def should_skip(self, client_name: str) -> bool:
        return self._client_states.get(client_name, {}).get("skip", False)

    def set_ups_on_battery(self, is_on_battery: bool, battery_percent: int = 100):
        if self._meta_state["ups_on_battery"] != is_on_battery:
            self._meta_state["ups_on_battery"] = is_on_battery
            self._dirty = True

        if is_on_battery and not self._meta_state.get("outage_id"):
            self._meta_state["outage_id"] = str(uuid.uuid4())
            self._dirty = True

        if self._meta_state["battery_percent_at_shutdown"] != battery_percent:
            self._meta_state["battery_percent_at_shutdown"] = battery_percent
            self._dirty = True

    def was_ups_on_battery(self) -> bool:
        return self._meta_state["ups_on_battery"]

    def outage_id(self) -> str | None:
        return self._meta_state.get("outage_id")

    def begin_outage(self, battery_percent: int) -> str:
        if not self.was_ups_on_battery():
            self._meta_state["outage_id"] = str(uuid.uuid4())
            for state in self._client_states.values():
                state["shutdown"] = self._new_shutdown_state()
            self._dirty = True
        self.set_ups_on_battery(True, battery_percent)
        return self._meta_state["outage_id"]

    def shutdown_state(self, client_name: str) -> Dict[str, Any]:
        state = self._client_states.get(client_name, {})
        return state.get("shutdown", self._new_shutdown_state())

    def should_attempt_shutdown(self, client_name: str, retry_delay: int) -> bool:
        shutdown = self.shutdown_state(client_name)
        if shutdown.get("acknowledged"):
            return False
        return time.time() - shutdown.get("last_attempt_at", 0) >= retry_delay

    def record_shutdown_attempt(
        self,
        client_name: str,
        command_id: str,
        battery_percent: int,
        source: str,
        error: str | None = None,
    ) -> None:
        if client_name not in self._client_states:
            return
        shutdown = self._client_states[client_name].setdefault(
            "shutdown", self._new_shutdown_state()
        )
        shutdown.update(
            {
                "command_id": command_id,
                "last_attempt_at": int(time.time()),
                "battery_percent": battery_percent,
                "source": source,
                "last_error": error,
            }
        )
        self._dirty = True

    def acknowledge_shutdown(self, client_name: str) -> None:
        if client_name not in self._client_states:
            return
        shutdown = self._client_states[client_name].setdefault(
            "shutdown", self._new_shutdown_state()
        )
        shutdown.update(
            {
                "acknowledged": True,
                "acknowledged_at": int(time.time()),
                "last_error": None,
            }
        )
        self._dirty = True

    def mark_shutdown_failure_notified(self, client_name: str) -> None:
        if client_name in self._client_states:
            shutdown = self._client_states[client_name].setdefault(
                "shutdown", self._new_shutdown_state()
            )
            shutdown["failure_notified"] = True
            self._dirty = True

    def observe_agent_update(
        self,
        client_name: str,
        installed_at: int,
        version: str,
        source: str,
        status: str,
    ) -> bool:
        if client_name not in self._client_states:
            return False
        update = self._client_states[client_name].setdefault(
            "agent_update", self._new_agent_update_state()
        )
        previous = int(update.get("installed_at", 0) or 0)
        initialized = bool(update.get("initialized", False))
        if not initialized or installed_at > previous:
            update.update(
                {
                    "initialized": True,
                    "installed_at": installed_at,
                    "version": version,
                }
            )
            self._dirty = True
        if not initialized:
            return False
        return (
            installed_at > previous
            and installed_at > 0
            and source == "automatic"
            and status == "updated"
        )

    def reset(self):
        for state in self._client_states.values():
            state.update(
                {
                    "was_online_before_battery": False,
                    "wol_sent": False,
                    "wol_sent_at": 0,
                    "skip": False,
                }
            )
        # Also reset the meta state for a complete reset
        self.set_ups_on_battery(False)
        self._meta_state["outage_id"] = None
        self._dirty = True

    def sync_clients(self, clients: List[Any]):
        """Sync tracker with new client list (hot-reload). Adds new clients, removes deleted ones."""
        new_names = {c.name for c in clients}
        # Add new clients
        for client in clients:
            if client.name not in self._client_states:
                self._client_states[client.name] = {
                    "was_online_before_battery": ASSUME_UNINITIALIZED_ONLINE,
                    "is_online": False,
                    "wol_sent": False,
                    "wol_sent_at": 0,
                    "skip": False,
                    "shutdown": self._new_shutdown_state(),
                    "agent_update": self._new_agent_update_state(),
                }
                self._dirty = True
                logger.info("Tracking new client: %s", client.name)
        # Remove deleted clients
        for name in list(self._client_states.keys()):
            if name not in new_names:
                del self._client_states[name]
                self._dirty = True
                logger.info("Stopped tracking removed client: %s", name)
