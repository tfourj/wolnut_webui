from types import SimpleNamespace

from wolnut.agent_updates import AgentUpdateMonitor
from wolnut.config import ClientConfig, ShutdownConfig
from wolnut.state import ClientStateTracker


def test_successful_automatic_agent_update_notifies_once(tmp_path, mocker):
    client = ClientConfig(
        "server",
        "server.local",
        shutdown=ShutdownConfig(
            agent_id="agent-123",
            agent_port=8184,
            auto_update=True,
        ),
    )
    config = SimpleNamespace(
        clients=[client],
        notifications=SimpleNamespace(
            events=SimpleNamespace(agent_update_succeeded=True)
        ),
    )
    tracker = ClientStateTracker([client], str(tmp_path / "state.json"))
    notifications = mocker.Mock()
    status = mocker.patch("wolnut.agent_updates.AgentClient").return_value.status
    status.side_effect = [
        {
            "update_installed_at": 0,
            "update_source": "",
            "update_status": "",
            "version": "1.0.0",
        },
        {
            "update_installed_at": 100,
            "update_source": "automatic",
            "update_status": "updated",
            "version": "1.1.0",
        },
        {
            "update_installed_at": 100,
            "update_source": "automatic",
            "update_status": "updated",
            "version": "1.1.0",
        },
        {
            "update_installed_at": 200,
            "update_source": "manual",
            "update_status": "updated",
            "version": "1.2.0",
        },
    ]
    monitor = AgentUpdateMonitor(check_interval=60)

    for now in (60, 120, 180, 240):
        monitor.poll(config, tracker, notifications, now=now)

    notifications.send.assert_called_once_with(
        "agent_update_succeeded",
        "Agent update installed",
        "server (server.local) updated to Wolnut agent 1.1.0.",
    )


def test_disabled_agent_update_event_does_not_poll(tmp_path, mocker):
    client = ClientConfig(
        "server",
        "server.local",
        shutdown=ShutdownConfig(agent_id="agent-123", auto_update=True),
    )
    config = SimpleNamespace(
        clients=[client],
        notifications=SimpleNamespace(
            events=SimpleNamespace(agent_update_succeeded=False)
        ),
    )
    tracker = ClientStateTracker([client], str(tmp_path / "state.json"))
    agent = mocker.patch("wolnut.agent_updates.AgentClient")

    AgentUpdateMonitor().poll(config, tracker, mocker.Mock(), now=60)

    agent.assert_not_called()


def test_agent_update_observation_survives_restart(tmp_path):
    client = ClientConfig("server", "server.local")
    state_path = tmp_path / "state.json"
    tracker = ClientStateTracker([client], str(state_path))

    assert not tracker.observe_agent_update("server", 0, "1.0.0", "", "")
    assert tracker.observe_agent_update("server", 100, "1.1.0", "automatic", "updated")
    tracker.save_state()

    restored = ClientStateTracker([client], str(state_path))
    assert not restored.observe_agent_update(
        "server", 100, "1.1.0", "automatic", "updated"
    )
