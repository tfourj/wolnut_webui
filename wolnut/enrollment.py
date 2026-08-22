import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time

from pathlib import Path
from typing import Any


class EnrollmentError(RuntimeError):
    """A safe error produced by one-time agent enrollment."""


class EnrollmentStore:
    def __init__(self, security_directory: str | Path):
        self.directory = Path(security_directory)
        self.path = self.directory / "enrollments.json"
        self._lock = threading.RLock()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise EnrollmentError("Enrollment state is unavailable") from error
        if not isinstance(value, dict):
            raise EnrollmentError("Enrollment state is invalid")
        return value

    def _write_unlocked(self, value: dict[str, dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        handle, temporary = tempfile.mkstemp(prefix=".enrollments.", dir=self.directory)
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w") as file:
                json.dump(value, file, indent=2, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def create(
        self, client_name: str, agent_port: int, lifetime_seconds: int = 600
    ) -> dict[str, Any]:
        now = int(time.time())
        enrollment_id = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        with self._lock:
            records = self._read_unlocked()
            for record in records.values():
                if record.get("client_name") != client_name:
                    continue
                if int(record.get("expires_at", 0)) < now and record.get("status") in {
                    "pending",
                    "processing",
                }:
                    record["status"] = "expired"
                elif record.get("status") == "processing":
                    raise EnrollmentError(
                        "An agent enrollment is already in progress for this client"
                    )
                elif record.get("status") == "pending":
                    record["status"] = "superseded"
            records[enrollment_id] = {
                "client_name": client_name,
                "agent_port": agent_port,
                "token_hash": self._token_hash(token),
                "created_at": now,
                "expires_at": now + lifetime_seconds,
                "status": "pending",
                "agent_id": None,
                "last_error": None,
            }
            self._write_unlocked(records)
        return {
            "enrollment_id": enrollment_id,
            "token": token,
            "expires_at": now + lifetime_seconds,
        }

    def claim(
        self, token: str, *, agent_id: str = "", csr_hash: str = ""
    ) -> tuple[str, dict[str, Any]]:
        token_hash = self._token_hash(token.strip())
        now = int(time.time())
        with self._lock:
            records = self._read_unlocked()
            for enrollment_id, record in records.items():
                if not hmac.compare_digest(record.get("token_hash", ""), token_hash):
                    continue
                if now > int(record.get("expires_at", 0)):
                    record["status"] = "expired"
                    self._write_unlocked(records)
                    raise EnrollmentError("Enrollment token has expired")
                if record.get("status") != "pending":
                    same_agent = (
                        agent_id
                        and csr_hash
                        and record.get("claimed_agent_id") == agent_id
                        and record.get("claimed_csr_hash") == csr_hash
                    )
                    if record.get("status") in {"processing", "paired"} and same_agent:
                        return enrollment_id, dict(record)
                    raise EnrollmentError("Enrollment token has already been used")
                record["status"] = "processing"
                record["claimed_agent_id"] = agent_id or None
                record["claimed_csr_hash"] = csr_hash or None
                self._write_unlocked(records)
                return enrollment_id, dict(record)
        raise EnrollmentError("Enrollment token is invalid")

    def bootstrap(self, token: str) -> dict[str, Any]:
        """Return pending enrollment details without consuming the one-time token."""
        token_hash = self._token_hash(token.strip())
        now = int(time.time())
        with self._lock:
            records = self._read_unlocked()
            for record in records.values():
                if not hmac.compare_digest(record.get("token_hash", ""), token_hash):
                    continue
                if now > int(record.get("expires_at", 0)):
                    record["status"] = "expired"
                    self._write_unlocked(records)
                    raise EnrollmentError("Enrollment token has expired")
                if record.get("status") != "pending":
                    raise EnrollmentError("Enrollment token has already been used")
                return {
                    key: value for key, value in record.items() if key != "token_hash"
                }
        raise EnrollmentError("Enrollment token is invalid")

    def complete(self, enrollment_id: str, agent_id: str) -> None:
        self._set_result(enrollment_id, "paired", agent_id=agent_id)

    def fail(self, enrollment_id: str, error: str) -> None:
        self._set_result(enrollment_id, "failed", error=error)

    def _set_result(
        self,
        enrollment_id: str,
        status: str,
        *,
        agent_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            records = self._read_unlocked()
            record = records.get(enrollment_id)
            if not record:
                raise EnrollmentError("Enrollment session was not found")
            record["status"] = status
            record["agent_id"] = agent_id
            record["last_error"] = error
            record["completed_at"] = int(time.time())
            self._write_unlocked(records)

    def status(self, enrollment_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._read_unlocked().get(enrollment_id)
            if not record:
                raise EnrollmentError("Enrollment session was not found")
            result = {
                key: value for key, value in record.items() if key != "token_hash"
            }
            if result.get("status") in {"pending", "processing"} and int(
                time.time()
            ) > int(result.get("expires_at", 0)):
                result["status"] = "expired"
            return result
