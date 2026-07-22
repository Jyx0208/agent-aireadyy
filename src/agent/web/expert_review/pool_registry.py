from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_REGISTRY_LOCK = threading.RLock()
_POOL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
_LEAKY_FIELDS = (
    "project_accession",
    "accession",
    "source_system",
    "runtime",
    "runtime_label",
    "system_name",
    "agent_runtime",
    "workflow_name",
    "generator",
    "generator_model",
    "generator_model_id",
    "generator_provider",
    "generator_requested_model",
    "generator_resolved_model",
    "generator_model_family",
    "generator_runtime",
    "candidate_generation_identity",
)


def expert_review_enabled() -> bool:
    raw = (os.getenv("AGENT_EXPERT_REVIEW_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def expert_review_root() -> Path:
    configured = (os.getenv("AGENT_EXPERT_REVIEW_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # Do not anchor durable review data to the process working directory. The
    # desktop sandbox can start uvicorn from a temporary mapped directory that
    # disappears after the session, while this source checkout remains stable.
    return Path(__file__).resolve().parents[4] / "runs" / "expert_review"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(label: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (label or "").strip()).strip("-._")
    return cleaned[:64] or uuid.uuid4().hex[:10]


_EXPERT_HIDDEN_FIELDS = (
    "grade",
    "review_notes",
    "reviewer_id",
    "human_grades",
    "machine_reviews",
    "machine_review_runs",
    "model_expert_judgments",
    "model_expert_consensus",
    "judgment_confidence",
    "review_model",
    "confidence",
    "judgment_source",
    "review_method",
    "rubric_version",
    "calibration_features",
)


def _strip_leaky_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _strip_leaky_fields(item)
            for key, item in value.items()
            if key not in _LEAKY_FIELDS
        }
    if isinstance(value, list):
        return [_strip_leaky_fields(item) for item in value]
    return value


def blind_candidate_view(
    candidate: Mapping[str, Any],
    *,
    mode: str = "expert",
    reviewer_id: str = "",
) -> dict[str, Any]:
    """Return a candidate dict safe for the given review mode and reviewer."""
    item = _strip_leaky_fields(dict(candidate))
    if mode == "expert":
        own_review = None
        for entry in reversed(candidate.get("human_grades") or []):
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("reviewer_id") or "") != reviewer_id:
                continue
            own_review = entry
            break
        for field in _EXPERT_HIDDEN_FIELDS:
            item.pop(field, None)
        if own_review is not None and not own_review.get("cleared") and own_review.get("grade") is not None:
            item["grade"] = own_review.get("grade")
            item["review_notes"] = str(own_review.get("notes") or "")
            item["reviewer_id"] = reviewer_id
    return item


def strip_pool_for_mode(pool: Mapping[str, Any], *, mode: str = "expert") -> dict[str, Any]:
    payload = _strip_leaky_fields(dict(pool))
    candidates = [
        blind_candidate_view(item, mode=mode)
        for item in (payload.get("candidates") or [])
        if isinstance(item, dict)
    ]
    payload["candidates"] = candidates
    if mode == "expert":
        payload["tasks"] = _strip_leaky_fields(payload.get("tasks") or {})
        payload.pop("tags", None)
        payload.pop("judgment_source", None)
        payload.pop("review_summary", None)
    return payload


class ExpertPoolRegistry:
    """Filesystem-backed registry of blind / reviewed judgment pools."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else expert_review_root()

    def list_pools(self) -> list[dict[str, Any]]:
        with _REGISTRY_LOCK:
            if not self.root.exists():
                return []
            records: list[dict[str, Any]] = []
            for path in sorted(self.root.iterdir()):
                if not path.is_dir():
                    continue
                record = self._load_record(path.name)
                if record is not None:
                    records.append(record)
            records.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return records

    def get_pool(self, pool_id: str) -> dict[str, Any] | None:
        with _REGISTRY_LOCK:
            return self._load_record(pool_id)

    def load_pool_document(self, pool_id: str, *, prefer_reviewed: bool = True) -> dict[str, Any] | None:
        with _REGISTRY_LOCK:
            record = self._load_record(pool_id)
            if record is None:
                return None
            pool_dir = self.root / pool_id
            reviewed = pool_dir / "pool.reviewed.json"
            blinded = pool_dir / "pool.blinded.json"
            path = reviewed if prefer_reviewed and reviewed.exists() else blinded
            if not path.exists():
                path = blinded if blinded.exists() else reviewed
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
            if not isinstance(payload, dict):
                return None
            return payload

    def load_private_key(self, pool_id: str) -> dict[str, Any] | None:
        with _REGISTRY_LOCK:
            if self._load_record(pool_id) is None:
                return None
            path = self.root / pool_id / "private" / "judgment.key.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

    def import_generated_pool(
        self,
        pool: Mapping[str, Any],
        *,
        private_key: Mapping[str, Any],
        label: str | None = None,
        pool_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically register a generated blinded pool and its private key."""
        validated = self._validate_pool(pool)
        if self._looks_reviewed(validated):
            raise ValueError("generated_pool_must_be_blinded")
        if not isinstance(private_key, Mapping) or not isinstance(private_key.get("candidates"), list):
            raise ValueError("generated_pool_requires_private_key")
        with _REGISTRY_LOCK:
            resolved_id = self._allocate_pool_id(pool_id or label or "prompt-pool")
            pool_dir = self.root / resolved_id
            private_dir = pool_dir / "private"
            pool_dir.mkdir(parents=True, exist_ok=True)
            private_dir.mkdir(parents=True, exist_ok=True)
            now = _utc_now()
            record = {
                "pool_id": resolved_id,
                "label": (label or "Prompt review pool").strip() or "Prompt review pool",
                "created_at": now,
                "updated_at": now,
                "paths": {"blinded": "pool.blinded.json", "reviewed": None},
                "stats": self._compute_stats(validated),
                "tags": ["prompt_generated"],
                "schema_version": str(validated.get("schema_version") or ""),
                "judgment_source": "",
            }
            self._write_json_atomic(pool_dir / "pool.blinded.json", validated)
            self._write_json_atomic(private_dir / "judgment.key.json", dict(private_key))
            self._write_json_atomic(pool_dir / "registry.json", record)
            return record

    def import_pool(
        self,
        pool: Mapping[str, Any],
        *,
        label: str | None = None,
        pool_id: str | None = None,
    ) -> dict[str, Any]:
        validated = self._validate_pool(pool)
        with _REGISTRY_LOCK:
            resolved_id = self._allocate_pool_id(pool_id or label or "pool")
            pool_dir = self.root / resolved_id
            pool_dir.mkdir(parents=True, exist_ok=True)
            is_reviewed = self._looks_reviewed(validated)
            target_name = "pool.reviewed.json" if is_reviewed else "pool.blinded.json"
            target = pool_dir / target_name
            target.write_text(
                json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # Always keep a blinded snapshot for expert-safe reloads when importing reviewed.
            if is_reviewed:
                blinded = self._as_blinded_snapshot(validated)
                (pool_dir / "pool.blinded.json").write_text(
                    json.dumps(blinded, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            now = _utc_now()
            stats = self._compute_stats(validated)
            record = {
                "pool_id": resolved_id,
                "label": (label or str(validated.get("label") or resolved_id)).strip() or resolved_id,
                "created_at": now,
                "updated_at": now,
                "paths": {
                    "blinded": "pool.blinded.json",
                    "reviewed": "pool.reviewed.json" if is_reviewed else None,
                },
                "stats": stats,
                "tags": list(validated.get("tags") or []) if isinstance(validated.get("tags"), list) else [],
                "schema_version": str(validated.get("schema_version") or ""),
                "judgment_source": str(validated.get("judgment_source") or ""),
            }
            self._write_record(resolved_id, record)
            return record

    def candidates(
        self,
        pool_id: str,
        *,
        mode: str = "expert",
        reviewer_id: str = "",
        task: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        document = self.load_pool_document(pool_id, prefer_reviewed=True)
        if document is None:
            return None
        mode = mode if mode in {"expert", "developer", "test"} else "expert"
        all_candidates = [
            blind_candidate_view(item, mode=mode, reviewer_id=reviewer_id)
            for item in (document.get("candidates") or [])
            if isinstance(item, dict)
        ]
        if task and task != "all":
            filtered = [
                item
                for item in all_candidates
                if f"{item.get('scenario_id')}:{item.get('variant_id')}" == task
            ]
        else:
            filtered = all_candidates
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        page = filtered[offset : offset + limit]
        record = self.get_pool(pool_id) or {}
        return {
            "pool_id": pool_id,
            "label": record.get("label") or pool_id,
            "mode": mode,
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "tasks": (
                _strip_leaky_fields(document.get("tasks") or {})
                if mode == "expert"
                else document.get("tasks") if isinstance(document.get("tasks"), dict) else {}
            ),
            "candidates": page,
            "stats": (
                {"candidate_count": len(filtered)}
                if mode == "expert"
                else record.get("stats") or self._compute_stats(document)
            ),
        }

    def save_reviewed_pool(self, pool_id: str, pool: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a reviewed pool document and refresh registry stats."""
        validated = self._validate_pool(pool)
        with _REGISTRY_LOCK:
            record = self._load_record(pool_id)
            if record is None:
                raise ValueError("pool_not_found")
            pool_dir = self.root / pool_id
            pool_dir.mkdir(parents=True, exist_ok=True)
            path = pool_dir / "pool.reviewed.json"
            path.write_text(
                json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            record = dict(record)
            record["updated_at"] = _utc_now()
            paths = dict(record.get("paths") or {})
            paths["reviewed"] = "pool.reviewed.json"
            if not paths.get("blinded") and (pool_dir / "pool.blinded.json").exists():
                paths["blinded"] = "pool.blinded.json"
            record["paths"] = paths
            record["stats"] = self._compute_stats(validated)
            record["judgment_source"] = str(validated.get("judgment_source") or "")
            record["schema_version"] = str(validated.get("schema_version") or "")
            self._write_record(pool_id, record)
            return record

    def mutate_reviewed_pool(
        self,
        pool_id: str,
        mutator: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically load, mutate, validate, and persist a reviewed pool."""
        with _REGISTRY_LOCK:
            document = self.load_pool_document(pool_id, prefer_reviewed=True)
            if document is None:
                raise ValueError("pool_not_found")
            mutated = mutator(dict(document))
            if not isinstance(mutated, Mapping):
                raise ValueError("pool_mutator_must_return_object")
            record = self.save_reviewed_pool(pool_id, mutated)
            return dict(mutated), record

    def _allocate_pool_id(self, seed: str) -> str:
        base = _slugify(seed)
        if not _POOL_ID_RE.match(base):
            base = uuid.uuid4().hex[:12]
        candidate = base
        index = 2
        while (self.root / candidate).exists():
            candidate = f"{base}-{index}"
            index += 1
            if index > 1000:
                candidate = f"{base}-{uuid.uuid4().hex[:6]}"
                break
        return candidate

    def _load_record(self, pool_id: str) -> dict[str, Any] | None:
        pool_id = str(pool_id or "").strip()
        if not pool_id or not _POOL_ID_RE.match(pool_id):
            return None
        path = self.root / pool_id / "registry.json"
        if not path.exists():
            # Recover a record from pool files if registry.json is missing.
            pool_dir = self.root / pool_id
            if not pool_dir.is_dir():
                return None
            document = None
            for name in ("pool.reviewed.json", "pool.blinded.json"):
                candidate = pool_dir / name
                if candidate.exists():
                    try:
                        payload = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict):
                        document = payload
                        break
            if document is None:
                return None
            record = {
                "pool_id": pool_id,
                "label": pool_id,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "paths": {
                    "blinded": "pool.blinded.json" if (pool_dir / "pool.blinded.json").exists() else None,
                    "reviewed": "pool.reviewed.json" if (pool_dir / "pool.reviewed.json").exists() else None,
                },
                "stats": self._compute_stats(document),
                "tags": [],
            }
            self._write_record(pool_id, record)
            return record
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _write_record(self, pool_id: str, record: Mapping[str, Any]) -> None:
        pool_dir = self.root / pool_id
        pool_dir.mkdir(parents=True, exist_ok=True)
        path = pool_dir / "registry.json"
        self._write_json_atomic(path, record)

    @staticmethod
    def _validate_pool(pool: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(pool, Mapping):
            raise ValueError("pool_must_be_object")
        candidates = pool.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("pool_requires_candidates")
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                raise ValueError(f"candidate_{index}_must_be_object")
            candidate_id = str(item.get("candidate_id") or "").strip()
            if not candidate_id:
                raise ValueError(f"candidate_{index}_missing_candidate_id")
            if candidate_id in seen_ids:
                raise ValueError(f"duplicate_candidate_id:{candidate_id}")
            seen_ids.add(candidate_id)
            normalized.append(dict(item))
        payload = dict(pool)
        payload["candidates"] = normalized
        return payload

    @staticmethod
    def _looks_reviewed(pool: Mapping[str, Any]) -> bool:
        schema = str(pool.get("schema_version") or "")
        if "reviewed" in schema:
            return True
        if pool.get("judgment_source") or pool.get("review_summary"):
            return True
        for item in pool.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            if (
                item.get("machine_reviews")
                or item.get("model_expert_judgments")
                or item.get("model_expert_consensus")
                or item.get("grade") is not None
            ):
                return True
        return False

    @staticmethod
    def _as_blinded_snapshot(pool: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(pool)
        payload.pop("review_summary", None)
        payload.pop("judgment_source", None)
        candidates = []
        for item in payload.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            candidates.append(blind_candidate_view(item, mode="expert"))
        payload["candidates"] = candidates
        if "reviewed" in str(payload.get("schema_version") or ""):
            payload["schema_version"] = "discovery-judgment-pool-blinded/v2"
        return payload

    @staticmethod
    def _compute_stats(pool: Mapping[str, Any]) -> dict[str, int]:
        candidates = [item for item in (pool.get("candidates") or []) if isinstance(item, dict)]
        graded_human = 0
        graded_machine = 0
        low_confidence = 0
        for item in candidates:
            if item.get("grade") is not None and (
                item.get("human_grades")
                or str(item.get("judgment_source") or "") == "human_verified"
            ):
                graded_human += 1
            elif (
                item.get("grade") is not None
                or item.get("machine_reviews")
                or item.get("model_expert_judgments")
            ):
                graded_machine += 1
            confidence = str(item.get("judgment_confidence") or item.get("confidence") or "")
            if confidence == "low":
                low_confidence += 1
        return {
            "candidate_count": len(candidates),
            "graded_human": graded_human,
            "graded_machine": graded_machine,
            "low_confidence": low_confidence,
        }
