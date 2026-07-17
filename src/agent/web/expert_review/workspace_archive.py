from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from agent.web.expert_review.openai_judge import redact_text
from agent.web.expert_review.pool_registry import ExpertPoolRegistry


WORKSPACE_SCHEMA_VERSION = "benchmark-review-workspace/v1"
MAX_WORKSPACE_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_WORKSPACE_FILE_BYTES = 64 * 1024 * 1024
MAX_WORKSPACE_FILES = 2048
_ALLOWED_ROOT_FILES = {"registry.json", "pool.blinded.json", "pool.reviewed.json"}
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|password|secret)=)[^&#\s]+"
)
_PROVIDER_KEY_RE = re.compile(
    r"\b(?:xai-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9_-]{8,}|hf_[A-Za-z0-9_-]{8,})\b"
)


class WorkspaceArchiveError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _secret_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return normalized in {"authorization", "privatekey", "clientsecret"} or normalized.endswith(
        ("apikey", "token", "password", "secret", "credential")
    )


def _sanitize_string(value: str) -> str:
    text = redact_text(value)
    text = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1", text)
    text = _SECRET_QUERY_RE.sub(r"\1***", text)
    return _PROVIDER_KEY_RE.sub("[redacted-api-key]", text)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not _secret_key(key)
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(_sanitize(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sanitized_file_bytes(path: Path) -> bytes:
    if path.suffix == ".json":
        try:
            return _json_bytes(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkspaceArchiveError(f"workspace_json_invalid:{path.name}") from exc
    if path.suffix == ".jsonl":
        records: list[bytes] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise WorkspaceArchiveError(f"workspace_jsonl_invalid:{path.name}") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WorkspaceArchiveError(f"workspace_jsonl_invalid:{path.name}") from exc
            records.append(json.dumps(_sanitize(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        return b"\n".join(records) + (b"\n" if records else b"")
    raise WorkspaceArchiveError(f"workspace_file_type_not_allowed:{path.name}")


def _archive_sources(pool_dir: Path) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for name in sorted(_ALLOWED_ROOT_FILES):
        path = pool_dir / name
        if path.is_file() and not path.is_symlink():
            sources.append((f"pool/{name}", path))
    private_key = pool_dir / "private" / "judgment.key.json"
    if private_key.is_file() and not private_key.is_symlink():
        sources.append(("pool/private/judgment.key.json", private_key))
    jobs_dir = pool_dir / "jobs"
    if jobs_dir.is_dir():
        for path in sorted(jobs_dir.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.suffix not in {".json", ".jsonl"}:
                continue
            relative = path.relative_to(jobs_dir).as_posix()
            sources.append((f"pool/jobs/{relative}", path))
    if len(sources) > MAX_WORKSPACE_FILES:
        raise WorkspaceArchiveError("workspace_too_many_files")
    return sources


def _safe_workspace_state(value: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "reviewer_id",
        "candidate_id",
        "task_filter",
        "completion_filter",
        "score_filter",
        "flag_filter",
        "desired_mode",
    }
    return {key: str(value.get(key) or "")[:500] for key in allowed if value.get(key) is not None}


def export_workspace_archive(
    registry: ExpertPoolRegistry,
    pool_id: str,
    *,
    workspace_state: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    record = registry.get_pool(pool_id)
    pool_dir = registry.root / pool_id
    if record is None or not pool_dir.is_dir():
        raise WorkspaceArchiveError("pool_not_found")
    if registry.load_pool_document(pool_id, prefer_reviewed=True) is None:
        raise WorkspaceArchiveError("pool_document_missing")

    entries: list[tuple[str, bytes]] = []
    total_size = 0
    for archive_name, path in _archive_sources(pool_dir):
        if path.stat().st_size > MAX_WORKSPACE_FILE_BYTES:
            raise WorkspaceArchiveError(f"workspace_file_too_large:{archive_name}")
        data = _sanitized_file_bytes(path)
        total_size += len(data)
        if total_size > MAX_WORKSPACE_ARCHIVE_BYTES:
            raise WorkspaceArchiveError("workspace_archive_too_large")
        entries.append((archive_name, data))

    manifest = {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "exported_at": _utc_now(),
        "pool_id": pool_id,
        "label": str(record.get("label") or pool_id),
        "candidate_count": int((record.get("stats") or {}).get("candidate_count") or 0),
        "workspace": _safe_workspace_state(workspace_state),
        "files": [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in entries
        ],
    }
    export_dir = registry.root / ".exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    output = export_dir / f"benchmark-review-{pool_id}-{uuid.uuid4().hex[:8]}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("workspace.json", _json_bytes(manifest))
        for name, data in entries:
            archive.writestr(name, data)
    if output.stat().st_size > MAX_WORKSPACE_ARCHIVE_BYTES:
        output.unlink(missing_ok=True)
        raise WorkspaceArchiveError("workspace_archive_too_large")
    return output, manifest


def _validate_member(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or name.startswith("/")
        or "//" in name
        or any(":" in part or any(ord(char) < 32 for char in part) for part in path.parts)
    ):
        raise WorkspaceArchiveError("workspace_unsafe_path")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise WorkspaceArchiveError("workspace_symlink_not_allowed")
    if info.file_size > MAX_WORKSPACE_FILE_BYTES:
        raise WorkspaceArchiveError(f"workspace_file_too_large:{name}")
    if info.compress_size and info.file_size > 1024 * 1024 and info.file_size / info.compress_size > 1000:
        raise WorkspaceArchiveError("workspace_suspicious_compression_ratio")
    allowed = (
        name == "workspace.json"
        or name in {f"pool/{item}" for item in _ALLOWED_ROOT_FILES}
        or name == "pool/private/judgment.key.json"
        or (
            name.startswith("pool/jobs/")
            and len(path.parts) == 3
            and Path(name).suffix in {".json", ".jsonl"}
        )
    )
    if not allowed:
        raise WorkspaceArchiveError(f"workspace_file_not_allowed:{name}")
    return name


def _read_archive(data: bytes) -> dict[str, bytes]:
    if not data or len(data) > MAX_WORKSPACE_ARCHIVE_BYTES:
        raise WorkspaceArchiveError("workspace_archive_too_large")
    files: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_WORKSPACE_FILES:
                raise WorkspaceArchiveError("workspace_too_many_files")
            for info in infos:
                if info.is_dir():
                    continue
                name = _validate_member(info)
                if name in files:
                    raise WorkspaceArchiveError(f"workspace_duplicate_file:{name}")
                payload = archive.read(info)
                total += len(payload)
                if total > MAX_WORKSPACE_ARCHIVE_BYTES:
                    raise WorkspaceArchiveError("workspace_archive_too_large")
                files[name] = payload
    except zipfile.BadZipFile as exc:
        raise WorkspaceArchiveError("workspace_zip_invalid") from exc
    return files


def _load_json(files: Mapping[str, bytes], name: str, *, required: bool = True) -> Any:
    payload = files.get(name)
    if payload is None:
        if required:
            raise WorkspaceArchiveError(f"workspace_file_missing:{name}")
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceArchiveError(f"workspace_json_invalid:{name}") from exc


def _candidate_ids(pool: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("candidate_id") or "")
        for item in (pool.get("candidates") or [])
        if isinstance(item, Mapping) and str(item.get("candidate_id") or "")
    }


def _unique_job_id(root: Path, job_id: str, reserved: set[str]) -> str:
    candidate = job_id
    while candidate in reserved or any(
        (pool_dir / "jobs" / f"{candidate}.json").exists()
        for pool_dir in root.iterdir()
        if pool_dir.is_dir()
    ):
        candidate = f"{job_id}-import-{uuid.uuid4().hex[:6]}"
    reserved.add(candidate)
    return candidate


def _rewrite_imported(
    value: Any,
    *,
    old_pool_id: str,
    new_pool_id: str,
    job_ids: Mapping[str, str],
    field_name: str = "",
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _rewrite_imported(
                item,
                old_pool_id=old_pool_id,
                new_pool_id=new_pool_id,
                job_ids=job_ids,
                field_name=str(key),
            )
            for key, item in value.items()
            if not _secret_key(key)
        }
    if isinstance(value, list):
        return [
            _rewrite_imported(
                item,
                old_pool_id=old_pool_id,
                new_pool_id=new_pool_id,
                job_ids=job_ids,
                field_name=field_name,
            )
            for item in value
        ]
    if isinstance(value, str):
        text = _sanitize_string(value)
        if field_name == "pool_id" and text == old_pool_id:
            return new_pool_id
        if field_name in {"job_id", "source_job_id", "parent_job_id"}:
            return job_ids.get(text, text)
        if field_name.endswith("_path") or field_name == "path":
            if old_pool_id:
                text = text.replace(old_pool_id, new_pool_id)
            for old, new in job_ids.items():
                text = text.replace(old, new)
        return text
    return value


def import_workspace_archive(
    registry: ExpertPoolRegistry,
    data: bytes,
) -> tuple[dict[str, Any], dict[str, str], int]:
    files = _read_archive(data)
    manifest = _load_json(files, "workspace.json")
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != WORKSPACE_SCHEMA_VERSION:
        raise WorkspaceArchiveError("workspace_schema_unsupported")
    listed = {
        str(item.get("path") or ""): item
        for item in (manifest.get("files") or [])
        if isinstance(item, Mapping)
    }
    actual_names = set(files) - {"workspace.json"}
    if set(listed) != actual_names:
        raise WorkspaceArchiveError("workspace_manifest_file_mismatch")
    for name in actual_names:
        expected = listed[name]
        payload = files[name]
        if int(expected.get("size") or -1) != len(payload) or str(expected.get("sha256") or "") != hashlib.sha256(payload).hexdigest():
            raise WorkspaceArchiveError(f"workspace_checksum_mismatch:{name}")

    archived_registry = _load_json(files, "pool/registry.json", required=False)
    reviewed_raw = _load_json(files, "pool/pool.reviewed.json", required=False)
    blinded_raw = _load_json(files, "pool/pool.blinded.json", required=False)
    try:
        reviewed = registry._validate_pool(reviewed_raw) if isinstance(reviewed_raw, Mapping) else None
        blinded = registry._validate_pool(blinded_raw) if isinstance(blinded_raw, Mapping) else None
    except ValueError as exc:
        raise WorkspaceArchiveError(str(exc)) from exc
    if reviewed is not None and blinded is not None and _candidate_ids(reviewed) != _candidate_ids(blinded):
        raise WorkspaceArchiveError("workspace_pool_versions_candidate_mismatch")
    pool = reviewed if reviewed is not None else blinded
    if not isinstance(pool, Mapping):
        raise WorkspaceArchiveError("workspace_pool_missing")
    pool_ids = _candidate_ids(pool)
    if not pool_ids:
        raise WorkspaceArchiveError("workspace_pool_has_no_candidates")
    private_key = _load_json(files, "pool/private/judgment.key.json", required=False)
    if isinstance(private_key, Mapping):
        private_ids = _candidate_ids(private_key)
        if private_ids != pool_ids:
            raise WorkspaceArchiveError("workspace_private_key_candidate_mismatch")

    old_pool_id = str(manifest.get("pool_id") or "workspace")
    label = str(manifest.get("label") or old_pool_id)
    record: dict[str, Any] | None = None
    try:
        record = registry.import_pool(pool, label=label, pool_id=old_pool_id)
        new_pool_id = str(record["pool_id"])
        pool_dir = registry.root / new_pool_id
        if blinded is not None:
            registry._write_json_atomic(pool_dir / "pool.blinded.json", _sanitize(blinded))
        if reviewed is not None:
            registry._write_json_atomic(pool_dir / "pool.reviewed.json", _sanitize(reviewed))
        if isinstance(private_key, Mapping):
            registry._write_json_atomic(pool_dir / "private" / "judgment.key.json", _sanitize(private_key))

        job_payloads: dict[str, Any] = {}
        for name in sorted(actual_names):
            if not name.startswith("pool/jobs/") or not name.endswith(".json"):
                continue
            payload = _load_json(files, name)
            relative = name.removeprefix("pool/jobs/")
            job_id = str(payload.get("job_id") or "") if isinstance(payload, Mapping) else ""
            if job_id and relative == f"{job_id}.json":
                if not _SAFE_JOB_ID_RE.fullmatch(job_id):
                    raise WorkspaceArchiveError("workspace_job_id_invalid")
                job_payloads[name] = payload
        reserved_job_ids: set[str] = set()
        job_ids = {
            str(payload.get("job_id")): _unique_job_id(
                registry.root,
                str(payload.get("job_id")),
                reserved_job_ids,
            )
            for payload in job_payloads.values()
        }
        written_destinations: set[Path] = set()
        for name in sorted(actual_names):
            if not name.startswith("pool/jobs/"):
                continue
            relative = name.removeprefix("pool/jobs/")
            original_job_id = next(
                (
                    old
                    for old in sorted(job_ids, key=len, reverse=True)
                    if relative == old or relative.startswith(f"{old}.")
                ),
                None,
            )
            if original_job_id is None:
                raise WorkspaceArchiveError(f"workspace_job_artifact_unmatched:{relative}")
            mapped_job_id = job_ids[original_job_id]
            relative = f"{mapped_job_id}{relative[len(original_job_id):]}"
            jobs_root = (pool_dir / "jobs").resolve()
            destination = (jobs_root / relative).resolve()
            if not destination.is_relative_to(jobs_root) or destination in written_destinations:
                raise WorkspaceArchiveError("workspace_unsafe_path")
            written_destinations.add(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".json"):
                payload = _rewrite_imported(
                    _load_json(files, name),
                    old_pool_id=old_pool_id,
                    new_pool_id=new_pool_id,
                    job_ids=job_ids,
                )
                if name in job_payloads and isinstance(payload, dict):
                    payload["pool_id"] = new_pool_id
                    payload["job_id"] = mapped_job_id
                    if str(payload.get("status") or "") in {"queued", "running"}:
                        payload["status"] = "cancelled"
                        payload["cancel_requested"] = True
                        payload["finished_at"] = payload.get("finished_at") or _utc_now()
                        payload["error"] = "imported_nonterminal_job_requires_manual_resume"
                        payload["items"] = {
                            str(candidate_id): "pending" if status == "running" else status
                            for candidate_id, status in (payload.get("items") or {}).items()
                        }
                        logs = list(payload.get("logs") or [])
                        logs.append(
                            {
                                "ts": _utc_now(),
                                "level": "warning",
                                "message": "Imported non-terminal job was cancelled to prevent automatic external model calls; resume manually if needed.",
                            }
                        )
                        payload["logs"] = logs
                destination.write_bytes(_json_bytes(payload))
            else:
                output: list[str] = []
                for line in files[name].decode("utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise WorkspaceArchiveError(f"workspace_jsonl_invalid:{name}") from exc
                    output.append(
                        json.dumps(
                            _rewrite_imported(
                                payload,
                                old_pool_id=old_pool_id,
                                new_pool_id=new_pool_id,
                                job_ids=job_ids,
                            ),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                destination.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
        restored_record = dict(record)
        if isinstance(archived_registry, Mapping):
            for key in ("created_at", "updated_at", "judgment_source", "schema_version"):
                if archived_registry.get(key) is not None:
                    restored_record[key] = _sanitize_string(str(archived_registry.get(key) or ""))
            if isinstance(archived_registry.get("tags"), list):
                restored_record["tags"] = [
                    _sanitize_string(str(item)) for item in archived_registry.get("tags") or []
                ]
        restored_record["pool_id"] = new_pool_id
        restored_record["label"] = label
        restored_record["paths"] = {
            "blinded": "pool.blinded.json" if (pool_dir / "pool.blinded.json").is_file() else None,
            "reviewed": "pool.reviewed.json" if (pool_dir / "pool.reviewed.json").is_file() else None,
        }
        restored_record["stats"] = registry._compute_stats(pool)
        restored_record["archive_origin"] = {
            "pool_id": old_pool_id,
            "exported_at": str(manifest.get("exported_at") or ""),
        }
        registry._write_record(new_pool_id, restored_record)
        record = restored_record
        workspace = _safe_workspace_state(manifest.get("workspace") if isinstance(manifest.get("workspace"), Mapping) else None)
        return record, workspace, len(job_ids)
    except Exception as exc:
        if record is not None:
            target = (registry.root / str(record.get("pool_id") or "")).resolve()
            root = registry.root.resolve()
            if target.parent == root and target.is_dir():
                shutil.rmtree(target)
        if isinstance(exc, WorkspaceArchiveError):
            raise
        if isinstance(exc, (OSError, UnicodeError, ValueError, json.JSONDecodeError)):
            raise WorkspaceArchiveError(str(exc) or "workspace_import_failed") from exc
        raise WorkspaceArchiveError("workspace_import_failed") from exc
