import hashlib
import http.client
import json
import os
import ssl
import tempfile
import threading
import time

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class AgentError(RuntimeError):
    """A safe, credential-free error returned by a Wolnut agent operation."""


def normalize_fingerprint(value: str) -> str:
    normalized = "".join(
        character for character in value if character.isalnum()
    ).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise AgentError(
            "Certificate fingerprint must contain 64 hexadecimal characters"
        )
    return normalized


@dataclass(frozen=True)
class ControllerIdentity:
    ca_cert: Path
    client_cert: Path
    client_key: Path


class SecurityStore:
    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(
            directory or os.getenv("WOLNUT_SECURITY_DIR", "/config/security")
        )
        self._lock = threading.RLock()

    def _write_private(self, path: Path, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def ensure_controller_identity(self) -> ControllerIdentity:
        with self._lock:
            return self._ensure_controller_identity()

    def _ensure_controller_identity(self) -> ControllerIdentity:
        ca_key_path = self.directory / "controller-ca.key"
        ca_cert_path = self.directory / "controller-ca.crt"
        client_key_path = self.directory / "controller-client.key"
        client_cert_path = self.directory / "controller-client.crt"
        paths = (ca_key_path, ca_cert_path, client_key_path, client_cert_path)
        if all(path.exists() for path in paths):
            for path in paths:
                os.chmod(path, 0o600)
            os.chmod(self.directory, 0o700)
            return ControllerIdentity(ca_cert_path, client_cert_path, client_key_path)
        if any(path.exists() for path in paths):
            raise AgentError(
                "Controller security identity is incomplete; restore it or remove all identity files"
            )

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        now = datetime.now(timezone.utc)
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca_subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Wolnut local controller CA")]
        )
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

        client_key = ec.generate_private_key(ec.SECP256R1())
        controller_id = hashlib.sha256(
            client_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).hexdigest()[:32]
        client_subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "wolnut-controller")]
        )
        client_cert = (
            x509.CertificateBuilder()
            .subject_name(client_subject)
            .issuer_name(ca_cert.subject)
            .public_key(client_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1825))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.UniformResourceIdentifier(
                            f"urn:wolnut:controller:{controller_id}"
                        )
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(client_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

        private_format = serialization.PrivateFormat.PKCS8
        encryption = serialization.NoEncryption()
        self._write_private(
            ca_key_path,
            ca_key.private_bytes(
                serialization.Encoding.PEM, private_format, encryption
            ),
        )
        self._write_private(
            ca_cert_path, ca_cert.public_bytes(serialization.Encoding.PEM)
        )
        self._write_private(
            client_key_path,
            client_key.private_bytes(
                serialization.Encoding.PEM, private_format, encryption
            ),
        )
        self._write_private(
            client_cert_path, client_cert.public_bytes(serialization.Encoding.PEM)
        )
        return ControllerIdentity(ca_cert_path, client_cert_path, client_key_path)

    def sign_agent_csr(self, csr_pem: str, agent_id: str) -> str:
        identity = self.ensure_controller_identity()
        ca_key = serialization.load_pem_private_key(
            (self.directory / "controller-ca.key").read_bytes(), password=None
        )
        ca_cert = x509.load_pem_x509_certificate(identity.ca_cert.read_bytes())
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode())
        except ValueError as error:
            raise AgentError("Agent returned an invalid certificate request") from error
        if not csr.is_signature_valid:
            raise AgentError("Agent certificate request signature is invalid")
        expected_identity = f"urn:wolnut:agent:{agent_id}"
        try:
            names = csr.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            uris = names.get_values_for_type(x509.UniformResourceIdentifier)
        except x509.ExtensionNotFound as error:
            raise AgentError("Agent certificate request has no identity") from error
        if uris != [expected_identity]:
            raise AgentError("Agent certificate request identity does not match")

        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(ca_cert.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1825))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(expected_identity)]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=True
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        return certificate.public_bytes(serialization.Encoding.PEM).decode()


class AgentClient:
    def __init__(
        self,
        host: str,
        port: int = 8184,
        security_store: SecurityStore | None = None,
        timeout: float = 5,
    ):
        self.host = host
        self.port = port
        self.security_store = security_store or SecurityStore()
        self.timeout = timeout

    def _decode_response(self, response: http.client.HTTPResponse) -> dict[str, Any]:
        body = response.read(64 * 1024)
        try:
            value = json.loads(body or b"{}")
        except json.JSONDecodeError as error:
            raise AgentError("Agent returned an invalid response") from error
        if response.status >= 400:
            message = value.get("error") if isinstance(value, dict) else None
            raise AgentError(
                str(message or f"Agent request failed with status {response.status}")
            )
        if not isinstance(value, dict):
            raise AgentError("Agent returned an invalid response")
        return value

    def _pinned_post(
        self, path: str, payload: dict[str, Any], fingerprint: str
    ) -> dict[str, Any]:
        expected = normalize_fingerprint(fingerprint)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            self.host, self.port, timeout=self.timeout, context=context
        )
        try:
            connection.connect()
            certificate = connection.sock.getpeercert(binary_form=True)
            actual = hashlib.sha256(certificate).hexdigest()
            if actual != expected:
                raise AgentError("Agent certificate fingerprint does not match")
            body = json.dumps(payload).encode()
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            return self._decode_response(connection.getresponse())
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            raise AgentError(f"Could not securely contact agent: {error}") from error
        finally:
            connection.close()

    def pair(self, code: str, fingerprint: str) -> dict[str, Any]:
        identity = self.security_store.ensure_controller_identity()
        request = {
            "code": code.strip(),
            "controller_ca": identity.ca_cert.read_text(),
            "controller_cert": identity.client_cert.read_text(),
        }
        started = self._pinned_post("/bootstrap/pair", request, fingerprint)
        agent_id = str(started.get("agent_id", ""))
        csr = str(started.get("csr", ""))
        completion_token = str(started.get("completion_token", ""))
        if not agent_id or not csr or not completion_token:
            raise AgentError("Agent returned incomplete pairing information")
        server_cert = self.security_store.sign_agent_csr(csr, agent_id)
        completed = self._pinned_post(
            "/bootstrap/complete",
            {
                "completion_token": completion_token,
                "server_cert": server_cert,
            },
            fingerprint,
        )
        if completed.get("agent_id") != agent_id or completed.get("status") != "paired":
            raise AgentError("Agent did not confirm pairing")
        status = self.status(agent_id)
        return {"agent_id": agent_id, **status}

    def _request(
        self,
        method: str,
        path: str,
        agent_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self.security_store.ensure_controller_identity()
        context = ssl.create_default_context(cafile=str(identity.ca_cert))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.load_cert_chain(str(identity.client_cert), str(identity.client_key))
        connection = http.client.HTTPSConnection(
            self.host, self.port, timeout=self.timeout, context=context
        )
        try:
            connection.connect()
            certificate = x509.load_der_x509_certificate(
                connection.sock.getpeercert(binary_form=True)
            )
            try:
                names = certificate.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
                uris = names.get_values_for_type(x509.UniformResourceIdentifier)
            except x509.ExtensionNotFound as error:
                raise AgentError("Agent certificate has no identity") from error
            if f"urn:wolnut:agent:{agent_id}" not in uris:
                raise AgentError(
                    "Agent certificate identity does not match configured device"
                )
            body = json.dumps(payload).encode() if payload is not None else None
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            return self._decode_response(connection.getresponse())
        except AgentError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            raise AgentError(f"Could not securely contact agent: {error}") from error
        finally:
            connection.close()

    def status(self, agent_id: str) -> dict[str, Any]:
        result = self._request("GET", "/v1/status", agent_id)
        if result.get("agent_id") != agent_id or result.get("protocol_version") != 1:
            raise AgentError("Agent identity or protocol version does not match")
        return result

    def shutdown(
        self,
        agent_id: str,
        command_id: str,
        source: str,
        ups: str = "",
        battery_percent: int | None = None,
        threshold_percent: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        payload = {
            "command_id": command_id,
            "source": source,
            "requested_at": now,
            "expires_at": now + 60,
            "ups": ups,
            "battery_percent": battery_percent,
            "threshold_percent": threshold_percent,
        }
        return self._request("POST", "/v1/shutdown", agent_id, payload)

    def unpair(self, agent_id: str) -> dict[str, Any]:
        return self._request("POST", "/v1/unpair", agent_id, {})

    def update(self, agent_id: str) -> dict[str, Any]:
        return self._request("POST", "/v1/update", agent_id, {})

    def set_auto_update(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/update-policy", agent_id, {"enabled": enabled}
        )
