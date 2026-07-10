from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Mapping


_FIELDS = ("api_key", "base_url", "model", "timeout")
_STORE_LOCK = threading.RLock()


class LLMConfigStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, str] | None:
        with _STORE_LOCK:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
        if not isinstance(payload, dict):
            return None
        config = {field: str(payload.get(field) or "").strip() for field in _FIELDS}
        if not all(config.values()):
            return None
        return config

    def save(self, config: Mapping[str, str]) -> None:
        payload = {field: str(config.get(field) or "").strip() for field in _FIELDS}
        if not all(payload.values()):
            raise ValueError("llm_config_requires_api_key_base_url_model_and_timeout")
        with _STORE_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _restrict_permissions(self.path.parent, 0o700)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _restrict_permissions(temporary, 0o600)
            temporary.replace(self.path)
            _restrict_permissions(self.path, 0o600)

    def delete(self) -> bool:
        with _STORE_LOCK:
            try:
                self.path.unlink()
            except FileNotFoundError:
                return False
            return True


def _restrict_permissions(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass
