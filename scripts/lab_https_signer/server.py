"""LAB-ONLY HTTPS Ed25519 signer.

NOT A PRODUCTION KMS/HSM. Private keys are read from an explicit lab file and
must only be generated in a temporary directory by tests or a lab operator.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


MAX_BODY_BYTES = 1_048_576


def _sha256_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _handler(
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    bearer_token: str,
) -> type[BaseHTTPRequestHandler]:
    class LabSignerHandler(BaseHTTPRequestHandler):
        server_version = "DiscoveryLabSigner/1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            if self.path != "/health":
                self._send_json(404, {"error": "not_found"})
                return
            self._send_json(
                200,
                {"status": "ok", "lab_only": True, "key_id": key_id},
            )

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            if self.path != "/v1/sign":
                self._send_json(404, {"error": "not_found"})
                return
            authorization = str(self.headers.get("Authorization") or "")
            if not hmac.compare_digest(authorization, f"Bearer {bearer_token}"):
                self._send_json(401, {"error": "unauthorized"})
                return
            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                content_length = 0
            if content_length <= 0 or content_length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "invalid_body_size"})
                return
            try:
                request = json.loads(self.rfile.read(content_length))
                requested_key_id = str(request.get("key_id") or "")
                payload_digest = str(request.get("payload_digest") or "")
                payload = base64.b64decode(
                    str(request.get("payload_base64") or ""),
                    validate=True,
                )
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid_request"})
                return
            if requested_key_id != key_id:
                self._send_json(409, {"error": "key_id_mismatch"})
                return
            if payload_digest != _sha256_digest(payload):
                self._send_json(409, {"error": "payload_digest_mismatch"})
                return
            signature = base64.urlsafe_b64encode(private_key.sign(payload)).decode(
                "ascii"
            )
            self._send_json(
                200,
                {
                    "key_id": key_id,
                    "payload_digest": payload_digest,
                    "signature": signature,
                    "lab_only": True,
                },
            )

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

    return LabSignerHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="NOT PRODUCTION: lab HTTPS signer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--tls-key", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("lab signer only binds to loopback")
    bearer_token = str(os.getenv("LAB_SIGNER_BEARER_TOKEN") or "").strip()
    if not bearer_token:
        parser.error("LAB_SIGNER_BEARER_TOKEN is required")
    loaded = serialization.load_pem_private_key(
        args.signing_key.read_bytes(),
        password=None,
    )
    if not isinstance(loaded, Ed25519PrivateKey):
        parser.error("lab signing key must be Ed25519")
    server = ThreadingHTTPServer(
        (args.host, args.port),
        _handler(private_key=loaded, key_id=args.key_id, bearer_token=bearer_token),
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certfile=args.cert, keyfile=args.tls_key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
