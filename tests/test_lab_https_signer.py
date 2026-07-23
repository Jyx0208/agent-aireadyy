from __future__ import annotations

import datetime
import ipaddress
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

from agent.discovery.production_authority import (
    HttpProductionPublicationSigner,
    ProductionPublicationVerifier,
    sha256_digest,
)


SERVER = Path(__file__).resolve().parents[1] / "scripts" / "lab_https_signer" / "server.py"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_lab_material(tmp_path: Path) -> tuple[Path, Path, Path, bytes]:
    tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(tls_key, hashes.SHA256())
    )
    cert_path = tmp_path / "lab-cert.pem"
    tls_key_path = tmp_path / "lab-tls-key.pem"
    signing_key_path = tmp_path / "lab-signing-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    tls_key_path.write_bytes(
        tls_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    signing_key = ed25519.Ed25519PrivateKey.generate()
    signing_key_path.write_bytes(
        signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_bytes = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return cert_path, tls_key_path, signing_key_path, public_bytes


def test_http_production_signer_connects_to_lab_only_https_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assert SERVER.is_file(), "implement scripts/lab_https_signer/server.py"
    cert_path, tls_key_path, signing_key_path, public_bytes = _write_lab_material(
        tmp_path
    )
    port = _free_port()
    bearer = "ephemeral-lab-token"
    environment = {
        **os.environ,
        "LAB_SIGNER_BEARER_TOKEN": bearer,
        "NO_PROXY": "localhost,127.0.0.1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--cert",
            str(cert_path),
            "--tls-key",
            str(tls_key_path),
            "--signing-key",
            str(signing_key_path),
            "--key-id",
            "lab-key",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        context = ssl.create_default_context(cafile=str(cert_path))
        health_url = f"https://localhost:{port}/health"
        for _ in range(50):
            try:
                with urllib.request.urlopen(health_url, context=context, timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        else:
            stdout, stderr = process.communicate(timeout=2)
            raise AssertionError(f"lab signer did not start: {stdout}\n{stderr}")

        monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
        monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
        payload = b"lab-only production signer contract"
        signer = HttpProductionPublicationSigner(
            endpoint=f"https://localhost:{port}/v1/sign",
            key_id="lab-key",
            bearer_token=bearer,
        )
        result = signer.sign(payload, payload_digest=sha256_digest(payload))

        assert result.key_id == "lab-key"
        assert ProductionPublicationVerifier({"lab-key": public_bytes}).verify(
            key_id=result.key_id,
            payload=payload,
            signature=result.signature,
            allow_retired=False,
        )
        with pytest.raises(httpx.HTTPStatusError):
            HttpProductionPublicationSigner(
                endpoint=f"https://localhost:{port}/v1/sign",
                key_id="lab-key",
                bearer_token="wrong-lab-token",
            ).sign(payload, payload_digest=sha256_digest(payload))
        with pytest.raises(httpx.HTTPStatusError):
            signer.sign(payload, payload_digest="sha256:" + "0" * 64)
        with pytest.raises(httpx.HTTPStatusError):
            HttpProductionPublicationSigner(
                endpoint=f"https://localhost:{port}/v1/sign",
                key_id="wrong-lab-key",
                bearer_token=bearer,
            ).sign(payload, payload_digest=sha256_digest(payload))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
