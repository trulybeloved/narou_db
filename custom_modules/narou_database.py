"""SQLite persistence for the Narou corpus.

The committed database is the application's canonical read model.  Raw scrape
artifacts remain in the repository as provenance and bootstrap input.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable


DATABASE_PATH = Path("datastores/narou.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chapters (
    chapter_uid INTEGER PRIMARY KEY,
    narou_link TEXT NOT NULL UNIQUE,
    chapter_title TEXT NOT NULL,
    chapter_id TEXT,
    chapter_type TEXT,
    arc_number TEXT,
    chapter_number TEXT,
    jp_chapter_name TEXT,
    chapter_body TEXT NOT NULL,
    publication_timestamp INTEGER,
    chapter_edited INTEGER NOT NULL DEFAULT 0 CHECK (chapter_edited IN (0, 1)),
    edit_timestamp INTEGER,
    scraped_timestamp INTEGER,
    content_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chapters_chapter_id ON chapters(chapter_id);
CREATE INDEX IF NOT EXISTS idx_chapters_publication_timestamp ON chapters(publication_timestamp);
CREATE INDEX IF NOT EXISTS idx_chapters_edit_timestamp ON chapters(edit_timestamp);
"""

INDEX_COLUMNS = (
    "chapter_uid", "chapter_title", "narou_link", "publication_timestamp",
    "chapter_edited", "edit_timestamp", "scraped_timestamp",
)


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def connect(path: Path | str = DATABASE_PATH, *, writable: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if not writable and not path.exists():
        raise FileNotFoundError(f"Canonical database does not exist: {path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if writable:
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def get_index(path: Path | str = DATABASE_PATH) -> list[dict]:
    connection = connect(path)
    try:
        rows = connection.execute(
            "SELECT chapter_uid, chapter_title, narou_link, publication_timestamp, "
            "chapter_edited, edit_timestamp, scraped_timestamp "
            "FROM chapters ORDER BY chapter_uid"
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def upsert_chapter(connection: sqlite3.Connection, index_entry: dict, chapter_entry: dict) -> None:
    """Insert or replace a complete chapter while preserving no remote state."""
    body = chapter_entry["chapter_body"]
    record = {
        "chapter_uid": int(index_entry["chapter_uid"]),
        "narou_link": index_entry["narou_link"],
        "chapter_title": chapter_entry.get("chapter_title") or index_entry["chapter_title"],
        "chapter_id": chapter_entry.get("chapter_id"),
        "chapter_type": chapter_entry.get("chapter_type"),
        "arc_number": chapter_entry.get("arc_number"),
        "chapter_number": chapter_entry.get("chapter_number"),
        "jp_chapter_name": chapter_entry.get("jp_chapter_name"),
        "chapter_body": body,
        "publication_timestamp": index_entry.get("publication_timestamp"),
        "chapter_edited": int(bool(index_entry.get("chapter_edited", 0))),
        "edit_timestamp": index_entry.get("edit_timestamp"),
        "scraped_timestamp": chapter_entry.get("scraped_timestamp", index_entry.get("scraped_timestamp")),
        "content_sha256": content_hash(body),
    }
    columns = ", ".join(record)
    values = ", ".join(f":{key}" for key in record)
    updates = ", ".join(f"{key}=excluded.{key}" for key in record if key != "chapter_uid")
    connection.execute(
        f"INSERT INTO chapters ({columns}) VALUES ({values}) "
        f"ON CONFLICT(chapter_uid) DO UPDATE SET {updates}",
        record,
    )


def validate(connection: sqlite3.Connection, *, expected_uids: Iterable[int] | None = None) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ValueError(f"SQLite integrity check failed: {integrity}")
    # SQLite has no portable SHA-256 builtin; verify hashes in Python.
    for row in connection.execute("SELECT chapter_uid, chapter_body, content_sha256 FROM chapters"):
        if content_hash(row["chapter_body"]) != row["content_sha256"]:
            raise ValueError(f"Content hash mismatch for chapter {row['chapter_uid']}")
    if expected_uids is not None:
        expected = set(expected_uids)
        actual = {row[0] for row in connection.execute("SELECT chapter_uid FROM chapters")}
        if actual != expected:
            raise ValueError(f"Database UID set does not match source (missing={sorted(expected - actual)}, extra={sorted(actual - expected)})")


def finalize(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("VACUUM")
    connection.commit()


def atomic_update(path: Path | str, update: Callable[[sqlite3.Connection], None]) -> None:
    """Apply an update to a temporary copy and replace the canonical file on success."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Bootstrap the canonical database first: {path}")
    with tempfile.TemporaryDirectory(dir=path.parent) as temp_dir:
        temporary = Path(temp_dir) / path.name
        source = connect(path)
        target = connect(temporary, writable=True)
        try:
            source.backup(target)
            initialize(target)
            try:
                target.execute("BEGIN IMMEDIATE")
                update(target)
                validate(target)
                target.commit()
            except Exception:
                target.rollback()
                raise
            finalize(target)
        finally:
            target.close()
            source.close()
        os.replace(temporary, path)
