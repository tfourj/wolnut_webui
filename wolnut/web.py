import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("wolnut")

try:
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as e:
    # Allow import without fastapi for tests that don't need web
    FastAPI = None  # type: ignore

from wolnut.config import DEFAULT_CONFIG_FILEPATHS, validate_config
from wolnut.monitor import get_ups_status, get_ups_status_detailed, is_client_online
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


class ClientModel(BaseModel):
    name: str
    host: str
    mac: str
    always_wake: bool = False
    enabled: bool = True


class WebUIConfigModel(BaseModel):
    suppress_mac_warnings: bool = False


class ConfigModel(BaseModel):
    log_level: str = "INFO"
    poll_interval: int = 15
    status_file: str = DEFAULT_STATE_FILEPATH
    nut: NutModel
    wake_on: WakeOnModel = Field(default_factory=WakeOnModel)
    clients: list[ClientModel] = Field(default_factory=list)
    webui: WebUIConfigModel = Field(default_factory=WebUIConfigModel)


class WolRequest(BaseModel):
    mac: str
    broadcast_ip: str = "255.255.255.255"


# ---------------------------------------------------------------------------
# Auth helpers (JWT bearer)
# ---------------------------------------------------------------------------

def _get_auth_config():
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    enabled = bool(username and password)
    # JWT secret: explicit env or fallback to password hash; if no password, use random ephemeral
    secret = os.getenv("WOLNUT_JWT_SECRET") or os.getenv("JWT_SECRET") or (password if password else "dev-secret-change-me")
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
        raise RuntimeError("FastAPI not installed. Install with: pip install fastapi uvicorn")

    app = FastAPI(title="Wolnut WebUI", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    cfg_path = resolve_config_path(config_file)
    st_path = resolve_status_path(status_file)

    # --- auth setup ---
    auth_enabled, admin_user, admin_pass, jwt_secret = _get_auth_config()
    security = HTTPBearer(auto_error=False)

    def require_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
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

    # --- helpers inside closure ---
    def _read_config_or_default() -> dict:
        raw = load_raw_config(cfg_path)
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
        if "suppress_mac_warnings" in raw and "suppress_mac_warnings" not in raw["webui"]:
            raw["webui"]["suppress_mac_warnings"] = raw.pop("suppress_mac_warnings")
        raw["webui"].setdefault("suppress_mac_warnings", False)
        for c in raw.get("clients", []) or []:
            c.setdefault("always_wake", False)
            c.setdefault("enabled", True)
        return raw

    @app.get("/api/health")
    def health():
        return {"status": "ok", "auth_enabled": auth_enabled}

    @app.get("/api/auth/status")
    def auth_status():
        return {"auth_enabled": auth_enabled, "user": admin_user if auth_enabled else None}

    class LoginRequest(BaseModel):
        username: str
        password: str

    @app.post("/api/auth/login")
    def login(req: LoginRequest):
        if not auth_enabled:
            # no auth configured -> issue token anyway for consistency
            token = _create_access_token(req.username or "anonymous", jwt_secret)
            return {"access_token": token, "token_type": "bearer", "auth_enabled": False}
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
    def put_config(cfg: ConfigModel, user: str = Depends(require_auth)):
        raw = cfg.model_dump()
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
        suppress_warnings = raw.get("webui", {}).get("suppress_mac_warnings", False) or raw.get("suppress_mac_warnings", False)
        if not suppress_warnings:
            for c in raw.get("clients", []):
                if c.get("enabled", True) is False:
                    continue
                if c.get("mac") == "auto":
                    host = c.get("host")
                    name = c.get("name", "?")
                    try:
                        resolved = resolve_mac_from_host(host)
                        if resolved:
                            logger.info("Resolved MAC for %s (%s) -> %s", name, host, resolved)
                        else:
                            msg = f"Could not resolve MAC for {name} ({host})"
                            logger.warning(msg)
                            warnings.append({"client": name, "host": host, "message": msg, "field": "mac"})
                    except Exception as e:
                        msg = f"Could not resolve MAC for {name} ({host}): {e}"
                        logger.warning(msg)
                        warnings.append({"client": name, "host": host, "message": msg, "field": "mac"})
        # save
        try:
            save_raw_config(cfg_path, raw)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")
        logger.info("Config saved via WebUI to %s", cfg_path)
        return {"status": "saved", "path": cfg_path, "config": raw, "warnings": warnings}

    @app.get("/api/status")
    def get_status(user: str = Depends(require_auth)):
        # UPS status
        raw_cfg = _read_config_or_default()
        ups_name = raw_cfg.get("nut", {}).get("ups", "ups@localhost")
        ups_username = raw_cfg.get("nut", {}).get("username")
        ups_password = raw_cfg.get("nut", {}).get("password")
        ups_port = raw_cfg.get("nut", {}).get("port", 3493)
        ups_timeout = raw_cfg.get("nut", {}).get("timeout", 5)
        ups, ups_error = get_ups_status_detailed(ups_name, username=ups_username, password=ups_password, port=ups_port, timeout=ups_timeout)
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

    @app.get("/api/ups")
    def get_ups(user: str = Depends(require_auth)):
        raw_cfg = _read_config_or_default()
        ups_name = raw_cfg.get("nut", {}).get("ups", "ups@localhost")
        ups_username = raw_cfg.get("nut", {}).get("username")
        ups_password = raw_cfg.get("nut", {}).get("password")
        ups_port = raw_cfg.get("nut", {}).get("port", 3493)
        ups_timeout = raw_cfg.get("nut", {}).get("timeout", 5)
        ups, ups_error = get_ups_status_detailed(ups_name, username=ups_username, password=ups_password, port=ups_port, timeout=ups_timeout)
        import shutil as _shutil

        return {
            "ups": ups,
            "ups_error": ups_error,
            "upsc_available": _shutil.which("upsc") is not None,
        }

    @app.post("/api/wol")
    def post_wol(req: WolRequest, user: str = Depends(require_auth)):
        if not validate_mac_format(req.mac):
            raise HTTPException(status_code=400, detail="Invalid MAC format")
        ok = send_wol_packet(req.mac, broadcast_ip=req.broadcast_ip)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to send WOL packet")
        return {"status": "sent", "mac": req.mac}

    @app.post("/api/wol/client/{client_name}")
    def post_wol_client(client_name: str, user: str = Depends(require_auth)):
        raw_cfg = _read_config_or_default()
        for c in raw_cfg.get("clients", []):
            if c["name"] == client_name:
                mac = c["host"] and c["mac"]
                lookup_mac = c["mac"]
                if lookup_mac == "auto":
                    resolved = resolve_mac_from_host(c["host"])
                    if not resolved:
                        raise HTTPException(status_code=400, detail=f"Could not resolve MAC for {client_name}")
                    lookup_mac = resolved
                ok = send_wol_packet(lookup_mac)
                if not ok:
                    raise HTTPException(status_code=500, detail="Failed to send WOL packet")
                return {"status": "sent", "client": client_name, "mac": lookup_mac}
        raise HTTPException(status_code=404, detail=f"Client {client_name} not found")

    @app.post("/api/resolve-mac")
    def post_resolve_mac(payload: dict, user: str = Depends(require_auth)):
        host = payload.get("host")
        if not host:
            raise HTTPException(status_code=400, detail="host required")
        mac = resolve_mac_from_host(host)
        if not mac:
            raise HTTPException(status_code=404, detail=f"Could not resolve MAC for {host}")
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
            if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
                from fastapi.responses import JSONResponse

                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # Try to serve exact file if exists
            maybe = dist_dir / full_path
            if full_path and maybe.exists() and maybe.is_file():
                return FileResponse(str(maybe))
            return FileResponse(str(dist_dir / "index.html"))
    else:
        logger.warning("WebUI dist not found, serving API only. Candidates checked: %s", dist_candidates)

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

def start_web_server(host: str = "0.0.0.0", port: int = 8183, config_file: str | None = None, status_file: str | None = None):
    """Start uvicorn in a daemon thread. Returns thread."""
    try:
        import uvicorn
    except ImportError:
        logger.error("uvicorn not installed, webui disabled")
        return None

    app = create_app(config_file=config_file, status_file=status_file)

    def _run():
        uvicorn.run(app, host=host, port=port, log_level="info")

    t = threading.Thread(target=_run, daemon=True, name="wolnut-web")
    t.start()
    logger.info("Wolnut WebUI started at http://%s:%s", host, port)
    return t


if __name__ == "__main__":
    import uvicorn

    _app = create_app()
    uvicorn.run(_app, host="0.0.0.0", port=8183)
