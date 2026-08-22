import os
import tempfile
import threading

from pathlib import Path
from typing import Callable

import yaml


class ConfigStore:
    """Thread-safe, atomic storage for the WebUI-managed YAML configuration."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def read(self) -> dict | None:
        with self._lock:
            if not self.path.exists():
                return None
            with self.path.open("r") as file:
                return yaml.safe_load(file) or {}

    def write(self, data: dict) -> None:
        with self._lock:
            self._write_unlocked(data)

    def update(self, operation: Callable[[dict], dict]) -> dict:
        with self._lock:
            current = {}
            if self.path.exists():
                with self.path.open("r") as file:
                    current = yaml.safe_load(file) or {}
            updated = operation(current)
            self._write_unlocked(updated)
            return updated

    def _write_unlocked(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current_mode = 0o600
        if self.path.exists():
            current_mode = self.path.stat().st_mode & 0o777
        handle, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            os.fchmod(handle, current_mode)
            with os.fdopen(handle, "w") as file:
                yaml.safe_dump(data, file, sort_keys=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
