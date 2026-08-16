import subprocess
import logging
import platform
from typing import Optional

logger = logging.getLogger("wolnut")


def get_ups_status_detailed(
    ups_name: str, username: Optional[str] = None, password: Optional[str] = None
) -> tuple[dict, Optional[str]]:
    """
    Fetch UPS status via `upsc` and also return an error string if something went wrong.
    Returns (status_dict, error_str_or_None). Empty status with error means failure.
    Distinguishes "upsc not found" from NUT server errors so the WebUI can surface it.
    """
    import shutil

    env = None
    if username and password:
        env = {**subprocess.os.environ, "USERNAME": username, "PASSWORD": password}

    # Pre-check if upsc binary is available so we can give a clear message.
    if shutil.which("upsc") is None:
        msg = "Failed to get UPS status: [Errno 2] No such file or directory: 'upsc' — nut-client not installed"
        logger.error(msg)
        return {}, msg

    try:
        result = subprocess.run(
            ["upsc", ups_name],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
            check=False,
        )

        if result.returncode != 0:
            err = result.stderr.strip() or f"upsc exited with code {result.returncode}"
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
    ups_name: str, username: Optional[str] = None, password: Optional[str] = None
) -> dict:
    status, _ = get_ups_status_detailed(ups_name, username=username, password=password)
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
