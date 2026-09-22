"""Build the canonical SQLite database from the Git-tracked provenance corpus."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from custom_modules.narou_database import DATABASE_PATH, connect, finalize, initialize, upsert_chapter, validate
from custom_modules.narou_parser import parse_chapter_title, generate_chapter_id, parse_narou_index_html


ROOT = Path(__file__).resolve().parent
CHAPTERS_DIR = ROOT / "datastores" / "chapters"
RAW_TEXT_DIR = ROOT / "datastores" / "chapter_raws_txt"
INDEX_SCRAPE_FILE = ROOT / "datastores" / "index_scrape_results.json"


def _text_from_html(value: str | None) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text()


def _parse_title(title: str) -> dict:
    try:
        parsed = parse_chapter_title(title)
        parsed["chapter_id"] = generate_chapter_id(parsed)
        return parsed
    except (IndexError, KeyError, TypeError):
        return {"chapter_id": None, "chapter_type": None, "arc_number": None,
                "chapter_number": None, "jp_chapter_name": None}


def load_index_metadata() -> tuple[dict[int, dict], list[str]]:
    """Extract tracked publication/edit metadata without contacting Narou or D1."""
    if not INDEX_SCRAPE_FILE.exists():
        return {}, []
    try:
        pages = json.loads(INDEX_SCRAPE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, [f"{INDEX_SCRAPE_FILE}: {exc}"]
    metadata: dict[int, dict] = {}
    errors: list[str] = []
    for position, page in enumerate(pages):
        try:
            html = page["scrape_results"].get(".p-eplist")
            if not html:
                continue
            for entry in parse_narou_index_html(html, '.p-eplist__sublist', '.p-eplist__update'):
                uid = int(entry['chapter_uid'])
                if uid in metadata:
                    raise ValueError(f"duplicate UID {uid}")
                entry['scraped_timestamp'] = None
                metadata[uid] = entry
        except Exception as exc:
            errors.append(f"{INDEX_SCRAPE_FILE} page {position}: {exc}")
    return metadata, errors


def load_sources() -> tuple[dict[int, tuple[dict, dict]], list[str]]:
    records: dict[int, tuple[dict, dict]] = {}
    errors: list[str] = []
    for file_path in sorted(CHAPTERS_DIR.glob("*.json")):
        try:
            source = json.loads(file_path.read_text(encoding="utf-8"))
            uid_match = re.search(r"/(\d+)/?$", source["scraped_url"])
            if not uid_match:
                raise ValueError("URL has no numeric chapter UID")
            uid = int(uid_match.group(1))
            scraped = source["scrape_results"]
            title = _text_from_html(scraped.get(".p-novel__title") or scraped.get(".novel_subtitle")).strip()
            body_html = scraped.get(".p-novel__body") or scraped.get("#novel_honbun")
            body = _text_from_html(body_html)
            if not title or not body.strip():
                raise ValueError("missing title or body")
            if uid in records:
                raise ValueError(f"duplicate UID {uid}")
            parsed = _parse_title(title)
            index = {"chapter_uid": uid, "chapter_title": title, "narou_link": source["scraped_url"],
                     "publication_timestamp": None, "chapter_edited": 0, "edit_timestamp": None,
                     "scraped_timestamp": None}
            chapter = {"chapter_title": title, "chapter_body": body, **parsed, "scraped_timestamp": None}
            records[uid] = (index, chapter)
        except Exception as exc:
            errors.append(f"{file_path}: {exc}")

    # Raw text is a provenance fallback for records absent from legacy JSON.
    for file_path in sorted(RAW_TEXT_DIR.glob("*.txt")):
        try:
            uid = int(file_path.stem)
            if uid in records:
                continue
            text = file_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            title_index = next((i for i, value in enumerate(lines) if value.strip()), None)
            if title_index is None:
                raise ValueError("missing title")
            title = lines[title_index].strip()
            body = "\n".join(lines[title_index + 1:])
            if not body.strip():
                raise ValueError("missing body")
            parsed = _parse_title(title)
            link = f"https://ncode.syosetu.com/n2267be/{uid}/"
            index = {"chapter_uid": uid, "chapter_title": title, "narou_link": link,
                     "publication_timestamp": None, "chapter_edited": 0, "edit_timestamp": None,
                     "scraped_timestamp": None}
            records[uid] = (index, {"chapter_title": title, "chapter_body": body, **parsed, "scraped_timestamp": None})
        except Exception as exc:
            errors.append(f"{file_path}: {exc}")
    metadata, metadata_errors = load_index_metadata()
    errors.extend(metadata_errors)
    for uid, metadata_entry in metadata.items():
        if uid not in records:
            errors.append(f"{INDEX_SCRAPE_FILE}: index UID {uid} has no chapter provenance")
            continue
        index, chapter = records[uid]
        # Preserve source-derived title/body, but use the tracked index timestamps and link.
        index.update(metadata_entry)
        index['chapter_title'] = chapter['chapter_title']
    return records, errors


def build(destination: Path, *, force: bool = False) -> int:
    records, errors = load_sources()
    if errors:
        print("Bootstrap aborted; no source records were silently skipped:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    if not records:
        print("Bootstrap aborted: no source records found.", file=sys.stderr)
        return 1
    if destination.exists() and not force:
        print(f"Refusing to overwrite {destination}; pass --force to replace it.", file=sys.stderr)
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = None
    try:
        connection = connect(temporary, writable=True)
        try:
            initialize(connection)
            connection.execute("BEGIN IMMEDIATE")
            for uid in sorted(records):
                upsert_chapter(connection, *records[uid])
            validate(connection, expected_uids=records)
            connection.commit()
            finalize(connection)
        finally:
            connection.close()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"database": str(destination), "chapters": len(records), "status": "ok"}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / DATABASE_PATH)
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(build(arguments.database, force=arguments.force))
