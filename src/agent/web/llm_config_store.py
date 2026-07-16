from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping


_FIELDS = ("api_key", "base_url", "model", "timeout")
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
            payload = {
                "id": profile_id,
                "label": label,
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "timeout": timeout,
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
        return {
            "id": profile_id,
            "label": str(profile.get("label") or profile_id),
            "base_url": str(profile.get("base_url") or "").rstrip("/"),
            "model": str(profile.get("model") or ""),
            "timeout": str(profile.get("timeout") or ""),
            "api_key_set": bool(str(profile.get("api_key") or "").strip()),
            "is_default": profile_id == default_id,
        }

    @staticmethod
    def _new_profile_id() -> str:
        return f"profile-{uuid.uuid4().hex[:8]}"


def _restrict_permissions(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass
