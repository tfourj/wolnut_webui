import click
import logging
import os
import time

from wolnut.agent_updates import AgentUpdateMonitor
from wolnut.config import load_config, DEFAULT_CONFIG_FILEPATHS
from wolnut.monitor import get_ups_status, is_client_online
from wolnut.notifications import NotificationService
from wolnut.shutdown import ShutdownCoordinator, parse_ups_snapshot
from wolnut.state import ClientStateTracker
from wolnut.wol import send_wol_packet

logger = logging.getLogger("wolnut")


def configure_logger(level: str):
    """
    Configures the root logger's level.
    Needed for unit testing since logging.basicConfig is a no-op if the root logger already has handlers.
    """
    logger.setLevel(level)


def get_battery_percent(ups_status):
    return round(float(ups_status.get("battery.charge", 100)))


def main(config_file: str, status_file: str, verbose: bool = False) -> int:
    """MAIN LOOP"""
    config = load_config(config_file, status_path=status_file, verbose=verbose)
    if not config:
        return 1

    configure_logger(config.log_level)
    logger.info("WOLNUT started. Monitoring UPS: %s", config.nut.ups)
    notifications = NotificationService(config.notifications)

    # Track config mtime for hot-reload (WebUI can edit without restart)
    from pathlib import Path as _Path

    def _get_mtime(p: str) -> float:
        try:
            return _Path(p).stat().st_mtime
        except OSError:
            return 0

    last_mtime = _get_mtime(config_file)

    on_battery = False
    recorded_down_clients = set()
    recorded_up_clients = set()
    battery_percent = 100
    restoration_event = False
    restoration_event_start = None
    wol_being_sent = False
    reported_wol_failures = set()

    state_tracker = ClientStateTracker(config.clients, status_file=config.status_file)
    if state_tracker.was_ups_on_battery():
        logger.info("WOLNUT is resuming from a UPS battery event")
        on_battery = True
    shutdown_coordinator = ShutdownCoordinator()
    agent_update_monitor = AgentUpdateMonitor()

    ups_status = get_ups_status(
        config.nut.ups,
        username=config.nut.username,
        password=config.nut.password,
        port=config.nut.port,
        timeout=config.nut.timeout,
    )
    snapshot = parse_ups_snapshot(ups_status)
    if snapshot:
        battery_percent = snapshot.battery_percent
        power_status = snapshot.mode
        logger.info(
            "UPS power status: %s, Battery: %s%%", power_status, battery_percent
        )
    else:
        logger.warning(
            "UPS status is incomplete; shutdown and restoration actions are paused"
        )

    while True:
        # Hot-reload config if file changed (WebUI save)
        try:
            cur_mtime = _get_mtime(config_file)
            if cur_mtime != last_mtime and cur_mtime != 0:
                logger.info("Config file changed, reloading...")
                new_config = load_config(
                    config_file, status_path=status_file, verbose=verbose
                )
                if new_config:
                    # Apply new config dynamically
                    if new_config.log_level != config.log_level:
                        configure_logger(new_config.log_level)
                        logger.info("Log level changed to %s", new_config.log_level)
                    if new_config.status_file != config.status_file:
                        logger.warning(
                            "status_file changed from %s to %s — restart recommended for full effect",
                            config.status_file,
                            new_config.status_file,
                        )
                    config = new_config
                    notifications = NotificationService(config.notifications)
                    # Sync state tracker with new client list
                    state_tracker.sync_clients(config.clients)
                    # Reset recorded sets for removed/added clients (only enabled)
                    enabled_names = {
                        c.name for c in config.clients if getattr(c, "enabled", True)
                    }
                    recorded_down_clients.intersection_update(enabled_names)
                    recorded_up_clients.intersection_update(enabled_names)
                    logger.info(
                        "Config reloaded: %s clients, poll_interval=%s",
                        len(config.clients),
                        config.poll_interval,
                    )
                else:
                    logger.warning("Failed to reload config, keeping previous config")
                last_mtime = cur_mtime
        except Exception as e:
            logger.warning("Error checking/reloading config: %s", e)

        ups_status = get_ups_status(
            config.nut.ups,
            username=config.nut.username,
            password=config.nut.password,
            port=config.nut.port,
            timeout=config.nut.timeout,
        )
        snapshot = parse_ups_snapshot(ups_status)
        if snapshot is None:
            logger.warning(
                "UPS status is incomplete; shutdown and restoration actions are paused"
            )
            agent_update_monitor.poll(config, state_tracker, notifications)
            state_tracker.save_state()
            time.sleep(config.poll_interval)
            continue
        battery_percent = snapshot.battery_percent
        power_status = snapshot.mode

        logger.debug(
            "UPS power status: %s, Battery: %s%%", power_status, battery_percent
        )

        # Check each client
        for client in config.clients:
            if not getattr(client, "enabled", True):
                continue
            online = is_client_online(client.host)
            state_tracker.update(client.name, online)

        # Power Loss Event
        if power_status == "OB":
            if not on_battery:
                state_tracker.mark_all_online_clients()
                state_tracker.begin_outage(battery_percent)
                logger.warning("UPS switched to battery power.")
                notifications.send(
                    "power_loss",
                    "UPS switched to battery power",
                    f"Battery charge is {battery_percent}%.",
                )
            else:
                state_tracker.begin_outage(battery_percent)
            on_battery = True
            restoration_event = False
            restoration_event_start = None
            shutdown_coordinator.process_on_battery(
                config,
                state_tracker,
                notifications,
                battery_percent,
            )

        # Power Restoration Event
        elif power_status == "OL" and (on_battery or restoration_event):
            on_battery = False
            restoration_event = True

            if not restoration_event_start:
                restoration_event_start = time.time()
                notifications.send(
                    "power_restored",
                    "Power restored",
                    f"UPS is back online with {battery_percent}% battery.",
                )

            if battery_percent < config.wake_on.min_battery_percent:
                logger.info(
                    """Power restored, but battery still below
                    minimum percentage (%s%%/%s%%). Waiting...""",
                    battery_percent,
                    config.wake_on.min_battery_percent,
                )

            elif (
                time.time() - restoration_event_start < config.wake_on.restore_delay_sec
            ):
                logger.info(
                    "Power restored, waiting %s seconds before waking clients...",
                    int(
                        config.wake_on.restore_delay_sec
                        - (time.time() - restoration_event_start)
                    ),
                )

            else:
                if not wol_being_sent:
                    logger.info(
                        "Power restored and battery >= %s%%. Preparing to send WOL...",
                        config.wake_on.min_battery_percent,
                    )
                    wol_being_sent = True

                for client in config.clients:
                    if not getattr(client, "enabled", True):
                        continue

                    if not getattr(client, "wake_enabled", True):
                        state_tracker.mark_skip(client.name)
                        continue

                    if state_tracker.should_skip(client.name):
                        continue

                    if not getattr(
                        client, "always_wake", False
                    ) and not state_tracker.was_online_before_shutdown(client.name):
                        logger.info(
                            "Skipping WOL for %s: was not online before power loss",
                            client.name,
                        )
                        state_tracker.mark_skip(client.name)
                        continue

                    if state_tracker.is_online(client.name):
                        if client.name not in recorded_up_clients:
                            logger.info("%s is online.", client.name)
                            if client.name in recorded_down_clients:
                                notifications.send(
                                    "client_recovered",
                                    "Client is back online",
                                    f"{client.name} ({client.host}) is reachable again.",
                                )
                            recorded_down_clients.discard(client.name)
                            recorded_up_clients.update({client.name})
                        continue

                    else:
                        recorded_down_clients.update({client.name})
                        if state_tracker.should_attempt_wol(
                            client.name, config.wake_on.reattempt_delay
                        ):
                            logger.info(
                                "Sending WOL packet to %s at %s",
                                client.name,
                                client.mac,
                            )
                            if send_wol_packet(client.mac):
                                state_tracker.mark_wol_sent(client.name)
                                notifications.send(
                                    "wake_sent",
                                    "Wake-on-LAN packet sent",
                                    f"Sent a wake packet to {client.name} ({client.mac}).",
                                )
                            elif client.name not in reported_wol_failures:
                                reported_wol_failures.add(client.name)
                                notifications.send(
                                    "errors",
                                    "Wake-on-LAN failed",
                                    (
                                        "Could not send a wake packet to "
                                        f"{client.name} ({client.mac})."
                                    ),
                                )
                        else:
                            logger.debug(
                                "Waiting to retry WOL for %s (delay not reached)",
                                client.name,
                            )

                if len(recorded_down_clients) == 0:
                    logger.info("Power Restored and all clients are back online!")
                    restoration_event = False
                    restoration_event_start = None
                    state_tracker.reset()
                    wol_being_sent = False
                    reported_wol_failures.clear()
                else:
                    if (
                        time.time() - restoration_event_start
                        > config.wake_on.client_timeout_sec
                    ):
                        logger.warning(
                            "Some devices failed to come back online within the timeout period."
                        )
                        for client in recorded_down_clients:
                            logger.warning(
                                "%s failed to come back online within timeout period.",
                                client,
                            )
                        failed_clients = ", ".join(sorted(recorded_down_clients))
                        notifications.send(
                            "errors",
                            "Client recovery timed out",
                            f"These clients did not come back online: {failed_clients}.",
                        )
                        restoration_event = False
                        restoration_event_start = None
                        wol_being_sent = False
                        reported_wol_failures.clear()
                    else:
                        pass

        elif power_status == "OL" and not on_battery and not restoration_event:
            state_tracker.reset()
            state_tracker.set_ups_on_battery(False)
            recorded_down_clients.clear()
            recorded_up_clients.clear()
            reported_wol_failures.clear()

        agent_update_monitor.poll(config, state_tracker, notifications)
        state_tracker.save_state()

        time.sleep(config.poll_interval)


@click.command()
@click.option(
    "--config-file",
    envvar="WOLNUT_CONFIG_FILE",
    help="The configuration filepath to load. Can also be set with WOLNUT_CONFIG_FILE env var.",
)
@click.option(
    "--status-file",
    envvar="WOLNUT_STATUS_FILE",
    help="The status filepath to load. Can also be set with WOLNUT_STATUS_FILE env var.",
)
@click.option("--verbose", is_flag=True, help="Enable verbose logging")
@click.option(
    "--web",
    is_flag=True,
    default=None,
    help="Enable WebUI (overrides WOLNUT_WEB_ENABLED env var)",
)
@click.option(
    "--web-host",
    envvar="WOLNUT_WEB_HOST",
    default="0.0.0.0",
    help="WebUI host",
)
@click.option(
    "--web-port",
    envvar="WOLNUT_WEB_PORT",
    default=8183,
    type=int,
    help="WebUI port",
)
@click.option(
    "--no-web",
    is_flag=True,
    help="Disable WebUI even if enabled via env",
)
def wolnut(
    config_file: str | None,
    status_file: str | None,
    verbose: bool,
    web: bool | None,
    web_host: str,
    web_port: int,
    no_web: bool,
) -> int:
    """A service to send Wake-on-LAN packets to clients after a power outage."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if verbose:
        configure_logger("DEBUG")

    # WebUI always runs by default; --no-web or WOLNUT_WEB_ENABLED=false disables it
    web_enabled = True
    env_flag = os.getenv("WOLNUT_WEB_ENABLED", "true").lower()
    if env_flag in ("0", "false", "no", "off"):
        web_enabled = False
    if env_flag in ("1", "true", "yes", "on"):
        web_enabled = True
    if web is True:
        web_enabled = True
    if no_web:
        web_enabled = False

    if web_enabled:
        try:
            from wolnut.web import start_web_server

            start_web_server(
                host=web_host,
                port=web_port,
                config_file=config_file,
                status_file=status_file,
            )
        except Exception as e:
            logger.warning("Failed to start WebUI: %s", e)

    # If an explicit config file was given (via --config-file or WOLNUT_CONFIG_FILE)
    # but it doesn't exist yet, create a sensible default so the service doesn't crash-loop.
    # The user can then edit it via the WebUI.
    if config_file is not None and not os.path.exists(config_file):
        if web_enabled:
            logger.warning(
                "Config file not found at '%s', creating default config...", config_file
            )
            try:
                _create_default_config(config_file, status_file)
                logger.info(
                    "Default config created at '%s'. Edit it via WebUI at http://%s:%s",
                    config_file,
                    web_host,
                    web_port,
                )
            except Exception as e:
                logger.error(
                    "Failed to create default config at '%s': %s", config_file, e
                )
        # if web is not enabled, let main() report the error normally

    if config_file is None:
        for path in DEFAULT_CONFIG_FILEPATHS:
            if os.path.exists(path):
                config_file = path
                break
        # If web is enabled, don't abort on missing config — create default and keep running
        if config_file is None:
            if web_enabled:
                # Use the primary default path
                default_path = DEFAULT_CONFIG_FILEPATHS[0]
                logger.warning(
                    "No config file found. Creating default at '%s' and running WebUI at http://%s:%s",
                    default_path,
                    web_host,
                    web_port,
                )
                try:
                    _create_default_config(default_path, status_file)
                    config_file = default_path
                except Exception as e:
                    logger.error("Failed to create default config: %s", e)
                    # Still keep web alive so user can fix via UI
                    try:
                        import time as _time

                        while True:
                            _time.sleep(3600)
                    except KeyboardInterrupt:
                        return 0
            else:
                click.echo(
                    "No config file found. Checked default paths and WOLNUT_CONFIG_FILE env var."
                )
                raise click.Abort()

    exit_code = main(config_file, status_file, verbose)
    if exit_code != 0:
        # main() will log the specific error, so we just abort.
        # If web is enabled, don't crash-loop — keep WebUI alive for repair
        if web_enabled:
            logger.warning(
                "Wolnut monitor failed to start (exit %s). WebUI remains available at http://%s:%s for configuration.",
                exit_code,
                web_host,
                web_port,
            )
            try:
                import time as _time

                while True:
                    _time.sleep(3600)
            except KeyboardInterrupt:
                return exit_code
        raise click.Abort()


def _create_default_config(config_path: str, status_file: str | None = None) -> None:
    """Create a minimal default config.yaml so the service can start and be configured via WebUI."""
    import yaml
    from pathlib import Path
    from wolnut.state import DEFAULT_STATE_FILEPATH

    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        return
    status_path = (
        status_file or os.getenv("WOLNUT_STATUS_FILE") or DEFAULT_STATE_FILEPATH
    )
    default_cfg = {
        "log_level": "INFO",
        "nut": {"ups": "ups@localhost"},
        "poll_interval": 15,
        "status_file": status_path,
        "wake_on": {
            "restore_delay_sec": 30,
            "min_battery_percent": 25,
            "client_timeout_sec": 600,
            "reattempt_delay": 30,
        },
        "clients": [],
        "webui": {"suppress_mac_warnings": False},
        "notifications": {
            "discord": {"enabled": False, "webhook_url": ""},
            "gotify": {"enabled": False, "url": "", "token": "", "priority": 5},
            "ntfy": {
                "enabled": False,
                "url": "https://ntfy.sh",
                "topic": "",
                "token": "",
                "priority": 3,
            },
            "events": {
                "power_loss": True,
                "power_restored": True,
                "wake_sent": True,
                "client_recovered": True,
                "errors": True,
                "shutdown_acknowledged": True,
                "shutdown_failed": True,
                "agent_update_succeeded": False,
            },
        },
    }
    with open(p, "w") as f:
        yaml.safe_dump(default_cfg, f, sort_keys=False)
    logger.info("Wrote default config to %s", p)


@click.command(name="web")
@click.option(
    "--config-file",
    envvar="WOLNUT_CONFIG_FILE",
    help="The configuration filepath to load.",
)
@click.option(
    "--status-file",
    envvar="WOLNUT_STATUS_FILE",
    help="The status filepath to load.",
)
@click.option(
    "--host", envvar="WOLNUT_WEB_HOST", default="0.0.0.0", help="Host to bind"
)
@click.option(
    "--port", envvar="WOLNUT_WEB_PORT", default=8183, type=int, help="Port to bind"
)
def web_only(config_file: str | None, status_file: str | None, host: str, port: int):
    """Run only the WebUI (no monitoring loop). Useful for config editing."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        import uvicorn
        from wolnut.web import create_app
    except ImportError:
        click.echo("FastAPI/uvicorn not installed")
        raise click.Abort()
    app = create_app(config_file=config_file, status_file=status_file)
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("WOLNUT_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
