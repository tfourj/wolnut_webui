import logging
import yaml

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wolnut.state import DEFAULT_STATE_FILEPATH
from wolnut.utils import validate_mac_format, resolve_mac_from_host

logger = logging.getLogger("wolnut")

DEFAULT_CONFIG_FILEPATHS = ["/config/config.yaml", "./config.yaml"]
DEFAULT_LOG_LEVEL = "INFO"


@dataclass
class NutConfig:
    ups: str
    port: int = 3493
    timeout: int = 5
    username: str | None = None
    password: str | None = None


@dataclass
class WakeOnConfig:
    restore_delay_sec: int = 30
    min_battery_percent: int = 20
    client_timeout_sec: int = 360
    reattempt_delay: int = 30


@dataclass
class ShutdownConfig:
    enabled: bool = False
    battery_percent: int = 20
    agent_id: str | None = None
    agent_port: int = 8184


@dataclass
class ClientConfig:
    name: str
    host: str
    mac: str = ""  # "auto" supported when Wake-on-LAN is enabled
    always_wake: bool = False  # if True, wake even if offline before power loss (default: only if was online)
    enabled: bool = True  # if False, client is ignored (no ping, no WOL) - useful to temporarily disable
    wake_enabled: bool = True
    shutdown: ShutdownConfig = field(default_factory=ShutdownConfig)


@dataclass
class WebUIConfig:
    suppress_mac_warnings: bool = False


@dataclass
class DiscordNotificationConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass
class GotifyNotificationConfig:
    enabled: bool = False
    url: str = ""
    token: str = ""
    priority: int = 5


@dataclass
class NtfyNotificationConfig:
    enabled: bool = False
    url: str = "https://ntfy.sh"
    topic: str = ""
    token: str = ""
    priority: int = 3


@dataclass
class NotificationEventsConfig:
    power_loss: bool = True
    power_restored: bool = True
    wake_sent: bool = True
    client_recovered: bool = True
    errors: bool = True
    shutdown_acknowledged: bool = True
    shutdown_failed: bool = True


@dataclass
class NotificationsConfig:
    discord: DiscordNotificationConfig = field(default_factory=DiscordNotificationConfig)
    gotify: GotifyNotificationConfig = field(default_factory=GotifyNotificationConfig)
    ntfy: NtfyNotificationConfig = field(default_factory=NtfyNotificationConfig)
    events: NotificationEventsConfig = field(default_factory=NotificationEventsConfig)


@dataclass
class WolnutConfig:
    nut: NutConfig
    status_file: str
    poll_interval: int = 10
    wake_on: WakeOnConfig = field(default_factory=WakeOnConfig)
    clients: list[ClientConfig] = field(default_factory=list)
    log_level: str = "INFO"
    webui: WebUIConfig = field(default_factory=WebUIConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


def notifications_config_from_dict(raw: dict | None) -> NotificationsConfig:
    notifications_raw = raw or {}
    discord_raw = notifications_raw.get("discord", {}) or {}
    gotify_raw = notifications_raw.get("gotify", {}) or {}
    ntfy_raw = notifications_raw.get("ntfy", {}) or {}
    events_raw = notifications_raw.get("events", {}) or {}
    discord = DiscordNotificationConfig(
        **{
            k: v
            for k, v in discord_raw.items()
            if k in DiscordNotificationConfig.__dataclass_fields__
        }
    )
    gotify = GotifyNotificationConfig(
        **{
            k: v
            for k, v in gotify_raw.items()
            if k in GotifyNotificationConfig.__dataclass_fields__
        }
    )
    ntfy = NtfyNotificationConfig(
        **{
            k: v
            for k, v in ntfy_raw.items()
            if k in NtfyNotificationConfig.__dataclass_fields__
        }
    )
    events = NotificationEventsConfig(
        **{
            k: v
            for k, v in events_raw.items()
            if k in NotificationEventsConfig.__dataclass_fields__
        }
    )
    return NotificationsConfig(
        discord=discord,
        gotify=gotify,
        ntfy=ntfy,
        events=events,
    )


def find_state_file(state_file: Optional[str] = None) -> str:
    """Find an existing state file or return a writable default path."""
    path = Path(state_file or DEFAULT_STATE_FILEPATH)
    if not state_file:
        logger.warning("No state file specified, using default: %s", path)

    # Ensure the parent directory exists
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("Could not create directory for state file '%s': %s", path, e)
        # Depending on desired behavior, you might want to exit or raise here.

    return str(path)


def load_config(
    config_path: str, status_path: str = None, verbose: bool = False
) -> Optional[WolnutConfig]:
    try:
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)
        validate_config(raw)
    except FileNotFoundError:
        logger.error("Config file not found at '%s'.", config_path)
        return None
    except Exception:
        logger.exception("Failed to load or parse config file: '%s'.\n", config_path)
        return None

    # LOGGING...
    nut = NutConfig(**raw["nut"])

    # get wake_on or use defaults
    wake_on = WakeOnConfig(**raw.get("wake_on", {}))

    # get webui settings (new WebUI Settings section)
    webui_raw = raw.get("webui", {}) or {}
    # legacy support: top-level suppress_mac_warnings
    if "suppress_mac_warnings" in raw and "suppress_mac_warnings" not in webui_raw:
        webui_raw["suppress_mac_warnings"] = raw["suppress_mac_warnings"]
    # filter to known fields to avoid TypeError on extra keys
    webui_filtered = {k: v for k, v in webui_raw.items() if k in WebUIConfig.__dataclass_fields__}
    webui = WebUIConfig(**webui_filtered)

    notifications = notifications_config_from_dict(raw.get("notifications"))

    # Determine status file path: CLI arg > config file > default
    final_status_path = status_path or raw.get("status_file")
    # find_state_file will handle None and also ensure the directory exists
    final_status_path = find_state_file(final_status_path)

    clients = []
    allowed_client_fields = set(ClientConfig.__dataclass_fields__.keys())
    for raw_client in raw["clients"]:
        try:
            shutdown_raw = raw_client.get("shutdown", {}) or {}
            shutdown = ShutdownConfig(
                **{
                    key: value
                    for key, value in shutdown_raw.items()
                    if key in ShutdownConfig.__dataclass_fields__
                }
            )
            wake_enabled = raw_client.get("wake_enabled", True)
            mac = raw_client.get("mac", "")
            if wake_enabled and mac == "auto":
                logger.info(
                    "Resolving MAC for %s at %s...",
                    raw_client["name"],
                    raw_client["host"],
                )
                resolved_mac = resolve_mac_from_host(raw_client["host"])
                if not resolved_mac:
                    raise ValueError(
                        f"Could not resolve MAC address for {raw_client['name']} ({raw_client['host']})"
                    )
                raw_client["mac"] = resolved_mac
                logger.info("MAC for %s: %s", raw_client["name"], resolved_mac)

            # Filter to known fields so unknown keys don't crash, but keep defaults for new optional fields
            filtered = {
                k: v
                for k, v in raw_client.items()
                if k in allowed_client_fields and k != "shutdown"
            }
            filtered["shutdown"] = shutdown
            clients.append(ClientConfig(**filtered))
        except ValueError as e:
            logger.error("Failed to load client %s: %s", raw_client.get("name", "?"), e)

    wolnut_config = WolnutConfig(
        nut=nut,
        poll_interval=raw.get("poll_interval", 10),
        wake_on=wake_on,
        clients=clients,
        log_level=raw.get("log_level", DEFAULT_LOG_LEVEL).upper(),
        status_file=final_status_path,
        webui=webui,
        notifications=notifications,
    )
    logger.info("Config Imported Successfully")
    for client in wolnut_config.clients:
        logger.info("Client: %s at MAC: %s", client.name, client.mac)

    return wolnut_config


def validate_config(raw: dict):
    if "clients" not in raw or not isinstance(raw["clients"], list):
        raise ValueError("Missing or invalid 'clients' list")

    if "nut" not in raw or "ups" not in raw["nut"]:
        raise ValueError("Missing required field: 'nut.ups'")

    if "status_file" not in raw:
        logger.warning("No 'status_file' specified in config, using default.")

    names: set[str] = set()
    for i, client in enumerate(raw["clients"]):
        if "name" not in client:
            raise ValueError(f"Client #{i} is missing required field: 'name'")
        if "host" not in client:
            raise ValueError(
                f"Client '{client.get('name', '?')}' is missing required field: 'host'"
            )
        name = client["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Client #{i} has an invalid empty name")
        if name in names:
            raise ValueError(f"Client names must be unique: '{name}'")
        names.add(name)

        wake_enabled = client.get("wake_enabled", True)
        if not isinstance(wake_enabled, bool):
            raise ValueError(
                f"Client '{name}' has invalid 'wake_enabled' (must be boolean)"
            )
        if wake_enabled and "mac" not in client:
            raise ValueError(
                f"Client '{client['name']}' is missing required field: 'mac'"
            )

        mac = client.get("mac", "")
        if not isinstance(mac, str):
            raise ValueError(
                f"Client '{client['name']}' has invalid mac format (should be string or 'auto')"
            )
        if wake_enabled and mac != "auto" and not validate_mac_format(mac):
            raise ValueError(
                f"Client '{client['name']}' has invalid MAC address format: {mac}"
            )
        if "always_wake" in client and not isinstance(client["always_wake"], bool):
            raise ValueError(f"Client '{client['name']}' has invalid 'always_wake' (must be boolean)")
        if "enabled" in client and not isinstance(client["enabled"], bool):
            raise ValueError(f"Client '{client['name']}' has invalid 'enabled' (must be boolean)")

        shutdown = client.get("shutdown", {}) or {}
        if not isinstance(shutdown, dict):
            raise ValueError(f"Client '{name}' has invalid 'shutdown' settings")
        shutdown_enabled = shutdown.get("enabled", False)
        if not isinstance(shutdown_enabled, bool):
            raise ValueError(
                f"Client '{name}' has invalid 'shutdown.enabled' (must be boolean)"
            )
        battery_percent = shutdown.get("battery_percent", 20)
        if (
            not isinstance(battery_percent, int)
            or isinstance(battery_percent, bool)
            or not 1 <= battery_percent <= 100
        ):
            raise ValueError(
                f"Client '{name}' shutdown battery percentage must be between 1 and 100"
            )
        agent_port = shutdown.get("agent_port", 8184)
        if (
            not isinstance(agent_port, int)
            or isinstance(agent_port, bool)
            or not 1 <= agent_port <= 65535
        ):
            raise ValueError(
                f"Client '{name}' shutdown agent port must be between 1 and 65535"
            )
        agent_id = shutdown.get("agent_id")
        if agent_id is not None and (
            not isinstance(agent_id, str) or not agent_id.strip()
        ):
            raise ValueError(f"Client '{name}' has invalid shutdown agent ID")
        if shutdown_enabled and not agent_id:
            raise ValueError(
                f"Client '{name}' must be paired before automatic shutdown is enabled"
            )
