from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from agent.discovery.blind_judging import BlindJudgmentVote


_SECRET_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)|(api[_-]?key\s*[:=]\s*)([^\s,;]+)|(bearer\s+)([^\s,;]+)"
)
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")
_JSON_SECRET_RE = re.compile(
    r'(?i)(["\'](?:api[_-]?key|authorization|token)["\']\s*:\s*["\'])([^"\']+)(["\'])'
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


def redact_text(value: str) -> str:
    def replace_secret(match: re.Match[str]) -> str:
        prefix = match.group(1) or match.group(3) or match.group(5) or ""
        return f"{prefix}***"

    text = _SECRET_RE.sub(replace_secret, value)
    text = _JSON_SECRET_RE.sub(r"\1***\3", text)
    return _SK_RE.sub("sk-***", text)


def parse_json_object_response(content: Any) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise ValueError("judge_response_empty")

    candidates = [text]
    fenced = _JSON_FENCE_RE.fullmatch(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, Mapping):
            return dict(payload)

        if candidate.startswith("<"):
            continue
        extracted: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(candidate):
            index = candidate.find("{", cursor)
            if index < 0:
                break
            try:
                payload, end = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                cursor = index + 1
                continue
            if isinstance(payload, Mapping):
                extracted.append(dict(payload))
            cursor = index + max(end, 1)
        if len(extracted) == 1:
            return extracted[0]
        if len(extracted) > 1:
            raise ValueError(f"judge_response_ambiguous_json; objects={len(extracted)}")

    if text.startswith("```"):
        shape = "markdown_fence"
    elif text.startswith("<"):
        shape = "html"
    elif text.startswith("{"):
        shape = "json_like"
    else:
        shape = "text"
    raise ValueError(f"judge_response_invalid_json; shape={shape}; length={len(text)}")


def validation_error_diagnostic(error: ValidationError) -> str:
    issues = error.errors(include_url=False, include_context=False, include_input=False)
    details = []
    for issue in issues[:8]:
        location = ".".join(str(part) for part in issue.get("loc") or ()) or "response"
        details.append(f"{location}:{issue.get('type') or 'invalid'}")
    suffix = ",".join(details) or "response:invalid"
    return f"judge_response_schema_invalid; issues={len(issues)}; details={suffix}"


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
        json_mode_enabled = True
        for attempt in range(3):
            modes = (True, False) if json_mode_enabled else (False,)
            for use_json_mode in modes:
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
                    content = response.choices[0].message.content
                    payload = parse_json_object_response(content)
                    try:
                        BlindJudgmentVote.model_validate(payload)
                    except ValidationError as exc:
                        raise ValueError(validation_error_diagnostic(exc)) from exc
                    return payload
                except Exception as exc:  # pragma: no cover - network boundary
                    if isinstance(exc, json.JSONDecodeError):
                        last_error = ValueError("judge_transport_invalid_json; stage=transport")
                        if use_json_mode:
                            json_mode_enabled = False
                            continue
                        break
                    last_error = exc
                    text = str(exc).lower()
                    unsupported_json_mode = use_json_mode and any(
                        marker in text for marker in ("response_format", "json mode", "unsupported", "unknown parameter")
                    )
                    if unsupported_json_mode:
                        json_mode_enabled = False
                        continue
                    break
            if attempt < 2:
                time.sleep(2**attempt)
        assert last_error is not None
        raise RuntimeError(redact_text(str(last_error))) from last_error
