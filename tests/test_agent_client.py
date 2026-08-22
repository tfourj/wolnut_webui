import os
import hashlib
import threading

import pytest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from wolnut.agent_client import (
    AgentClient,
    AgentError,
    SecurityStore,
    normalize_fingerprint,
)


def test_normalize_fingerprint():
    raw = ":".join(["AB"] * 32)
    assert normalize_fingerprint(raw) == "ab" * 32
    with pytest.raises(AgentError, match="64 hexadecimal"):
        normalize_fingerprint("not-a-fingerprint")


def test_pairing_stops_before_sending_code_on_fingerprint_mismatch(mocker):
    connection = mocker.patch("wolnut.agent_client.http.client.HTTPSConnection")
    instance = connection.return_value
    instance.sock.getpeercert.return_value = b"unexpected-certificate"
    expected = hashlib.sha256(b"expected-certificate").hexdigest()

    with pytest.raises(AgentError, match="fingerprint does not match"):
        AgentClient("agent.local")._pinned_post(
            "/bootstrap/pair", {"code": "must-not-be-sent"}, expected
        )

    instance.request.assert_not_called()


def test_controller_identity_is_persistent_and_private(tmp_path):
    security = SecurityStore(tmp_path / "security")
    first = security.ensure_controller_identity()
    first_certificate = first.client_cert.read_bytes()
    second = security.ensure_controller_identity()

    assert second.client_cert.read_bytes() == first_certificate
    assert (os.stat(security.directory).st_mode & 0o777) == 0o700
    for path in (first.ca_cert, first.client_cert, first.client_key):
        assert (os.stat(path).st_mode & 0o777) == 0o600


def test_concurrent_controller_identity_creation_is_consistent(tmp_path):
    security = SecurityStore(tmp_path / "security")
    certificates = []

    def create_identity():
        identity = security.ensure_controller_identity()
        certificates.append(identity.client_cert.read_bytes())

    threads = [threading.Thread(target=create_identity) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(certificates) == 20
    assert len(set(certificates)) == 1


def test_incomplete_controller_identity_is_rejected(tmp_path):
    security = SecurityStore(tmp_path / "security")
    security.directory.mkdir()
    (security.directory / "controller-ca.key").write_text("partial")

    with pytest.raises(AgentError, match="incomplete"):
        security.ensure_controller_identity()


def test_sign_agent_csr_requires_matching_identity(tmp_path):
    security = SecurityStore(tmp_path / "security")
    security.ensure_controller_identity()
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")]))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("urn:wolnut:agent:other")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with pytest.raises(AgentError, match="does not match"):
        security.sign_agent_csr(
            csr.public_bytes(serialization.Encoding.PEM).decode(), "expected"
        )


def test_controller_and_agent_certificates_have_separate_roles(tmp_path):
    security = SecurityStore(tmp_path / "security")
    identity = security.ensure_controller_identity()
    controller = x509.load_pem_x509_certificate(identity.client_cert.read_bytes())
    controller_usage = controller.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value
    assert list(controller_usage) == [ExtendedKeyUsageOID.CLIENT_AUTH]

    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")]))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("urn:wolnut:agent:agent-1")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    signed = security.sign_agent_csr(
        csr.public_bytes(serialization.Encoding.PEM).decode(), "agent-1"
    )
    agent = x509.load_pem_x509_certificate(signed.encode())
    agent_usage = agent.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(agent_usage) == [ExtendedKeyUsageOID.SERVER_AUTH]
