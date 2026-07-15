from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from agent.discovery.blind_judging import BlindJudgmentVote


_SECRET_RE = re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*([^\s,;]+)")
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")


def redact_text(value: str) -> str:
    text = _SECRET_RE.sub(r"\1=***", value)
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
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=timeout)

    def __call__(self, system_prompt: str, user_prompt: str) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                content = response.choices[0].message.content or "{}"
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError("judge_response_must_be_object")
                # Validate shape early for clearer errors.
                BlindJudgmentVote.model_validate(payload)
                return payload
            except Exception as exc:  # pragma: no cover - network boundary
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        assert last_error is not None
        raise RuntimeError(redact_text(str(last_error))) from last_error
