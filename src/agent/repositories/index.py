from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent.metadata.canonical import CanonicalFile, CanonicalProject


class RepositoryIndex:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    repository TEXT NOT NULL,
                    primary_accession TEXT NOT NULL,
                    native_accession TEXT,
                    px_accession TEXT,
                    title TEXT,
                    description TEXT,
                    publication_date TEXT,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (repository, primary_accession)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    repository TEXT NOT NULL,
                    project_accession TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    logical_path TEXT,
                    file_format TEXT,
                    file_category TEXT,
                    size_bytes INTEGER,
                    transfer_method TEXT,
                    download_urls_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(file_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_px ON projects(px_accession)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_native ON projects(native_accession)")

    def upsert_project(self, project: CanonicalProject) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    repository, primary_accession, native_accession, px_accession,
                    title, description, publication_date, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, primary_accession) DO UPDATE SET
                    native_accession=excluded.native_accession,
                    px_accession=excluded.px_accession,
                    title=excluded.title,
                    description=excluded.description,
                    publication_date=excluded.publication_date,
                    raw_json=excluded.raw_json
                """,
                (
                    project.repository,
                    project.primary_accession,
                    project.native_accession,
                    project.px_accession,
                    project.title,
                    project.description,
                    project.publication_date,
                    json.dumps(project.raw_metadata, ensure_ascii=False),
                ),
            )

    def replace_files(self, repository: str, project_accession: str, files: list[CanonicalFile]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM files WHERE repository=? AND project_accession=?", (repository, project_accession))
            conn.executemany(
                """
                INSERT INTO files (
                    repository, project_accession, file_name, logical_path, file_format,
                    file_category, size_bytes, transfer_method, download_urls_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        file.repository,
                        file.project_accession,
                        file.file_name,
                        file.logical_path,
                        file.file_format,
                        file.file_category,
                        file.size_bytes,
                        file.transfer_method,
                        json.dumps(file.download_urls, ensure_ascii=False),
                        json.dumps(file.raw_record, ensure_ascii=False),
                    )
                    for file in files
                ],
            )

    def get_project(self, repository: str, accession: str) -> CanonicalProject | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT repository, primary_accession, native_accession, px_accession,
                       title, description, publication_date, raw_json
                FROM projects
                WHERE repository=? AND (primary_accession=? OR native_accession=? OR px_accession=?)
                LIMIT 1
                """,
                (repository, accession, accession, accession),
            ).fetchone()
        if row is None:
            return None
        raw = self._load_json(row[7], {})
        return CanonicalProject(
            repository=row[0],
            primary_accession=row[1],
            native_accession=row[2],
            px_accession=row[3],
            title=row[4],
            description=row[5],
            publication_date=row[6],
            raw_metadata=raw,
        )

    def list_files(self, repository: str, project_accession: str) -> list[CanonicalFile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT repository, project_accession, file_name, logical_path, file_format,
                       file_category, size_bytes, transfer_method, download_urls_json, raw_json
                FROM files
                WHERE repository=? AND project_accession=?
                """,
                (repository, project_accession),
            ).fetchall()
        return [self._file_from_row(row) for row in rows]

    def find_files_by_name(self, repository: str, file_name: str) -> list[CanonicalFile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT repository, project_accession, file_name, logical_path, file_format,
                       file_category, size_bytes, transfer_method, download_urls_json, raw_json
                FROM files
                WHERE repository=? AND lower(file_name)=lower(?)
                """,
                (repository, file_name),
            ).fetchall()
        return [self._file_from_row(row) for row in rows]

    def _file_from_row(self, row: tuple[Any, ...]) -> CanonicalFile:
        return CanonicalFile(
            repository=row[0],
            project_accession=row[1],
            file_name=row[2],
            logical_path=row[3],
            file_format=row[4],
            file_category=row[5],
            size_bytes=row[6],
            transfer_method=row[7] or "unknown",
            download_urls=self._load_json(row[8], []),
            raw_record=self._load_json(row[9], {}),
        )

    @staticmethod
    def _load_json(text: str, fallback: Any) -> Any:
        try:
            return json.loads(text) if text else fallback
        except json.JSONDecodeError:
            return fallback
