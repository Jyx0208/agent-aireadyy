from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from agent.operations.config import OperationsSettings


def sqlite_url(path: Path) -> str:
    resolved = path.resolve().as_posix()
    return f"sqlite:///{quote(resolved, safe='/:')}"


def _configure_sqlite(connection, _record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA temp_store = MEMORY")
    finally:
        cursor.close()


class OperationsDatabase:
    def __init__(self, settings: OperationsSettings | None = None) -> None:
        self.settings = settings or OperationsSettings.from_environment()
        self.settings.ensure_directories()
        self.engine: Engine = create_engine(
            sqlite_url(self.settings.database_path),
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )
        event.listen(self.engine, "connect", _configure_sqlite)
        self._session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    def migrate(self) -> None:
        config = Config()
        config.set_main_option(
            "script_location",
            str(Path(__file__).with_name("migrations")),
        )
        config.set_main_option("sqlalchemy.url", sqlite_url(self.settings.database_path))
        config.attributes["connection"] = self.engine
        command.upgrade(config, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._session_factory() as session:
            yield session

    def new_session(self) -> Session:
        return self._session_factory()

    def dispose(self) -> None:
        self.engine.dispose()
