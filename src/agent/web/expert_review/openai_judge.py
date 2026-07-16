from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from agent.discovery.blind_judging import BlindJudgmentVote


_SECRET_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)|(api[_-]?key\s*[:=]\s*)([^\s,;]+)|(bearer\s+)([^\s,;]+)"
)
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")


def redact_text(value: str) -> str:
    def replace_secret(match: re.Match[str]) -> str:
        prefix = match.group(1) or match.group(3) or match.group(5) or ""
        return f"{prefix}***"

    text = _SECRET_RE.sub(replace_secret, value)
    return _SK_RE.sub("sk-***", text)


class OpenAISdkJudge:
    """JudgeCall adapter using the official OpenAI Python SDK.

    Works with OpenAI-compatible endpoints via ``base_url``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        if client is not None:
            self._client = client
        else:
            from urllib.parse import urlparse

            import httpx
            from openai import OpenAI

            hostname = (urlparse(self.base_url).hostname or "").lower()
            http_client = httpx.Client(trust_env=hostname not in {"localhost", "127.0.0.1", "::1"})
            self._client = OpenAI(
                api_key=api_key,
                base_url=self.base_url,
                timeout=timeout,
                http_client=http_client,
            )

    def __call__(self, system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            for use_json_mode in (True, False):
                try:
                    kwargs = {
                        "model": self.model,
                        "temperature": 0,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    }
                    if use_json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    response = self._client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content or "{}"
                    payload = json.loads(content)
                    if not isinstance(payload, dict):
                        raise ValueError("judge_response_must_be_object")
                    BlindJudgmentVote.model_validate(payload)
                    return payload
                except Exception as exc:  # pragma: no cover - network boundary
                    last_error = exc
                    text = str(exc).lower()
                    unsupported_json_mode = use_json_mode and any(
                        marker in text for marker in ("response_format", "json mode", "unsupported", "unknown parameter")
                    )
                    if unsupported_json_mode:
                        continue
                    break
            if attempt < 2:
                time.sleep(2**attempt)
        assert last_error is not None
        raise RuntimeError(redact_text(str(last_error))) from last_error
