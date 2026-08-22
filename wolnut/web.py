import hashlib
import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

import yaml

logger = logging.getLogger("wolnut")

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, Response
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as e:
    # Allow import without fastapi for tests that don't need web
    FastAPI = None  # type: ignore

from wolnut.config import (
    DEFAULT_CONFIG_FILEPATHS,
    notifications_config_from_dict,
    validate_config,
)
from wolnut.agent_client import AgentClient, AgentError, SecurityStore
from wolnut.config_store import ConfigStore
from wolnut.enrollment import EnrollmentError, EnrollmentStore
from wolnut.monitor import get_ups_status, get_ups_status_detailed, is_client_online
from wolnut.notifications import NotificationService
from wolnut.state import DEFAULT_STATE_FILEPATH
from wolnut.utils import resolve_mac_from_host, validate_mac_format
from wolnut.wol import send_wol_packet

# ---------------------------------------------------------------------------
# Config path helpers
# ---------------------------------------------------------------------------


def resolve_config_path(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.getenv("WOLNUT_CONFIG_FILE")
    if env:
        return env
    for p in DEFAULT_CONFIG_FILEPATHS:
        if Path(p).exists():
            return p
    # default writable location (docker)
    return DEFAULT_CONFIG_FILEPATHS[0]


def resolve_status_path(explicit: str | None = None) -> str:
    env = os.getenv("WOLNUT_STATUS_FILE")
    if explicit:
        return explicit
    if env:
        return env
    return DEFAULT_STATE_FILEPATH


def load_raw_config(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r") as f:
        return yaml.safe_load(f) or {}


def save_raw_config(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


# ---------------------------------------------------------------------------
# Pydantic models (for validation / OpenAPI)
# ---------------------------------------------------------------------------


class NutModel(BaseModel):
    ups: str = Field(..., description="Format: <ups-name>@<host>")
    username: Optional[str] = None
    password: Optional[str] = None
    port: int = 3493
    timeout: int = 5


class WakeOnModel(BaseModel):
    restore_delay_sec: int = 30
    min_battery_percent: int = 25
    client_timeout_sec: int = 600
    reattempt_delay: int = 30


class ShutdownModel(BaseModel):
    enabled: bool = False
    battery_percent: int = Field(default=20, ge=1, le=100)
    agent_id: Optional[str] = None
    agent_port: int = Field(default=8184, ge=1, le=65535)
    auto_update: bool = False


class ClientModel(BaseModel):
    name: str
    host: str
    mac: str = ""
    always_wake: bool = False
    enabled: bool = True
    wake_enabled: bool = True
    shutdown: ShutdownModel = Field(default_factory=ShutdownModel)


class WebUIConfigModel(BaseModel):
    suppress_mac_warnings: bool = False


class DiscordNotificationModel(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class GotifyNotificationModel(BaseModel):
    enabled: bool = False
    url: str = ""
    token: str = ""
    priority: int = Field(default=5, ge=0, le=10)


class NtfyNotificationModel(BaseModel):
    enabled: bool = False
    url: str = "https://ntfy.sh"
    topic: str = ""
    token: str = ""
    priority: int = Field(default=3, ge=1, le=5)


class NotificationEventsModel(BaseModel):
    power_loss: bool = True
    power_restored: bool = True
    wake_sent: bool = True
    client_recovered: bool = True
    errors: bool = True
    shutdown_acknowledged: bool = True
    shutdown_failed: bool = True


class NotificationsConfigModel(BaseModel):
    discord: DiscordNotificationModel = Field(default_factory=DiscordNotificationModel)
    gotify: GotifyNotificationModel = Field(default_factory=GotifyNotificationModel)
    ntfy: NtfyNotificationModel = Field(default_factory=NtfyNotificationModel)
    events: NotificationEventsModel = Field(default_factory=NotificationEventsModel)


class ConfigModel(BaseModel):
    log_level: str = "INFO"
    poll_interval: int = 15
    status_file: str = DEFAULT_STATE_FILEPATH
    nut: NutModel
    wake_on: WakeOnModel = Field(default_factory=WakeOnModel)
    clients: list[ClientModel] = Field(default_factory=list)
    webui: WebUIConfigModel = Field(default_factory=WebUIConfigModel)
    notifications: NotificationsConfigModel = Field(
        default_factory=NotificationsConfigModel
    )


class WolRequest(BaseModel):
    mac: str
    broadcast_ip: str = "255.255.255.255"


class NotificationTestRequest(BaseModel):
    provider: Literal["discord", "gotify", "ntfy"]
    notifications: NotificationsConfigModel


class AgentPairRequest(BaseModel):
    client_name: str
    agent_port: int = Field(default=8184, ge=1, le=65535)
    pairing_code: str = Field(..., min_length=10, max_length=128)
    fingerprint: str = Field(..., min_length=64, max_length=128)


class AgentShutdownRequest(BaseModel):
    confirmation: str


class AgentUnpairRequest(BaseModel):
    confirmation: str
    force_local: bool = False


class AgentAutoUpdateRequest(BaseModel):
    enabled: bool


class AgentEnrollmentRequest(BaseModel):
    client_name: str
    agent_port: int = Field(default=8184, ge=1, le=65535)


class AgentEnrollmentCompleteRequest(BaseModel):
    token: str = Field(..., min_length=32, max_length=128)
    agent_id: str = Field(..., pattern=r"^[0-9a-f]{32}$")
    csr: str = Field(..., min_length=128, max_length=16384)
    hostname: str = Field(default="", max_length=255)
    version: str = Field(default="", max_length=64)


def _agent_download_base() -> str:
    download_base = os.getenv(
        "WOLNUT_AGENT_DOWNLOAD_BASE_URL",
        "https://github.com/tfourj/wolnut_webui/releases/latest/download",
    ).rstrip("/")
    parsed_download = urlsplit(download_base)
    if (
        parsed_download.scheme != "https"
        or not parsed_download.netloc
        or parsed_download.username
        or parsed_download.query
        or parsed_download.fragment
    ):
        raise ValueError("WOLNUT_AGENT_DOWNLOAD_BASE_URL must use HTTPS")
    return download_base


def _agent_script_command(script_name: str, arguments: list[str]) -> str:
    download_base = _agent_download_base()
    quoted_base = shlex.quote(download_base)
    quoted_script = shlex.quote(script_name)
    quoted_arguments = " ".join(shlex.quote(value) for value in arguments)
    return (
        'TMP="$(mktemp -d)" && trap \'rm -rf "$TMP"\' EXIT && '
        f"BASE={quoted_base} && SCRIPT={quoted_script} && "
        'curl --proto "=https" --proto-redir "=https" --tlsv1.2 -fsSL '
        '"$BASE/$SCRIPT" -o "$TMP/$SCRIPT" && '
        'curl --proto "=https" --proto-redir "=https" --tlsv1.2 -fsSL '
        '"$BASE/$SCRIPT.sha256" -o "$TMP/$SCRIPT.sha256" && '
        '(cd "$TMP" && sha256sum -c "$SCRIPT.sha256") && '
        f'/bin/sh "$TMP/$SCRIPT" {quoted_arguments}'
    ).rstrip()


def _agent_pipe_command(
    public_url: str, token: str = "", agent_port: int = 8184
) -> str:
    endpoint = public_url.rstrip("/") + "/api/agents/install.sh"
    if not token and agent_port != 8184:
        endpoint += f"?agent_port={agent_port}"
    arguments = [
        "curl",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--tlsv1.2",
        "-fsSL",
    ]
    if token:
        arguments.extend(["-H", f"Authorization: Bearer {token}"])
    arguments.append(endpoint)
    return shlex.join(arguments) + " | /bin/sh"


def _agent_install_template() -> str:
    packaged = resources.files("wolnut").joinpath("assets/install.sh")
    try:
        return packaged.read_text(encoding="utf-8")
    except FileNotFoundError:
        development = Path(__file__).resolve().parent.parent / "agent" / "install.sh"
        return development.read_text(encoding="utf-8")


def build_agent_install_script(
    *,
    download_base: str,
    agent_port: int,
    enrollment_url: str = "",
    enrollment_token: str = "",
) -> str:
    if not 1 <= agent_port <= 65535:
        raise ValueError("Agent port must be between 1 and 65535")
    values = {
        'download_base="https://github.com/tfourj/wolnut_webui/releases/latest/download"': (
            "download_base=" + shlex.quote(download_base)
        ),
        'listen_address="0.0.0.0:8184"': (
            "listen_address=" + shlex.quote(f"0.0.0.0:{agent_port}")
        ),
        'enrollment_url=""': "enrollment_url=" + shlex.quote(enrollment_url),
        'enrollment_token=""': ("enrollment_token=" + shlex.quote(enrollment_token)),
    }
    script = _agent_install_template()
    for original, replacement in values.items():
        if script.count(original) != 1:
            raise ValueError("Packaged agent installer template is invalid")
        script = script.replace(original, replacement, 1)
    return script


def build_agent_install_command(public_url: str, token: str, agent_port: int) -> str:
    _agent_download_base()
    return _agent_pipe_command(public_url, token, agent_port)


def build_agent_manual_commands(public_url: str, agent_port: int) -> dict[str, str]:
    _agent_download_base()
    return {
        "install_command": _agent_pipe_command(public_url, agent_port=agent_port),
        "pairing_command": (
            'if [ "$(id -u)" -eq 0 ]; then wolnut-agent pairing-code; '
            "elif command -v sudo >/dev/null 2>&1; then sudo wolnut-agent pairing-code; "
            "else echo 'Log in as root and run: wolnut-agent pairing-code'; fi"
        ),
        "uninstall_command": _agent_script_command("uninstall.sh", []),
    }


# ---------------------------------------------------------------------------
# Auth helpers (JWT bearer)
# ---------------------------------------------------------------------------


def _get_auth_config():
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    enabled = bool(username and password)
    # JWT secret: explicit env or fallback to password hash; if no password, use random ephemeral
    secret = (
        os.getenv("WOLNUT_JWT_SECRET")
        or os.getenv("JWT_SECRET")
        or (password if password else "dev-secret-change-me")
    )
    return enabled, username, password, secret


def _create_access_token(username: str, secret: str, expires_hours: int = 24) -> str:
    try:
        import jwt
        from datetime import datetime, timedelta, timezone

        payload = {
            "sub": username,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=expires_hours),
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    except ImportError:
        # fallback: very simple unsigned token (not for production, but avoids hard crash if PyJWT missing)
        import base64
        import json as _json
        import time

        payload = {"sub": username, "exp": int(time.time()) + expires_hours * 3600}
        return base64.urlsafe_b64encode(_json.dumps(payload).encode()).decode()


def _verify_token(token: str, secret: str) -> str:
    try:
        import jwt

        payload = jwt.decode(token, secret, algorithms=["HS256"])
        sub = payload.get("sub")
        if not sub:
            raise ValueError("Missing sub")
        return sub
    except ImportError:
        import base64
        import json as _json
        import time

        try:
            # try base64 fallback
            padded = token + "=" * (-len(token) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(padded).decode())
            if payload.get("exp") and payload["exp"] < time.time():
                raise ValueError("Token expired")
            if not payload.get("sub"):
                raise ValueError("Missing sub")
            return payload["sub"]
        except Exception as e:
            raise ValueError(str(e))
    except Exception as e:
        raise ValueError(str(e))


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_app(config_file: str | None = None, status_file: str | None = None) -> Any:
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI not installed. Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(title="Wolnut WebUI", version="1.0.0")

    cors_origins = [
        value.strip()
        for value in os.getenv("WOLNUT_CORS_ORIGINS", "").split(",")
        if value.strip()
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type"],
        )

    cfg_path = resolve_config_path(config_file)
    st_path = resolve_status_path(status_file)
    config_store = ConfigStore(cfg_path)
    security_store = SecurityStore()
    enrollment_store = EnrollmentStore(security_store.directory)
    state_lock = threading.RLock()

    # --- auth setup ---
    auth_enabled, admin_user, admin_pass, jwt_secret = _get_auth_config()
    explicit_jwt_secret = os.getenv("WOLNUT_JWT_SECRET", "")
    shutdown_admin_configured = auth_enabled and len(explicit_jwt_secret) >= 32
    security = HTTPBearer(auto_error=False)

    def require_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ):
        if not auth_enabled:
            return admin_user or "anonymous"
        if credentials is None or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Not authenticated")
        try:
            user = _verify_token(credentials.credentials, jwt_secret)
            if user != admin_user:
                raise HTTPException(status_code=401, detail="Invalid token user")
            return user
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    def _is_secure_request(request: Request) -> bool:
        return request.url.scheme == "https"

    def _require_secure_admin(request: Request) -> None:
        if not shutdown_admin_configured:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Secure shutdown administration requires ADMIN_USERNAME, "
                    "ADMIN_PASSWORD, and WOLNUT_JWT_SECRET with at least 32 characters"
                ),
            )
        if not _is_secure_request(request):
            raise HTTPException(
                status_code=426,
                detail="Secure shutdown administration requires HTTPS",
            )

    def _public_url(request: Request) -> str:
        value = os.getenv("WOLNUT_PUBLIC_URL", "").strip() or str(
            request.base_url
        ).rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(
                status_code=503,
                detail="WOLNUT_PUBLIC_URL must be a valid HTTPS URL",
            )
        return value.rstrip("/")

    # --- helpers inside closure ---
    def _read_config_or_default() -> dict:
        raw = config_store.read()
        if raw is None:
            # return sensible defaults for fresh install
            return {
                "log_level": "INFO",
                "poll_interval": 15,
                "status_file": st_path,
                "nut": {"ups": "ups@localhost", "username": "", "password": ""},
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
                    "gotify": {
                        "enabled": False,
                        "url": "",
                        "token": "",
                        "priority": 5,
                    },
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
                    },
                },
            }
        # normalize defaults if missing
        raw.setdefault("log_level", "INFO")
        raw.setdefault("poll_interval", 15)
        raw.setdefault("status_file", st_path)
        raw.setdefault("wake_on", {})
        for k, v in {
            "restore_delay_sec": 30,
            "min_battery_percent": 25,
            "client_timeout_sec": 600,
            "reattempt_delay": 30,
        }.items():
            raw["wake_on"].setdefault(k, v)
        raw.setdefault("webui", {})
        # legacy: migrate top-level suppress_mac_warnings into webui
        if (
            "suppress_mac_warnings" in raw
            and "suppress_mac_warnings" not in raw["webui"]
        ):
            raw["webui"]["suppress_mac_warnings"] = raw.pop("suppress_mac_warnings")
        raw["webui"].setdefault("suppress_mac_warnings", False)
        raw.setdefault("notifications", {})
        raw["notifications"].setdefault("discord", {})
        raw["notifications"]["discord"].setdefault("enabled", False)
        raw["notifications"]["discord"].setdefault("webhook_url", "")
        raw["notifications"].setdefault("gotify", {})
        for key, value in {
            "enabled": False,
            "url": "",
            "token": "",
            "priority": 5,
        }.items():
            raw["notifications"]["gotify"].setdefault(key, value)
        raw["notifications"].setdefault("ntfy", {})
        for key, value in {
            "enabled": False,
            "url": "https://ntfy.sh",
            "topic": "",
            "token": "",
            "priority": 3,
        }.items():
            raw["notifications"]["ntfy"].setdefault(key, value)
        raw["notifications"].setdefault("events", {})
        for key in (
            "power_loss",
            "power_restored",
            "wake_sent",
            "client_recovered",
            "errors",
            "shutdown_acknowledged",
            "shutdown_failed",
        ):
            raw["notifications"]["events"].setdefault(key, True)
        for c in raw.get("clients", []) or []:
            c.setdefault("always_wake", False)
            c.setdefault("enabled", True)
            c.setdefault("wake_enabled", True)
            c.setdefault("mac", "")
            c.setdefault("shutdown", {})
            for key, value in {
                "enabled": False,
                "battery_percent": 20,
                "agent_id": None,
                "agent_port": 8184,
                "auto_update": False,
            }.items():
                c["shutdown"].setdefault(key, value)
        return raw

    def _find_client(raw: dict, client_name: str) -> dict:
        matches = [
            client
            for client in raw.get("clients", [])
            if client.get("name") == client_name
        ]
        if not matches:
            raise HTTPException(
                status_code=404, detail=f"Client {client_name} not found"
            )
        if len(matches) > 1:
            raise HTTPException(
                status_code=409, detail=f"Client name {client_name} is ambiguous"
            )
        return matches[0]

    def _agent_client(client: dict) -> AgentClient:
        shutdown = client.get("shutdown", {}) or {}
        return AgentClient(
            client["host"],
            int(shutdown.get("agent_port", 8184)),
            security_store=security_store,
        )

    def _record_agent_result(
        client_name: str,
        *,
        status: str,
        source: str | None = None,
        command_id: str | None = None,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        path = Path(st_path)
        with state_lock:
            data: dict[str, Any] = {"meta": {}, "clients": {}}
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                except Exception:
                    pass
            data.setdefault("meta", {})
            clients = data.setdefault("clients", {})
            client_state = clients.setdefault(client_name, {})
            shutdown_state = client_state.setdefault("shutdown", {})
            shutdown_state.update(
                {
                    "status": status,
                    "source": source,
                    "command_id": command_id,
                    "last_error": error,
                    "updated_at": int(time.time()),
                }
            )
            if details:
                for key in (
                    "hostname",
                    "version",
                    "certificate_expires_at",
                    "auto_update",
                    "update_status",
                    "latest_version",
                    "last_update_error",
                    "update_checked_at",
                    "update_installed_at",
                ):
                    if details.get(key) is not None:
                        shutdown_state[key] = details[key]
            if status in {"paired", "online", "accepted"}:
                shutdown_state["last_seen_at"] = int(time.time())
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(data, indent=2, sort_keys=True))
            temporary.replace(path)

    def _notifications_from_raw(raw: dict) -> NotificationService:
        config = notifications_config_from_dict(raw.get("notifications"))
        return NotificationService(config)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "auth_enabled": auth_enabled}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {
            "auth_enabled": auth_enabled,
            "user": admin_user if auth_enabled else None,
            "shutdown_admin_configured": shutdown_admin_configured,
            "secure_transport": _is_secure_request(request),
        }

    class LoginRequest(BaseModel):
        username: str
        password: str

    @app.post("/api/auth/login")
    def login(req: LoginRequest):
        if not auth_enabled:
            # no auth configured -> issue token anyway for consistency
            token = _create_access_token(req.username or "anonymous", jwt_secret)
            return {
                "access_token": token,
                "token_type": "bearer",
                "auth_enabled": False,
            }
        if req.username != admin_user or req.password != admin_pass:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        token = _create_access_token(req.username, jwt_secret)
        return {"access_token": token, "token_type": "bearer"}

    @app.get("/api/auth/me")
    def auth_me(user: str = Depends(require_auth)):
        return {"user": user, "auth_enabled": auth_enabled}

    @app.post("/api/auth/logout")
    def logout(user: str = Depends(require_auth)):
        # stateless JWT: client just discards token
        return {"status": "logged out"}

    @app.get("/api/config")
    def get_config(user: str = Depends(require_auth)):
        data = _read_config_or_default()
        return data

    @app.put("/api/config")
    def put_config(
        cfg: ConfigModel,
        request: Request,
        user: str = Depends(require_auth),
    ):
        raw = cfg.model_dump()

        def normalized_shutdown(client: dict) -> dict:
            shutdown = client.get("shutdown", {}) or {}
            return {
                "enabled": shutdown.get("enabled", False),
                "battery_percent": shutdown.get("battery_percent", 20),
                "agent_id": shutdown.get("agent_id"),
                "agent_port": shutdown.get("agent_port", 8184),
                "auto_update": shutdown.get("auto_update", False),
            }

        def persist_config(current: dict) -> dict:
            current_clients = {
                client.get("name"): client for client in current.get("clients", [])
            }
            submitted_names = {client.get("name") for client in raw.get("clients", [])}
            for client in raw.get("clients", []):
                previous = current_clients.get(client.get("name"), {})
                previous_shutdown = normalized_shutdown(previous)
                shutdown = normalized_shutdown(client)
                if shutdown["agent_id"] != previous_shutdown["agent_id"]:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Agent pairing changed while editing; refresh the configuration "
                            "and try again"
                        ),
                    )
                if shutdown["auto_update"] != previous_shutdown["auto_update"]:
                    raise HTTPException(
                        status_code=409,
                        detail="Use the agent auto-update control to change this setting",
                    )
                if shutdown != previous_shutdown:
                    _require_secure_admin(request)

            for name, previous in current_clients.items():
                if (
                    name not in submitted_names
                    and normalized_shutdown(previous)["agent_id"]
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Unpair {name} before removing the client",
                    )
            return raw

        # clean empty auth (so they become omitted if blank)
        if not raw["nut"].get("username"):
            raw["nut"].pop("username", None)
            raw["nut"].pop("password", None)
        # Filter out blank password if username missing
        try:
            validate_config(raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Try to resolve MAC for clients with "auto" and collect warnings (unless suppressed via WebUI Settings)
        warnings: list[dict] = []
        suppress_warnings = raw.get("webui", {}).get(
            "suppress_mac_warnings", False
        ) or raw.get("suppress_mac_warnings", False)
        if not suppress_warnings:
            for c in raw.get("clients", []):
                if c.get("enabled", True) is False:
                    continue
                if c.get("wake_enabled", True) and c.get("mac") == "auto":
                    host = c.get("host")
                    name = c.get("name", "?")
                    try:
                        resolved = resolve_mac_from_host(host)
                        if resolved:
                            logger.info(
                                "Resolved MAC for %s (%s) -> %s", name, host, resolved
                            )
                        else:
                            msg = f"Could not resolve MAC for {name} ({host})"
                            logger.warning(msg)
                            warnings.append(
                                {
                                    "client": name,
                                    "host": host,
                                    "message": msg,
                                    "field": "mac",
                                }
                            )
                    except Exception as e:
                        msg = f"Could not resolve MAC for {name} ({host}): {e}"
                        logger.warning(msg)
                        warnings.append(
                            {
                                "client": name,
                                "host": host,
                                "message": msg,
                                "field": "mac",
                            }
                        )
        # save
        try:
            config_store.update(persist_config)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")
        logger.info("Config saved via WebUI to %s", cfg_path)
        return {
            "status": "saved",
            "path": cfg_path,
            "config": raw,
            "warnings": warnings,
        }

    @app.get("/api/status")
    def get_status(user: str = Depends(require_auth)):
        # UPS status
        raw_cfg = _read_config_or_default()
        ups_name = raw_cfg.get("nut", {}).get("ups", "ups@localhost")
        ups_username = raw_cfg.get("nut", {}).get("username")
        ups_password = raw_cfg.get("nut", {}).get("password")
        ups_port = raw_cfg.get("nut", {}).get("port", 3493)
        ups_timeout = raw_cfg.get("nut", {}).get("timeout", 5)
        ups, ups_error = get_ups_status_detailed(
            ups_name,
            username=ups_username,
            password=ups_password,
            port=ups_port,
            timeout=ups_timeout,
        )
        import shutil as _shutil

        upsc_available = _shutil.which("upsc") is not None

        # state file
        state = {}
        sp = Path(st_path)
        if sp.exists():
            try:
                with open(sp, "r") as f:
                    state = json.loads(f.read())
            except Exception as e:
                state = {"error": str(e)}

        # client online checks
        clients_status = []
        for c in raw_cfg.get("clients", []):
            # skip ping for disabled clients to avoid noise, but report as offline
            enabled = c.get("enabled", True)
            if enabled:
                online = is_client_online(c["host"])
            else:
                online = False
            clients_status.append(
                {
                    "name": c["name"],
                    "host": c["host"],
                    "mac": c["mac"],
                    "online": online,
                    "always_wake": c.get("always_wake", False),
                    "enabled": enabled,
                    "wake_enabled": c.get("wake_enabled", True),
                    "shutdown": {
                        **(c.get("shutdown", {}) or {}),
                        "paired": bool((c.get("shutdown", {}) or {}).get("agent_id")),
                        "last_result": (
                            state.get("clients", {})
                            .get(c["name"], {})
                            .get("shutdown", {})
                        ),
                    },
                }
            )

        return {
            "ups": ups,
            "ups_error": ups_error,
            "upsc_available": upsc_available,
            "state": state,
            "clients": clients_status,
            "config_path": cfg_path,
            "status_path": st_path,
        }

    @app.get("/api/agents/install.sh")
    def download_agent_installer(request: Request, agent_port: int = 8184):
        if not _is_secure_request(request):
            raise HTTPException(
                status_code=426,
                detail="Agent installation requires HTTPS",
            )
        authorization = request.headers.get("Authorization", "").strip()
        enrollment_url = ""
        enrollment_token = ""
        if authorization:
            scheme, separator, credential = authorization.partition(" ")
            if separator == "" or scheme.lower() != "bearer" or not credential:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid agent enrollment authorization",
                )
            enrollment_token = credential.strip()
            try:
                enrollment = enrollment_store.bootstrap(enrollment_token)
            except EnrollmentError as error:
                raise HTTPException(
                    status_code=403,
                    detail="Enrollment token is invalid, expired, or already used",
                ) from error
            agent_port = int(enrollment["agent_port"])
            enrollment_url = _public_url(request) + "/api/agents/enroll"
        if not 1 <= agent_port <= 65535:
            raise HTTPException(
                status_code=422,
                detail="Agent port must be between 1 and 65535",
            )
        try:
            script = build_agent_install_script(
                download_base=_agent_download_base(),
                agent_port=agent_port,
                enrollment_url=enrollment_url,
                enrollment_token=enrollment_token,
            )
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail="Agent installer is unavailable",
            ) from error
        return Response(
            content=script,
            media_type="text/x-shellscript",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'inline; filename="install.sh"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/agents/manual-install")
    def create_manual_agent_install(
        req: AgentEnrollmentRequest,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        _find_client(_read_config_or_default(), req.client_name)
        try:
            return build_agent_manual_commands(_public_url(request), req.agent_port)
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.post("/api/agents/enrollments")
    def create_agent_enrollment(
        req: AgentEnrollmentRequest,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        raw = _read_config_or_default()
        client = _find_client(raw, req.client_name)
        if (client.get("shutdown", {}) or {}).get("agent_id"):
            raise HTTPException(
                status_code=409,
                detail="Unpair the existing agent before creating an install command",
            )
        try:
            created = enrollment_store.create(req.client_name, req.agent_port)
        except EnrollmentError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        try:
            command = build_agent_install_command(
                _public_url(request), created["token"], req.agent_port
            )
        except ValueError as error:
            enrollment_store.fail(created["enrollment_id"], str(error))
            raise HTTPException(status_code=503, detail=str(error)) from error
        logger.warning(
            "One-time agent enrollment created for %s by %s",
            req.client_name,
            user,
        )
        return {
            "enrollment_id": created["enrollment_id"],
            "expires_at": created["expires_at"],
            "install_command": command,
        }

    @app.get("/api/agents/enrollments/{enrollment_id}")
    def get_agent_enrollment(
        enrollment_id: str,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        try:
            return enrollment_store.status(enrollment_id)
        except EnrollmentError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/agents/enroll")
    def complete_agent_enrollment(
        req: AgentEnrollmentCompleteRequest,
        request: Request,
    ):
        if not _is_secure_request(request):
            raise HTTPException(
                status_code=426,
                detail="Agent enrollment requires HTTPS",
            )
        csr_hash = hashlib.sha256(req.csr.encode()).hexdigest()
        try:
            enrollment_id, enrollment = enrollment_store.claim(
                req.token,
                agent_id=req.agent_id,
                csr_hash=csr_hash,
            )
        except EnrollmentError as error:
            raise HTTPException(
                status_code=403,
                detail="Enrollment token is invalid, expired, or already used",
            ) from error

        client_name = str(enrollment["client_name"])
        try:
            server_cert = security_store.sign_agent_csr(req.csr, req.agent_id)
            identity = security_store.ensure_controller_identity()

            def persist_enrollment(value: dict) -> dict:
                target = _find_client(value, client_name)
                shutdown = target.setdefault("shutdown", {})
                existing_agent = shutdown.get("agent_id")
                if existing_agent and existing_agent != req.agent_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Client was paired while enrollment was in progress",
                    )
                shutdown.update(
                    {
                        "enabled": False,
                        "battery_percent": shutdown.get("battery_percent", 20),
                        "agent_id": req.agent_id,
                        "agent_port": int(enrollment["agent_port"]),
                        "auto_update": shutdown.get("auto_update", False),
                    }
                )
                validate_config(value)
                return value

            config_store.update(persist_enrollment)
            enrollment_store.complete(enrollment_id, req.agent_id)
            details = {
                "hostname": req.hostname,
                "version": req.version,
            }
            _record_agent_result(
                client_name,
                status="paired",
                details=details,
            )
            logger.warning(
                "Agent enrolled for client %s using one-time token", client_name
            )
            return {
                "status": "paired",
                "protocol_version": 1,
                "agent_id": req.agent_id,
                "controller_ca": identity.ca_cert.read_text(),
                "controller_cert": identity.client_cert.read_text(),
                "server_cert": server_cert,
            }
        except HTTPException as error:
            enrollment_store.fail(enrollment_id, str(error.detail))
            raise
        except (AgentError, EnrollmentError, OSError, ValueError) as error:
            enrollment_store.fail(enrollment_id, str(error))
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/agents/pair")
    def pair_agent(
        req: AgentPairRequest,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        raw = _read_config_or_default()
        client = _find_client(raw, req.client_name)
        try:
            result = AgentClient(
                client["host"],
                req.agent_port,
                security_store=security_store,
            ).pair(req.pairing_code, req.fingerprint)
        except AgentError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        def persist_pairing(value: dict) -> dict:
            target = _find_client(value, req.client_name)
            shutdown = target.setdefault("shutdown", {})
            shutdown.update(
                {
                    "enabled": False,
                    "battery_percent": shutdown.get("battery_percent", 20),
                    "agent_id": result["agent_id"],
                    "agent_port": req.agent_port,
                    "auto_update": shutdown.get("auto_update", False),
                }
            )
            validate_config(value)
            return value

        config_store.update(persist_pairing)
        _record_agent_result(req.client_name, status="paired", details=result)
        logger.info("Agent paired for client %s by %s", req.client_name, user)
        return {"status": "paired", **result}

    @app.post("/api/agents/{client_name}/test")
    def test_agent(
        client_name: str,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        client = _find_client(_read_config_or_default(), client_name)
        agent_id = (client.get("shutdown", {}) or {}).get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=409, detail="Client agent is not paired")
        try:
            result = _agent_client(client).status(agent_id)
        except AgentError as error:
            _record_agent_result(client_name, status="failed", error=str(error))
            raise HTTPException(status_code=502, detail=str(error)) from error
        _record_agent_result(client_name, status="online", details=result)
        return result

    @app.post("/api/agents/{client_name}/shutdown")
    def shutdown_agent(
        client_name: str,
        req: AgentShutdownRequest,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        if req.confirmation != client_name:
            raise HTTPException(
                status_code=400, detail="Device name confirmation does not match"
            )
        raw = _read_config_or_default()
        client = _find_client(raw, client_name)
        if client.get("enabled", True) is False:
            raise HTTPException(status_code=409, detail="Client is disabled")
        agent_id = (client.get("shutdown", {}) or {}).get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=409, detail="Client agent is not paired")
        command_id = f"manual:{uuid.uuid4()}"
        try:
            result = _agent_client(client).shutdown(
                agent_id,
                command_id,
                "manual",
                ups=raw.get("nut", {}).get("ups", ""),
            )
        except AgentError as error:
            _notifications_from_raw(raw).send(
                "shutdown_failed",
                "Device shutdown delivery failed",
                f"Wolnut could not reach {client_name}: {error}",
            )
            _record_agent_result(
                client_name,
                status="failed",
                source="manual",
                command_id=command_id,
                error=str(error),
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        _record_agent_result(
            client_name,
            status="accepted",
            source="manual",
            command_id=command_id,
        )
        _notifications_from_raw(raw).send(
            "shutdown_acknowledged",
            "Device shutdown accepted",
            f"{client_name} accepted a manual shutdown request from {user}.",
        )
        logger.warning("Manual shutdown accepted for %s by %s", client_name, user)
        return result

    @app.post("/api/agents/{client_name}/update")
    def update_agent(
        client_name: str,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        client = _find_client(_read_config_or_default(), client_name)
        agent_id = (client.get("shutdown", {}) or {}).get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=409, detail="Client agent is not paired")
        try:
            result = _agent_client(client).update(agent_id)
        except AgentError as error:
            _record_agent_result(client_name, status="update_failed", error=str(error))
            raise HTTPException(status_code=502, detail=str(error)) from error
        _record_agent_result(client_name, status="update_checking")
        logger.warning("Agent update check requested for %s by %s", client_name, user)
        return result

    @app.post("/api/agents/{client_name}/auto-update")
    def set_agent_auto_update(
        client_name: str,
        req: AgentAutoUpdateRequest,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        raw = _read_config_or_default()
        client = _find_client(raw, client_name)
        shutdown = client.get("shutdown", {}) or {}
        agent_id = shutdown.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=409, detail="Client agent is not paired")
        previous = bool(shutdown.get("auto_update", False))
        try:
            result = _agent_client(client).set_auto_update(agent_id, req.enabled)
        except AgentError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        def persist_policy(value: dict) -> dict:
            target = _find_client(value, client_name)
            target_shutdown = target.setdefault("shutdown", {})
            if target_shutdown.get("agent_id") != agent_id:
                raise HTTPException(
                    status_code=409,
                    detail="Agent pairing changed while update policy was being saved",
                )
            target_shutdown["auto_update"] = req.enabled
            validate_config(value)
            return value

        try:
            config_store.update(persist_policy)
        except Exception:
            try:
                _agent_client(client).set_auto_update(agent_id, previous)
            except AgentError:
                logger.error(
                    "Could not restore agent update policy for %s after save failure",
                    client_name,
                )
            raise
        _record_agent_result(
            client_name,
            status="online",
            details={"auto_update": req.enabled},
        )
        logger.warning(
            "Agent auto-update %s for %s by %s",
            "enabled" if req.enabled else "disabled",
            client_name,
            user,
        )
        return result

    @app.post("/api/agents/{client_name}/unpair")
    def unpair_agent(
        client_name: str,
        req: AgentUnpairRequest,
        request: Request,
        user: str = Depends(require_auth),
    ):
        _require_secure_admin(request)
        if req.confirmation != client_name:
            raise HTTPException(
                status_code=400, detail="Device name confirmation does not match"
            )
        raw = _read_config_or_default()
        client = _find_client(raw, client_name)
        agent_id = (client.get("shutdown", {}) or {}).get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=409, detail="Client agent is not paired")
        if not req.force_local:
            try:
                _agent_client(client).unpair(agent_id)
            except AgentError as error:
                raise HTTPException(
                    status_code=502,
                    detail=f"Remote unpair failed; retry or explicitly forget locally: {error}",
                ) from error

        def clear_pairing(value: dict) -> dict:
            target = _find_client(value, client_name)
            shutdown = target.setdefault("shutdown", {})
            shutdown["enabled"] = False
            shutdown["agent_id"] = None
            shutdown["auto_update"] = False
            validate_config(value)
            return value

        config_store.update(clear_pairing)
        _record_agent_result(client_name, status="unpaired")
        logger.warning(
            "Agent unpaired for %s by %s%s",
            client_name,
            user,
            " (local only)" if req.force_local else "",
        )
        return {"status": "unpaired", "local_only": req.force_local}

    @app.get("/api/ups")
    def get_ups(user: str = Depends(require_auth)):
        raw_cfg = _read_config_or_default()
        ups_name = raw_cfg.get("nut", {}).get("ups", "ups@localhost")
        ups_username = raw_cfg.get("nut", {}).get("username")
        ups_password = raw_cfg.get("nut", {}).get("password")
        ups_port = raw_cfg.get("nut", {}).get("port", 3493)
        ups_timeout = raw_cfg.get("nut", {}).get("timeout", 5)
        ups, ups_error = get_ups_status_detailed(
            ups_name,
            username=ups_username,
            password=ups_password,
            port=ups_port,
            timeout=ups_timeout,
        )
        import shutil as _shutil

        return {
            "ups": ups,
            "ups_error": ups_error,
            "upsc_available": _shutil.which("upsc") is not None,
        }

    @app.post("/api/wol")
    def post_wol(req: WolRequest, user: str = Depends(require_auth)):
        notifications = _notifications_from_raw(_read_config_or_default())
        if not validate_mac_format(req.mac):
            raise HTTPException(status_code=400, detail="Invalid MAC format")
        ok = send_wol_packet(req.mac, broadcast_ip=req.broadcast_ip)
        if not ok:
            notifications.send(
                "errors",
                "Wake-on-LAN failed",
                f"Could not send a wake packet to {req.mac}.",
            )
            raise HTTPException(status_code=500, detail="Failed to send WOL packet")
        notifications.send(
            "wake_sent",
            "Wake-on-LAN packet sent",
            f"Sent a wake packet to {req.mac}.",
        )
        return {"status": "sent", "mac": req.mac}

    @app.post("/api/wol/client/{client_name}")
    def post_wol_client(client_name: str, user: str = Depends(require_auth)):
        raw_cfg = _read_config_or_default()
        notifications = _notifications_from_raw(raw_cfg)
        for c in raw_cfg.get("clients", []):
            if c["name"] == client_name:
                lookup_mac = c["mac"]
                if lookup_mac == "auto":
                    resolved = resolve_mac_from_host(c["host"])
                    if not resolved:
                        notifications.send(
                            "errors",
                            "MAC resolution failed",
                            (
                                "Could not resolve a MAC address for "
                                f"{client_name} ({c['host']})."
                            ),
                        )
                        raise HTTPException(
                            status_code=400,
                            detail=f"Could not resolve MAC for {client_name}",
                        )
                    lookup_mac = resolved
                ok = send_wol_packet(lookup_mac)
                if not ok:
                    notifications.send(
                        "errors",
                        "Wake-on-LAN failed",
                        f"Could not send a wake packet to {client_name} ({lookup_mac}).",
                    )
                    raise HTTPException(
                        status_code=500, detail="Failed to send WOL packet"
                    )
                notifications.send(
                    "wake_sent",
                    "Wake-on-LAN packet sent",
                    f"Sent a wake packet to {client_name} ({lookup_mac}).",
                )
                return {"status": "sent", "client": client_name, "mac": lookup_mac}
        raise HTTPException(status_code=404, detail=f"Client {client_name} not found")

    @app.post("/api/notifications/test")
    def post_notification_test(
        req: NotificationTestRequest,
        user: str = Depends(require_auth),
    ):
        raw_notifications = req.notifications.model_dump()
        notifications = NotificationService(
            notifications_config_from_dict(raw_notifications)
        )
        result = notifications.send_test(req.provider)
        if not result.success:
            raise HTTPException(status_code=502, detail=result.error)
        return {"status": "sent", "provider": result.provider}

    @app.post("/api/resolve-mac")
    def post_resolve_mac(payload: dict, user: str = Depends(require_auth)):
        host = payload.get("host")
        if not host:
            raise HTTPException(status_code=400, detail="host required")
        mac = resolve_mac_from_host(host)
        if not mac:
            raise HTTPException(
                status_code=404, detail=f"Could not resolve MAC for {host}"
            )
        return {"host": host, "mac": mac}

    @app.post("/api/ping")
    def post_ping(payload: dict, user: str = Depends(require_auth)):
        host = payload.get("host")
        if not host:
            raise HTTPException(status_code=400, detail="host required")
        online = is_client_online(host)
        return {"host": host, "online": online}

    # ---- static frontend ----
    # In production, Vite build output lives at /app/webui/dist
    # In development, allow missing folder gracefully.
    dist_candidates = [
        Path(__file__).parent.parent / "webui" / "dist",
        Path("/app/webui/dist"),
        Path("/app/wolnut_webui/webui/dist"),
        Path.cwd() / "webui" / "dist",
    ]
    dist_dir = None
    for cand in dist_candidates:
        if cand.exists() and (cand / "index.html").exists():
            dist_dir = cand
            break

    if dist_dir:
        # mount assets
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        def serve_spa(full_path: str):
            # Don't intercept API/docs/openapi paths — let FastAPI 404
            if (
                full_path.startswith("api/")
                or full_path.startswith("docs")
                or full_path.startswith("openapi")
            ):
                from fastapi.responses import JSONResponse

                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # Try to serve exact file if exists
            maybe = dist_dir / full_path
            if full_path and maybe.exists() and maybe.is_file():
                return FileResponse(str(maybe))
            return FileResponse(str(dist_dir / "index.html"))

    else:
        logger.warning(
            "WebUI dist not found, serving API only. Candidates checked: %s",
            dist_candidates,
        )

        @app.get("/")
        def root_no_dist():
            return {
                "message": "Wolnut WebUI API is running. Frontend not built yet.",
                "docs": "/docs",
                "config": "/api/config",
                "status": "/api/status",
            }

    return app


# ---------------------------------------------------------------------------
# Threaded server launcher (used by CLI)
# ---------------------------------------------------------------------------


def start_web_server(
    host: str = "0.0.0.0",
    port: int = 8183,
    config_file: str | None = None,
    status_file: str | None = None,
):
    """Start uvicorn in a daemon thread. Returns thread."""
    try:
        import uvicorn
    except ImportError:
        logger.error("uvicorn not installed, webui disabled")
        return None

    app = create_app(config_file=config_file, status_file=status_file)

    def _run():
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            proxy_headers=True,
            forwarded_allow_ips=os.getenv("WOLNUT_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )

    t = threading.Thread(target=_run, daemon=True, name="wolnut-web")
    t.start()
    logger.info("Wolnut WebUI started at http://%s:%s", host, port)
    return t


if __name__ == "__main__":
    import uvicorn

    _app = create_app()
    uvicorn.run(
        _app,
        host="0.0.0.0",
        port=8183,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("WOLNUT_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
