import yaml
from fastapi.testclient import TestClient

from wolnut.web import create_app


def write_config(path, **overrides):
    config = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "192.168.1.10",
                "mac": "DE:AD:BE:EF:00:01",
            }
        ],
        "notifications": {
            "discord": {
                "enabled": True,
                "webhook_url": "https://discord.example/webhook",
            }
        },
    }
    config.update(overrides)
    path.write_text(yaml.safe_dump(config))


def test_manual_client_wake_sends_notification(tmp_path, mocker):
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    send_wol = mocker.patch("wolnut.web.send_wol_packet", return_value=True)
    notify = mocker.patch("wolnut.notifications.send_discord")
    client = TestClient(create_app(config_file=str(config_path)))

    response = client.post("/api/wol/client/server")

    assert response.status_code == 200
    send_wol.assert_called_once_with("DE:AD:BE:EF:00:01")
    notify.assert_called_once_with(
        "https://discord.example/webhook",
        "Wake-on-LAN packet sent",
        "Sent a wake packet to server (DE:AD:BE:EF:00:01).",
    )


def test_manual_client_wake_failure_sends_error_notification(tmp_path, mocker):
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    mocker.patch("wolnut.web.send_wol_packet", return_value=False)
    notify = mocker.patch("wolnut.notifications.send_discord")
    client = TestClient(create_app(config_file=str(config_path)))

    response = client.post("/api/wol/client/server")

    assert response.status_code == 500
    notify.assert_called_once_with(
        "https://discord.example/webhook",
        "Wake-on-LAN failed",
        "Could not send a wake packet to server (DE:AD:BE:EF:00:01).",
    )


def test_notification_test_uses_submitted_provider_config(tmp_path, mocker):
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    notify = mocker.patch("wolnut.notifications.send_gotify")
    client = TestClient(create_app(config_file=str(config_path)))

    response = client.post(
        "/api/notifications/test",
        json={
            "provider": "gotify",
            "notifications": {
                "gotify": {
                    "enabled": True,
                    "url": "https://gotify.test",
                    "token": "test-token",
                    "priority": 9,
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "sent", "provider": "gotify"}
    notify.assert_called_once_with(
        "https://gotify.test",
        "test-token",
        "Wolnut test notification",
        "Your notification provider is configured correctly.",
        9,
    )
