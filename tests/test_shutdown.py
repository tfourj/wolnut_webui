from types import SimpleNamespace

import pytest

from wolnut.agent_client import AgentError
from wolnut.config import ClientConfig, NutConfig, ShutdownConfig
from wolnut.shutdown import ShutdownCoordinator, UpsSnapshot, parse_ups_snapshot
from wolnut.state import ClientStateTracker


@pytest.mark.parametrize(
    "status, expected",
    [
        ({"ups.status": "OB LB", "battery.charge": "19.6"}, UpsSnapshot("OB", 20)),
        ({"ups.status": "OL", "battery.charge": 100}, UpsSnapshot("OL", 100)),
        ({"ups.status": "UNKNOWN", "battery.charge": 50}, None),
        ({"ups.status": "OB"}, None),
        ({"ups.status": "OB", "battery.charge": "bad"}, None),
        ({"ups.status": "OB", "battery.charge": 101}, None),
    ],
)
def test_parse_ups_snapshot_fails_closed(status, expected):
    assert parse_ups_snapshot(status) == expected


def _config(clients):
    return SimpleNamespace(
        clients=clients,
        poll_interval=5,
        nut=NutConfig(ups="ups@localhost"),
    )


def test_shutdown_triggers_only_at_each_client_threshold(tmp_path, mocker):
    early = ClientConfig(
        "early",
        "early.local",
        shutdown=ShutdownConfig(True, 40, "agent-early", 8184),
    )
    late = ClientConfig(
        "late",
        "late.local",
        shutdown=ShutdownConfig(True, 20, "agent-late", 8184),
    )
    tracker = ClientStateTracker([early, late], str(tmp_path / "state.json"))
    tracker.begin_outage(35)
    agent = mocker.patch("wolnut.shutdown.AgentClient").return_value
    agent.shutdown.return_value = {"status": "accepted"}
    notifications = mocker.Mock()

    ShutdownCoordinator().process_on_battery(
        _config([early, late]), tracker, notifications, 35
    )

    agent.shutdown.assert_called_once()
    assert agent.shutdown.call_args.args[0] == "agent-early"
    assert tracker.shutdown_state("early")["acknowledged"] is True
    assert tracker.shutdown_state("late")["command_id"] is None


def test_failed_shutdown_retries_with_same_id_and_notifies_once(tmp_path, mocker):
    client = ClientConfig(
        "server",
        "server.local",
        shutdown=ShutdownConfig(True, 20, "agent-server", 8184),
    )
    tracker = ClientStateTracker([client], str(tmp_path / "state.json"))
    tracker.begin_outage(20)
    clock = mocker.patch("wolnut.state.time.time", return_value=1000)
    agent = mocker.patch("wolnut.shutdown.AgentClient").return_value
    agent.shutdown.side_effect = [AgentError("offline"), {"status": "accepted"}]
    notifications = mocker.Mock()
    coordinator = ShutdownCoordinator()

    coordinator.process_on_battery(_config([client]), tracker, notifications, 20)
    first_id = tracker.shutdown_state("server")["command_id"]
    clock.return_value = 1005
    coordinator.process_on_battery(_config([client]), tracker, notifications, 19)

    assert agent.shutdown.call_count == 2
    assert agent.shutdown.call_args_list[0].args[1] == first_id
    assert agent.shutdown.call_args_list[1].args[1] == first_id
    assert notifications.send.call_args_list[0].args[0] == "shutdown_failed"
    assert notifications.send.call_args_list[1].args[0] == "shutdown_acknowledged"
    assert tracker.shutdown_state("server")["acknowledged"] is True


def test_restarted_client_receives_new_shutdown_during_same_outage(tmp_path, mocker):
    client = ClientConfig(
        "server",
        "server.local",
        shutdown=ShutdownConfig(True, 20, "agent-server", 8184),
    )
    tracker = ClientStateTracker([client], str(tmp_path / "state.json"))
    tracker.update("server", True)
    tracker.begin_outage(20)
    agent = mocker.patch("wolnut.shutdown.AgentClient").return_value
    agent.shutdown.return_value = {"status": "accepted"}
    coordinator = ShutdownCoordinator()

    coordinator.process_on_battery(_config([client]), tracker, mocker.Mock(), 20)
    first_id = agent.shutdown.call_args.args[1]
    coordinator.process_on_battery(_config([client]), tracker, mocker.Mock(), 19)
    agent.shutdown.assert_called_once()

    tracker.update("server", False)
    tracker.update("server", True)
    coordinator.process_on_battery(_config([client]), tracker, mocker.Mock(), 18)

    assert agent.shutdown.call_count == 2
    assert agent.shutdown.call_args.args[1] != first_id
    assert tracker.shutdown_state("server")["acknowledged"] is True


def test_disabled_and_unpaired_clients_never_shutdown(tmp_path, mocker):
    clients = [
        ClientConfig(
            "disabled",
            "disabled.local",
            enabled=False,
            shutdown=ShutdownConfig(True, 50, "agent-disabled", 8184),
        ),
        ClientConfig(
            "unpaired",
            "unpaired.local",
            shutdown=ShutdownConfig(False, 50, None, 8184),
        ),
    ]
    tracker = ClientStateTracker(clients, str(tmp_path / "state.json"))
    tracker.begin_outage(10)
    agent = mocker.patch("wolnut.shutdown.AgentClient")

    ShutdownCoordinator().process_on_battery(
        _config(clients), tracker, mocker.Mock(), 10
    )

    agent.assert_not_called()
