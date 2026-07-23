from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field

from agent.models import JsonModel


class AuthorityLedgerRecord(JsonModel):
    namespace: str
    token: str
    payload_digest: str
    binding: dict[str, str] = Field(default_factory=dict)
    consumed: bool = False


class DurableAuthorityLedger:
    """SQLite-backed, cross-process Authority issuance and consumption ledger."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_environment(cls, *, required: bool = True) -> "DurableAuthorityLedger | None":
        raw = str(os.getenv("DISCOVERY_AUTHORITY_LEDGER_PATH") or "").strip()
        if not raw:
            if required:
                raise RuntimeError("DISCOVERY_AUTHORITY_LEDGER_PATH is required")
            return None
        return cls(raw)

    def reserve(
        self,
        namespace: str,
        token: str,
        payload_digest: str,
        *,
        binding: Mapping[str, Any] | None = None,
    ) -> bool:
        namespace, token, payload_digest, encoded_binding = self._normalize(
            namespace, token, payload_digest, binding
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO authority_ledger
                      (namespace, token, payload_digest, binding_json, consumed)
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    (namespace, token, payload_digest, encoded_binding),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            connection.commit()
        return True

    def get(self, namespace: str, token: str) -> AuthorityLedgerRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT namespace, token, payload_digest, binding_json, consumed
                FROM authority_ledger WHERE namespace = ? AND token = ?
                """,
                (str(namespace).strip(), str(token).strip()),
            ).fetchone()
        if row is None:
            return None
        return AuthorityLedgerRecord(
            namespace=row[0],
            token=row[1],
            payload_digest=row[2],
            binding=json.loads(row[3]),
            consumed=bool(row[4]),
        )

    def verify(
        self,
        namespace: str,
        token: str,
        payload_digest: str,
        *,
        binding: Mapping[str, Any] | None = None,
        allow_consumed: bool = True,
    ) -> bool:
        record = self.get(namespace, token)
        if record is None or record.payload_digest != str(payload_digest).strip():
            return False
        if record.consumed and not allow_consumed:
            return False
        expected = _normalize_binding(binding)
        return all(record.binding.get(key) == value for key, value in expected.items())

    def consume_many(
        self,
        entries: Sequence[tuple[str, str, str]],
    ) -> bool:
        normalized = [
            (str(namespace).strip(), str(token).strip(), str(digest).strip())
            for namespace, token, digest in entries
        ]
        if not normalized or any(not all(item) for item in normalized):
            return False
        if len(normalized) != len(set(normalized)):
            return False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for namespace, token, digest in normalized:
                row = connection.execute(
                    """
                    SELECT payload_digest, consumed FROM authority_ledger
                    WHERE namespace = ? AND token = ?
                    """,
                    (namespace, token),
                ).fetchone()
                if row is None or row[0] != digest or bool(row[1]):
                    connection.rollback()
                    return False
            for namespace, token, _digest in normalized:
                cursor = connection.execute(
                    """
                    UPDATE authority_ledger SET consumed = 1, consumed_at = CURRENT_TIMESTAMP
                    WHERE namespace = ? AND token = ? AND consumed = 0
                    """,
                    (namespace, token),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
            connection.commit()
        return True

    def release(self, namespace: str, token: str, payload_digest: str) -> bool:
        """Release an unconsumed reservation after a failed external operation."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM authority_ledger
                WHERE namespace = ? AND token = ? AND payload_digest = ?
                  AND consumed = 0
                """,
                (
                    str(namespace).strip(),
                    str(token).strip(),
                    str(payload_digest).strip(),
                ),
            )
            connection.commit()
        return cursor.rowcount == 1

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authority_ledger (
                    namespace TEXT NOT NULL,
                    token TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    binding_json TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    consumed_at TEXT,
                    PRIMARY KEY (namespace, token)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _normalize(
        namespace: str,
        token: str,
        payload_digest: str,
        binding: Mapping[str, Any] | None,
    ) -> tuple[str, str, str, str]:
        normalized = (
            str(namespace or "").strip(),
            str(token or "").strip(),
            str(payload_digest or "").strip(),
        )
        if not all(normalized):
            raise ValueError("ledger namespace, token, and payload_digest are required")
        encoded = json.dumps(
            _normalize_binding(binding),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (*normalized, encoded)


class ProductionSigningResult(JsonModel):
    key_id: str = Field(min_length=1, max_length=200)
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signature: str = Field(min_length=1, max_length=4000)


class ProductionPublicationSigner(Protocol):
    @property
    def key_id(self) -> str: ...

    def sign(self, payload: bytes, *, payload_digest: str) -> ProductionSigningResult: ...


@dataclass(frozen=True)
class ProductionAuthorityRuntime:
    signer: ProductionPublicationSigner
    verifier: "ProductionPublicationVerifier"
    ledger: DurableAuthorityLedger


class CallbackProductionPublicationSigner:
    """Testable seam for an external KMS/HSM client; owns no private key."""

    def __init__(
        self,
        *,
        key_id: str,
        callback: Callable[[bytes, str], ProductionSigningResult],
    ) -> None:
        self._key_id = str(key_id).strip()
        if not self._key_id:
            raise ValueError("production signer key_id is required")
        self._callback = callback

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes, *, payload_digest: str) -> ProductionSigningResult:
        result = self._callback(payload, payload_digest)
        if result.key_id != self.key_id or result.payload_digest != payload_digest:
            raise ValueError("external signer response does not match the request")
        return result


class HttpProductionPublicationSigner:
    """HTTP client for an external signer; HTTP success alone is never acceptance."""

    def __init__(
        self,
        *,
        endpoint: str,
        key_id: str,
        bearer_token: str,
        timeout: float = 15.0,
    ) -> None:
        self.endpoint = str(endpoint).strip()
        self._key_id = str(key_id).strip()
        self._bearer_token = str(bearer_token).strip()
        self.timeout = float(timeout)
        if not self.endpoint.startswith("https://"):
            raise ValueError("production signer endpoint must use https")
        if not self._key_id or not self._bearer_token:
            raise ValueError("production signer key_id and bearer token are required")

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes, *, payload_digest: str) -> ProductionSigningResult:
        response = httpx.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self._bearer_token}"},
            json={
                "key_id": self.key_id,
                "payload_digest": payload_digest,
                "payload_base64": base64.b64encode(payload).decode("ascii"),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = ProductionSigningResult.model_validate(response.json())
        if result.key_id != self.key_id or result.payload_digest != payload_digest:
            raise ValueError("external signer response does not match the request")
        return result


class ProductionPublicationVerifier:
    def __init__(
        self,
        public_keys: Mapping[
            str,
            bytes | tuple[bytes, Literal["active", "retired", "revoked"]],
        ],
    ):
        self._public_keys: dict[str, bytes] = {}
        self._key_status: dict[str, Literal["active", "retired", "revoked"]] = {}
        for raw_key_id, value in public_keys.items():
            key_id = str(raw_key_id).strip()
            if not key_id:
                continue
            if isinstance(value, tuple):
                raw_key, status = value
            else:
                raw_key, status = value, "active"
            if status not in {"active", "retired", "revoked"}:
                raise ValueError(f"invalid trusted key status for {key_id}: {status}")
            if raw_key:
                self._public_keys[key_id] = bytes(raw_key)
                self._key_status[key_id] = status

    @classmethod
    def from_environment(cls, *, required: bool = True) -> "ProductionPublicationVerifier | None":
        raw = str(os.getenv("DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS") or "").strip()
        if not raw:
            if required:
                raise RuntimeError("DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS is required")
            return None
        try:
            values = json.loads(raw)
            keys = {}
            for key_id, configured in values.items():
                if isinstance(configured, Mapping):
                    encoded = configured.get("public_key")
                    status = str(configured.get("status") or "active").strip().casefold()
                    keys[str(key_id)] = (
                        base64.b64decode(str(encoded), validate=True),
                        status,
                    )
                else:
                    keys[str(key_id)] = base64.b64decode(
                        str(configured), validate=True
                    )
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("trusted public keys must be a JSON base64 mapping") from exc
        return cls(keys)

    def verify(
        self,
        *,
        key_id: str,
        payload: bytes,
        signature: str,
        allow_retired: bool = True,
    ) -> bool:
        normalized_key_id = str(key_id).strip()
        raw_key = self._public_keys.get(normalized_key_id)
        status = self._key_status.get(normalized_key_id)
        if (
            raw_key is None
            or status == "revoked"
            or (status == "retired" and not allow_retired)
        ):
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            public_key = Ed25519PublicKey.from_public_bytes(raw_key)
            public_key.verify(base64.urlsafe_b64decode(signature.encode("ascii")), payload)
        except Exception:
            return False
        return True

    def key_status(self, key_id: str) -> str | None:
        return self._key_status.get(str(key_id).strip())


def authority_mode() -> str:
    mode = str(os.getenv("DISCOVERY_AUTHORITY_MODE") or "off").strip().casefold()
    return mode if mode in {"off", "dev", "production"} else "invalid"


def load_production_authority_runtime(
    *,
    signer: ProductionPublicationSigner | None = None,
) -> ProductionAuthorityRuntime:
    """Load the production trust boundary without any dev-key fallback."""

    if authority_mode() != "production":
        raise RuntimeError("production Authority runtime requires production mode")
    ledger = DurableAuthorityLedger.from_environment(required=True)
    verifier = ProductionPublicationVerifier.from_environment(required=True)
    if ledger is None or verifier is None:  # defensive for static narrowing
        raise RuntimeError("production Authority ledger and verifier are required")
    active_signer = signer
    if active_signer is None:
        endpoint = str(
            os.getenv("DISCOVERY_AUTHORITY_SIGNER_ENDPOINT") or ""
        ).strip()
        key_id = str(
            os.getenv("DISCOVERY_AUTHORITY_SIGNER_KEY_ID") or ""
        ).strip()
        bearer_token = str(
            os.getenv("DISCOVERY_AUTHORITY_SIGNER_BEARER_TOKEN") or ""
        ).strip()
        if not endpoint or not key_id or not bearer_token:
            raise RuntimeError("production external signer configuration is required")
        raw_timeout = str(
            os.getenv("DISCOVERY_AUTHORITY_SIGNER_TIMEOUT_SECONDS") or "15"
        ).strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise RuntimeError("production signer timeout must be numeric") from exc
        if timeout <= 0 or timeout > 120:
            raise RuntimeError("production signer timeout must be within 0..120 seconds")
        active_signer = HttpProductionPublicationSigner(
            endpoint=endpoint,
            key_id=key_id,
            bearer_token=bearer_token,
            timeout=timeout,
        )
    return ProductionAuthorityRuntime(
        signer=active_signer,
        verifier=verifier,
        ledger=ledger,
    )


def sha256_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def repair_completion_context_token(authority_id: str, attempt_id: str) -> str:
    return sha256_digest(
        _canonical_json_bytes(
            {"authority_id": authority_id, "attempt_id": attempt_id}
        )
    )


def repair_completion_context_digest(
    authority_id: str,
    attempt_id: str,
    nonce: str,
) -> str:
    return sha256_digest(
        _canonical_json_bytes(
            {
                "authority_id": authority_id,
                "attempt_id": attempt_id,
                "nonce": nonce,
            }
        )
    )


def issue_publication_completion_context(
    *,
    ledger: DurableAuthorityLedger,
    run_id: str,
    audit_ref: str,
    package_digest: str,
) -> dict[str, str]:
    """Issue or recover one durable normal-publication completion attempt."""

    normalized_run_id = str(run_id or "").strip()
    normalized_audit_ref = str(audit_ref or "").strip()
    normalized_package_digest = str(package_digest or "").strip()
    if not normalized_run_id or not normalized_audit_ref or not normalized_package_digest:
        raise ValueError(
            "publication completion requires run_id, audit_ref, and package_digest"
        )
    authority_id = f"publication-authority:{normalized_run_id}"
    attempt_suffix = hashlib.sha256(
        (
            f"{normalized_run_id}\n{normalized_audit_ref}\n"
            f"{normalized_package_digest}"
        ).encode("utf-8")
    ).hexdigest()
    attempt_id = f"publication-attempt:{attempt_suffix}"
    context_token = repair_completion_context_token(authority_id, attempt_id)
    existing = ledger.get("repair_completion_context", context_token)
    if existing is not None:
        nonce = existing.binding.get("nonce", "")
        digest = publication_completion_context_digest(
            authority_id,
            attempt_id,
            nonce,
            normalized_run_id,
            normalized_audit_ref,
            normalized_package_digest,
        )
        if (
            existing.consumed
            or not nonce
            or not ledger.verify(
                "repair_completion_context",
                context_token,
                digest,
                binding={
                    "authority_id": authority_id,
                    "attempt_id": attempt_id,
                    "nonce": nonce,
                    "run_id": normalized_run_id,
                    "audit_ref": normalized_audit_ref,
                    "package_digest": normalized_package_digest,
                },
                allow_consumed=False,
            )
        ):
            raise ValueError("publication completion context is unavailable")
    else:
        nonce = "publication-attempt-nonce:" + secrets.token_urlsafe(32)
        digest = publication_completion_context_digest(
            authority_id,
            attempt_id,
            nonce,
            normalized_run_id,
            normalized_audit_ref,
            normalized_package_digest,
        )
        if not ledger.reserve(
            "repair_completion_context",
            context_token,
            digest,
            binding={
                "authority_id": authority_id,
                "attempt_id": attempt_id,
                "nonce": nonce,
                "run_id": normalized_run_id,
                "audit_ref": normalized_audit_ref,
                "package_digest": normalized_package_digest,
            },
        ):
            raise RuntimeError("publication completion context collision")
    return {
        "repair_authority_id": authority_id,
        "repair_attempt_id": attempt_id,
        "repair_attempt_nonce": nonce,
    }


def publication_completion_context_digest(
    authority_id: str,
    attempt_id: str,
    nonce: str,
    run_id: str,
    audit_ref: str,
    package_digest: str,
) -> str:
    return sha256_digest(
        _canonical_json_bytes(
            {
                "authority_id": authority_id,
                "attempt_id": attempt_id,
                "nonce": nonce,
                "run_id": run_id,
                "audit_ref": audit_ref,
                "package_digest": package_digest,
            }
        )
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _normalize_binding(binding: Mapping[str, Any] | None) -> dict[str, str]:
    return {
        str(key).strip(): str(value).strip()
        for key, value in (binding or {}).items()
        if str(key).strip() and str(value).strip()
    }


__all__ = [
    "AuthorityLedgerRecord",
    "CallbackProductionPublicationSigner",
    "DurableAuthorityLedger",
    "HttpProductionPublicationSigner",
    "ProductionPublicationSigner",
    "ProductionAuthorityRuntime",
    "ProductionPublicationVerifier",
    "ProductionSigningResult",
    "authority_mode",
    "load_production_authority_runtime",
    "issue_publication_completion_context",
    "publication_completion_context_digest",
    "repair_completion_context_digest",
    "repair_completion_context_token",
    "sha256_digest",
]
