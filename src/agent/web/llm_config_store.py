from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from agent.errors import redact_secrets
from agent.web.expert_review.consensus import ExpertModelProfile


_FIELDS = ("api_key", "base_url", "model", "timeout")
_IDENTITY_VERIFICATIONS = {"verified", "provider_attested", "unverified"}
_STORE_LOCK = threading.RLock()
_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class LLMConfigStore:
    """Durable LLM config store with multi-profile support.

    On-disk formats:
    - legacy single profile: flat ``{api_key, base_url, model, timeout}``
    - multi-profile v2: ``{version: 2, default_profile_id, profiles: [...]}``

    ``load()`` / ``save()`` / ``delete()`` keep the historical default-profile
    surface used by existing callers. Prefer ``list_profiles`` /
    ``get_profile`` / ``upsert_profile`` for expert multi-model flows.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, str] | None:
        """Return the default profile as a flat config dict, or None."""
        with _STORE_LOCK:
            document = self._read_document()
            if document is None:
                return None
            profile = self._default_profile(document)
            if profile is None:
                return None
            return self._flat_config(profile)

    def save(self, config: Mapping[str, str]) -> None:
        """Create/update the default profile from a flat config dict."""
        payload = {field: str(config.get(field) or "").strip() for field in _FIELDS}
        if not all(payload.values()):
            raise ValueError("llm_config_requires_api_key_base_url_model_and_timeout")
        with _STORE_LOCK:
            document = self._read_document() or self._empty_document()
            default_id = str(document.get("default_profile_id") or "default")
            profiles = list(document.get("profiles") or [])
            updated = False
            for index, profile in enumerate(profiles):
                if str(profile.get("id") or "") == default_id:
                    profiles[index] = {
                        **profile,
                        "id": default_id,
                        "label": str(profile.get("label") or "Default"),
                        **payload,
                    }
                    updated = True
                    break
            if not updated:
                profiles.insert(
                    0,
                    {
                        "id": default_id,
                        "label": "Default",
                        **payload,
                    },
                )
            document["version"] = 2
            document["default_profile_id"] = default_id
            document["profiles"] = profiles
            self._write_document(document)

    def delete(self) -> bool:
        """Delete the whole store file (legacy behavior)."""
        with _STORE_LOCK:
            try:
                self.path.unlink()
            except FileNotFoundError:
                return False
            return True

    def list_profiles(self, *, include_secrets: bool = False) -> list[dict[str, Any]]:
        with _STORE_LOCK:
            document = self._read_document()
            if document is None:
                return []
            default_id = str(document.get("default_profile_id") or "")
            result: list[dict[str, Any]] = []
            for profile in document.get("profiles") or []:
                if not isinstance(profile, dict):
                    continue
                item = self._public_profile(profile, default_id=default_id)
                if include_secrets:
                    item["api_key"] = str(profile.get("api_key") or "")
                result.append(item)
            return result

    def get_profile(self, profile_id: str, *, include_secrets: bool = False) -> dict[str, Any] | None:
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            return None
        with _STORE_LOCK:
            document = self._read_document()
            if document is None:
                return None
            default_id = str(document.get("default_profile_id") or "")
            for profile in document.get("profiles") or []:
                if not isinstance(profile, dict):
                    continue
                if str(profile.get("id") or "") != profile_id:
                    continue
                item = self._public_profile(profile, default_id=default_id)
                if include_secrets:
                    item["api_key"] = str(profile.get("api_key") or "")
                    item.update(self._flat_config(profile) or {})
                return item
            return None

    def get_profile_secrets(self, profile_id: str | None = None) -> dict[str, str] | None:
        """Return flat secrets for a profile (default when id omitted)."""
        with _STORE_LOCK:
            document = self._read_document()
            if document is None:
                return None
            if profile_id:
                for profile in document.get("profiles") or []:
                    if isinstance(profile, dict) and str(profile.get("id") or "") == profile_id:
                        return self._flat_config(profile)
                return None
            profile = self._default_profile(document)
            return self._flat_config(profile) if profile else None

    def upsert_profile(self, profile: Mapping[str, Any], *, make_default: bool = False) -> dict[str, Any]:
        """Create or update a named profile.

        Empty ``api_key`` on update keeps the existing key. Required fields for
        a complete profile: api_key (or existing), base_url, model, timeout.
        """
        profile_id = str(profile.get("id") or "").strip() or self._new_profile_id()
        if not _PROFILE_ID_RE.match(profile_id):
            raise ValueError("invalid_profile_id")
        label = str(profile.get("label") or profile_id).strip() or profile_id
        with _STORE_LOCK:
            document = self._read_document() or self._empty_document()
            profiles = [
                item
                for item in (document.get("profiles") or [])
                if isinstance(item, dict)
            ]
            existing = next((item for item in profiles if str(item.get("id") or "") == profile_id), None)
            api_key = str(profile.get("api_key") or "").strip()
            if not api_key and existing is not None:
                api_key = str(existing.get("api_key") or "").strip()
            base_url = str(profile.get("base_url") or (existing or {}).get("base_url") or "").strip().rstrip("/")
            model = str(profile.get("model") or (existing or {}).get("model") or "").strip()
            timeout = str(profile.get("timeout") or (existing or {}).get("timeout") or "").strip()
            if not all([api_key, base_url, model, timeout]):
                raise ValueError("llm_config_requires_api_key_base_url_model_and_timeout")
            try:
                if float(timeout) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("llm_config_timeout_must_be_positive_number") from None
            identity = self._identity_metadata(
                profile,
                existing=existing,
                profile_id=profile_id,
                base_url=base_url,
                model=model,
            )
            payload = {
                "id": profile_id,
                "label": label,
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "timeout": timeout,
                **identity,
            }
            if existing is None:
                profiles.append(payload)
            else:
                profiles = [
                    payload if str(item.get("id") or "") == profile_id else item
                    for item in profiles
                ]
            first_profile = existing is None and len(profiles) == 1
            if make_default or first_profile or not document.get("default_profile_id"):
                document["default_profile_id"] = profile_id
            document["version"] = 2
            document["profiles"] = profiles
            self._write_document(document)
            return self._public_profile(payload, default_id=str(document["default_profile_id"]))

    def delete_profile(self, profile_id: str) -> bool:
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            return False
        with _STORE_LOCK:
            document = self._read_document()
            if document is None:
                return False
            profiles = [
                item
                for item in (document.get("profiles") or [])
                if isinstance(item, dict) and str(item.get("id") or "") != profile_id
            ]
            original = [
                item
                for item in (document.get("profiles") or [])
                if isinstance(item, dict)
            ]
            if len(profiles) == len(original):
                return False
            if not profiles:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                return True
            document["profiles"] = profiles
            if str(document.get("default_profile_id") or "") == profile_id:
                document["default_profile_id"] = str(profiles[0].get("id") or "default")
            document["version"] = 2
            self._write_document(document)
            return True

    def set_default_profile(self, profile_id: str) -> dict[str, Any]:
        profile_id = str(profile_id or "").strip()
        with _STORE_LOCK:
            document = self._read_document()
            if document is None:
                raise ValueError("profile_not_found")
            match = None
            for profile in document.get("profiles") or []:
                if isinstance(profile, dict) and str(profile.get("id") or "") == profile_id:
                    match = profile
                    break
            if match is None:
                raise ValueError("profile_not_found")
            document["default_profile_id"] = profile_id
            document["version"] = 2
            self._write_document(document)
            return self._public_profile(match, default_id=profile_id)

    def _read_document(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if "profiles" in payload:
            profiles = [
                item
                for item in (payload.get("profiles") or [])
                if isinstance(item, dict) and self._flat_config(item) is not None
            ]
            if not profiles:
                return None
            default_id = str(payload.get("default_profile_id") or profiles[0].get("id") or "default")
            if not any(str(item.get("id") or "") == default_id for item in profiles):
                default_id = str(profiles[0].get("id") or "default")
            return {
                "version": 2,
                "default_profile_id": default_id,
                "profiles": profiles,
            }
        flat = self._flat_config(payload)
        if flat is None:
            return None
        return {
            "version": 2,
            "default_profile_id": "default",
            "profiles": [{"id": "default", "label": "Default", **flat}],
        }

    def _write_document(self, document: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _restrict_permissions(self.path.parent, 0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _restrict_permissions(temporary, 0o600)
        temporary.replace(self.path)
        _restrict_permissions(self.path, 0o600)

    @staticmethod
    def _empty_document() -> dict[str, Any]:
        return {"version": 2, "default_profile_id": "default", "profiles": []}

    @staticmethod
    def _flat_config(profile: Mapping[str, Any] | None) -> dict[str, str] | None:
        if not isinstance(profile, Mapping):
            return None
        config = {field: str(profile.get(field) or "").strip() for field in _FIELDS}
        if config.get("base_url"):
            config["base_url"] = config["base_url"].rstrip("/")
        if not all(config.values()):
            return None
        return config

    @staticmethod
    def _default_profile(document: Mapping[str, Any]) -> dict[str, Any] | None:
        profiles = [item for item in (document.get("profiles") or []) if isinstance(item, dict)]
        if not profiles:
            return None
        default_id = str(document.get("default_profile_id") or "")
        for profile in profiles:
            if str(profile.get("id") or "") == default_id:
                return profile
        return profiles[0]

    @staticmethod
    def _public_profile(profile: Mapping[str, Any], *, default_id: str) -> dict[str, Any]:
        profile_id = str(profile.get("id") or "")
        model = str(profile.get("model") or "")
        base_url = _normalize_endpoint_identity(profile.get("base_url"))
        endpoint_identity = _normalize_endpoint_identity(
            profile.get("endpoint_identity") or base_url
        )
        verification = str(profile.get("identity_verification") or "unverified")
        identity = ExpertModelProfile.from_mapping(
            {
                **profile,
                "base_url": base_url,
                "endpoint_identity": endpoint_identity,
                "identity_verification": (
                    "provider_attested" if verification == "verified" else verification
                ),
            }
        )
        return {
            "id": profile_id,
            "label": str(profile.get("label") or profile_id),
            "base_url": base_url,
            "model": model,
            "timeout": str(profile.get("timeout") or ""),
            "provider": identity.provider,
            "requested_model_id": identity.requested_model_id,
            "resolved_model_id": identity.resolved_model_id,
            "model_family": identity.model_family,
            "endpoint_identity": endpoint_identity,
            "routing_profile_id": identity.routing_profile_id,
            "identity_verification": identity.identity_verification,
            "enabled": identity.enabled,
            "capabilities": list(identity.capabilities),
            "config_version": identity.config_version,
            "api_key_set": bool(str(profile.get("api_key") or "").strip()),
            "is_default": profile_id == default_id,
        }

    @staticmethod
    def _identity_metadata(
        profile: Mapping[str, Any],
        *,
        existing: Mapping[str, Any] | None,
        profile_id: str,
        base_url: str,
        model: str,
    ) -> dict[str, Any]:
        current = existing or {}
        verification = str(
            profile.get("identity_verification")
            or current.get("identity_verification")
            or "unverified"
        ).strip()
        if verification not in _IDENTITY_VERIFICATIONS:
            raise ValueError("invalid_identity_verification")
        if verification == "verified":
            raise ValueError("verified_identity_requires_runtime_attestation")
        if "resolved_model_id" in profile:
            resolved_model_id = str(profile.get("resolved_model_id") or "").strip() or None
        else:
            resolved_model_id = str(current.get("resolved_model_id") or "").strip() or None
        raw_capabilities = profile.get("capabilities", current.get("capabilities") or [])
        if isinstance(raw_capabilities, str):
            raw_capabilities = [raw_capabilities]
        if not isinstance(raw_capabilities, list):
            raise ValueError("profile_capabilities_must_be_list")
        return {
            "provider": str(profile.get("provider") or current.get("provider") or "openai_compatible").strip(),
            "requested_model_id": str(
                profile.get("requested_model_id") or current.get("requested_model_id") or model
            ).strip(),
            "resolved_model_id": resolved_model_id,
            "model_family": str(profile.get("model_family") or current.get("model_family") or model).strip(),
            "endpoint_identity": _normalize_endpoint_identity(
                profile.get("endpoint_identity") or current.get("endpoint_identity") or base_url
            ),
            "routing_profile_id": str(
                profile.get("routing_profile_id") or current.get("routing_profile_id") or profile_id
            ).strip(),
            "identity_verification": verification,
            "enabled": _as_bool(profile.get("enabled", current.get("enabled")), default=True),
            "capabilities": [str(item) for item in raw_capabilities if str(item).strip()],
            "config_version": str(
                profile.get("config_version") or current.get("config_version") or "expert-model-profile/v1"
            ).strip(),
        }

    @staticmethod
    def _new_profile_id() -> str:
        return f"profile-{uuid.uuid4().hex[:8]}"


def _restrict_permissions(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _normalize_endpoint_identity(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        if redact_secrets(text) != text:
            raise ValueError("endpoint_identity_must_not_contain_credentials")
        if re.search(r"[\s?#@&=]", text):
            raise ValueError("invalid_endpoint_identity")
        return text
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid_endpoint_identity") from None
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("invalid_endpoint_identity")
    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{port}" if port is not None else hostname
    normalized = urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", ""))
    if redact_secrets(normalized) != normalized:
        raise ValueError("endpoint_identity_must_not_contain_credentials")
    return normalized
