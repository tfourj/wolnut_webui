import os

import pytest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from wolnut.agent_client import AgentError, SecurityStore, normalize_fingerprint


def test_normalize_fingerprint():
    raw = ":".join(["AB"] * 32)
    assert normalize_fingerprint(raw) == "ab" * 32
    with pytest.raises(AgentError, match="64 hexadecimal"):
        normalize_fingerprint("not-a-fingerprint")


def test_controller_identity_is_persistent_and_private(tmp_path):
    security = SecurityStore(tmp_path / "security")
    first = security.ensure_controller_identity()
    first_certificate = first.client_cert.read_bytes()
    second = security.ensure_controller_identity()

    assert second.client_cert.read_bytes() == first_certificate
    assert (os.stat(security.directory).st_mode & 0o777) == 0o700
    for path in (first.ca_cert, first.client_cert, first.client_key):
        assert (os.stat(path).st_mode & 0o777) == 0o600


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
