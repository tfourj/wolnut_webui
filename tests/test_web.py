import shlex

import yaml
from fastapi.testclient import TestClient

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

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


def test_ntfy_notification_test_uses_submitted_provider_config(tmp_path, mocker):
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    notify = mocker.patch("wolnut.notifications.send_ntfy")
    client = TestClient(create_app(config_file=str(config_path)))

    response = client.post(
        "/api/notifications/test",
        json={
            "provider": "ntfy",
            "notifications": {
                "ntfy": {
                    "enabled": True,
                    "url": "https://ntfy.test",
                    "topic": "wolnut-alerts",
                    "token": "tk_test",
                    "priority": 4,
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "sent", "provider": "ntfy"}
    notify.assert_called_once_with(
        "https://ntfy.test",
        "wolnut-alerts",
        "tk_test",
        "Wolnut test notification",
        "Your notification provider is configured correctly.",
        4,
    )


def _secure_app_client(tmp_path, monkeypatch, config_data):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config_data))
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct horse battery staple")
    monkeypatch.setenv("WOLNUT_JWT_SECRET", "a" * 32)
    monkeypatch.setenv("WOLNUT_SECURITY_DIR", str(tmp_path / "security"))
    client = TestClient(
        create_app(
            config_file=str(config_path), status_file=str(tmp_path / "state.json")
        ),
        base_url="https://wolnut.test",
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    token = login.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client, config_path


def _agent_csr(agent_id):
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wolnut-agent")])
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:wolnut:agent:{agent_id}")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def test_pair_agent_requires_https(tmp_path, monkeypatch):
    config = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "server.local",
                "mac": "00:11:22:33:44:55",
            }
        ],
    }
    secure_client, config_path = _secure_app_client(tmp_path, monkeypatch, config)
    token = secure_client.headers["Authorization"]
    http_client = TestClient(
        create_app(config_file=str(config_path)), base_url="http://wolnut.test"
    )
    http_client.headers["Authorization"] = token

    response = http_client.post(
        "/api/agents/pair",
        json={
            "client_name": "server",
            "agent_port": 8184,
            "pairing_code": "ABCDEFGHIJKLMNOP",
            "fingerprint": "AA" * 32,
        },
    )

    assert response.status_code == 426


def test_pair_agent_persists_server_owned_identity(tmp_path, monkeypatch, mocker):
    config = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "server.local",
                "mac": "00:11:22:33:44:55",
            }
        ],
    }
    client, config_path = _secure_app_client(tmp_path, monkeypatch, config)
    agent = mocker.patch("wolnut.web.AgentClient").return_value
    agent.pair.return_value = {
        "agent_id": "agent-123",
        "hostname": "server",
        "version": "1.0.0",
    }

    response = client.post(
        "/api/agents/pair",
        json={
            "client_name": "server",
            "agent_port": 9191,
            "pairing_code": "ABCDEFGHIJKLMNOP",
            "fingerprint": "AA" * 32,
        },
    )

    assert response.status_code == 200
    saved = yaml.safe_load(config_path.read_text())
    assert saved["clients"][0]["shutdown"] == {
        "enabled": False,
        "battery_percent": 20,
        "agent_id": "agent-123",
        "agent_port": 9191,
    }


def test_one_line_enrollment_installs_and_pairs_agent(tmp_path, monkeypatch):
    config = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "server.local",
                "mac": "00:11:22:33:44:55",
            }
        ],
    }
    monkeypatch.setenv("WOLNUT_PUBLIC_URL", "https://wolnut.example")
    client, config_path = _secure_app_client(tmp_path, monkeypatch, config)

    created = client.post(
        "/api/agents/enrollments",
        json={"client_name": "server", "agent_port": 9191},
    )

    assert created.status_code == 200
    payload = created.json()
    command = payload["install_command"]
    assert "sha256sum -c" in command
    assert "curl |" not in command
    assert "--enroll-url https://wolnut.example/api/agents/enroll" in command
    arguments = shlex.split(command)
    token = arguments[arguments.index("--enrollment-token") + 1]

    agent_id = "a" * 32
    enrolled = client.post(
        "/api/agents/enroll",
        json={
            "token": token,
            "agent_id": agent_id,
            "csr": _agent_csr(agent_id),
            "hostname": "server",
            "version": "1.2.3",
        },
    )

    assert enrolled.status_code == 200
    assert enrolled.json()["agent_id"] == agent_id
    assert "BEGIN CERTIFICATE" in enrolled.json()["server_cert"]
    saved = yaml.safe_load(config_path.read_text())
    assert saved["clients"][0]["shutdown"]["agent_id"] == agent_id
    assert saved["clients"][0]["shutdown"]["agent_port"] == 9191
    status = client.get(f"/api/agents/enrollments/{payload['enrollment_id']}").json()
    assert status["status"] == "paired"
    assert "token_hash" not in status


def test_one_line_enrollment_requires_https(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("WOLNUT_JWT_SECRET", "a" * 32)
    client = TestClient(
        create_app(config_file=str(config_path)), base_url="http://wolnut.test"
    )
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "password"}
    )
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    response = client.post(
        "/api/agents/enrollments",
        json={"client_name": "server", "agent_port": 8184},
    )

    assert response.status_code == 426


def test_invalid_enrollment_token_cannot_change_pairing(tmp_path, monkeypatch):
    config = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "server.local",
                "mac": "00:11:22:33:44:55",
            }
        ],
    }
    client, config_path = _secure_app_client(tmp_path, monkeypatch, config)
    agent_id = "b" * 32

    response = client.post(
        "/api/agents/enroll",
        json={
            "token": "invalid-token-value-that-is-long-enough",
            "agent_id": agent_id,
            "csr": _agent_csr(agent_id),
        },
    )

    assert response.status_code == 403
    saved = yaml.safe_load(config_path.read_text())
    assert "shutdown" not in saved["clients"][0]


def test_manual_shutdown_requires_exact_device_name(tmp_path, monkeypatch, mocker):
    config = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "server.local",
                "mac": "00:11:22:33:44:55",
                "shutdown": {
                    "enabled": True,
                    "battery_percent": 20,
                    "agent_id": "agent-123",
                    "agent_port": 8184,
                },
            }
        ],
    }
    client, _ = _secure_app_client(tmp_path, monkeypatch, config)
    agent = mocker.patch("wolnut.web.AgentClient").return_value
    agent.shutdown.return_value = {"status": "accepted"}

    rejected = client.post(
        "/api/agents/server/shutdown", json={"confirmation": "wrong"}
    )
    accepted = client.post(
        "/api/agents/server/shutdown", json={"confirmation": "server"}
    )

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    agent.shutdown.assert_called_once()


def test_shutdown_setting_changes_require_secure_admin(tmp_path):
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    client = TestClient(
        create_app(config_file=str(config_path)), base_url="http://wolnut.test"
    )
    submitted = yaml.safe_load(config_path.read_text())
    submitted["clients"][0]["shutdown"] = {
        "enabled": False,
        "battery_percent": 30,
        "agent_id": None,
        "agent_port": 8184,
    }

    response = client.put("/api/config", json=submitted)

    assert response.status_code == 503
    saved = yaml.safe_load(config_path.read_text())
    assert "shutdown" not in saved["clients"][0]


def test_stale_config_save_cannot_overwrite_pairing(tmp_path, monkeypatch):
    initial = {
        "nut": {"ups": "ups@localhost"},
        "clients": [
            {
                "name": "server",
                "host": "server.local",
                "mac": "00:11:22:33:44:55",
            }
        ],
    }
    client, config_path = _secure_app_client(tmp_path, monkeypatch, initial)
    stale = client.get("/api/config").json()
    paired = yaml.safe_load(config_path.read_text())
    paired["clients"][0]["shutdown"] = {
        "enabled": False,
        "battery_percent": 20,
        "agent_id": "agent-new",
        "agent_port": 8184,
    }
    config_path.write_text(yaml.safe_dump(paired))

    response = client.put("/api/config", json=stale)

    assert response.status_code == 409
    saved = yaml.safe_load(config_path.read_text())
    assert saved["clients"][0]["shutdown"]["agent_id"] == "agent-new"
