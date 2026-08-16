import subprocess
import logging
import platform
from typing import Optional

logger = logging.getLogger("wolnut")


def _clean_upsc_error(err: str) -> str:
    """Strip noisy SSL init line that NUT prints even on unrelated errors."""
    lines = []
    for line in err.splitlines():
        if "Init SSL without certificate database" in line:
            continue
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def _build_ups_target(ups_name: str, port: Optional[int] = None) -> str:
    """Append :port to ups_name@host if a non-default port is configured."""
    if port is None or port == 3493:
        return ups_name
    # ups_name format is <ups>@<host>[:port]
    if "@" in ups_name:
        host_part = ups_name.rsplit("@", 1)[-1]
        if ":" in host_part:
            return ups_name  # already has explicit port
        return f"{ups_name}:{port}"
    # bare host or unknown format - don't mangle
    return ups_name


def get_ups_status_detailed(
    ups_name: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    port: Optional[int] = None,
    timeout: int = 5,
) -> tuple[dict, Optional[str]]:
    """
    Fetch UPS status via `upsc` and also return an error string if something went wrong.
    Returns (status_dict, error_str_or_None). Empty status with error means failure.
    Distinguishes "upsc not found" from NUT server errors so the WebUI can surface it.
    """
    import shutil

    # NUT's libupsclient prints "Init SSL without certificate database" to
    # stderr when no CA cert DB is present.  Suppress via env and also filter
    # the line so logs stay clean - it is not actionable for the user.
    env = {**subprocess.os.environ, "NUT_QUIET_INIT_SSL": "1"}
    if username and password:
        env["USERNAME"] = username
        env["PASSWORD"] = password

    target = _build_ups_target(ups_name, port)

    # Pre-check if upsc binary is available so we can give a clear message.
    if shutil.which("upsc") is None:
        msg = "Failed to get UPS status: [Errno 2] No such file or directory: 'upsc' — nut-client not installed"
        logger.error(msg)
        return {}, msg

    try:
        result = subprocess.run(
            ["upsc", target],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )

        if result.returncode != 0:
            raw_err = result.stderr.strip() or f"upsc exited with code {result.returncode}"
            err = _clean_upsc_error(raw_err) or raw_err
            # Provide a more actionable hint for the most common NUT mis-config.
            if "Unknown UPS" in err:
                host = target.rsplit("@", 1)[-1].split(":")[0] if "@" in target else target
                err = f"{err} — check NUT server: run `upsc -l {host}` to list available UPS names and verify nut.ups in config"
            logger.error("upsc returned error: %s", err)
            return {}, err

        status: dict = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                status[key.strip()] = value.strip()

        return status, None

    except FileNotFoundError as e:
        msg = f"Failed to get UPS status: [Errno 2] No such file or directory: 'upsc' ({e})"
        logger.error(msg)
        return {}, msg
    except Exception as e:
        msg = f"Failed to get UPS status: {e}"
        logger.error(msg)
        return {}, msg


def get_ups_status(
    ups_name: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    port: Optional[int] = None,
    timeout: int = 5,
) -> dict:
    status, _ = get_ups_status_detailed(ups_name, username=username, password=password, port=port, timeout=timeout)
    return status


def is_client_online(host: str) -> bool:
    try:
        count_flag = "-n" if platform.system().lower() == "windows" else "-c"
        result = subprocess.run(
            ["ping", count_flag, "1", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        logger.debug("Host: %s Online: %s", host, result.returncode == 0)
        return result.returncode == 0
    except Exception as e:
        logger.warning("Failed to ping %s: %s", host, e)
        return False
