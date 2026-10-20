"""Single-writer Narou scraper using the Git-tracked SQLite database."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from loguru import logger

from U_save_chapters_as_txt import save_chapter
from custom_modules.discord_integration import send_discord_message
from custom_modules.git_workflow import commit_datastores_and_push, require_clean_current_master
from custom_modules.narou_database import atomic_update, get_index, upsert_chapter
from custom_modules.narou_parser import parse_narou_chapter_html, parse_narou_index_html
from custom_modules.utilities import get_current_unix_timestamp, sleep_with_progress, to_filename_friendly
from custom_modules.webscraper import ScrapeInstruction, TransportConfig, async_scrape_url_list

NAROU_INDEX_SELECTOR = '.p-eplist'
INDEX_ENTRY_SELECTOR = '.p-eplist__sublist'
ENTRY_PUBLISHED_TIMESTAMP_SELECTOR = '.p-eplist__update'
CHAPTER_TITLE_SELECTOR = '.p-novel__title'
CHAPTER_TEXT_SELECTOR = '.p-novel__body'
INDEX_URLS = [f'https://ncode.syosetu.com/n2267be/?p={page}' for page in range(1, 10)]
REPOSITORY = Path(__file__).resolve().parent


@dataclass
class ScrapeConfig:
    datastore_root: Path = field(default_factory=lambda: Path('datastores'))
    index_urls: list[str] = field(default_factory=lambda: list(INDEX_URLS))
    transport: TransportConfig = field(default_factory=TransportConfig)
    clock: Callable[[], int] = get_current_unix_timestamp

    @property
    def database(self):
        return Path(self.datastore_root) / 'narou.sqlite'


def _index_signature(entry: dict) -> tuple:
    return (
        entry.get('chapter_title'), entry.get('narou_link'), entry.get('publication_timestamp'),
        int(bool(entry.get('chapter_edited', 0))), entry.get('edit_timestamp') or 0,
    )


def find_mismatches(local_index: list[dict], stored_index: list[dict]) -> list[dict]:
    stored_by_uid = {int(entry['chapter_uid']): entry for entry in stored_index}
    return [entry for entry in local_index if _index_signature(entry) != _index_signature(stored_by_uid.get(int(entry['chapter_uid']), {}))]


async def scrape_index(config: ScrapeConfig | None = None) -> list[dict]:
    config = config or ScrapeConfig()
    scrape_timestamp = config.clock()
    results = await async_scrape_url_list([ScrapeInstruction(url, [NAROU_INDEX_SELECTOR]) for url in config.index_urls], transport=config.transport)
    (Path(config.datastore_root) / 'index_scrape_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding='utf-8')
    index = []
    for page in results:
        html = page['scrape_results'].get(NAROU_INDEX_SELECTOR)
        if not html:
            continue
        for entry in parse_narou_index_html(html, INDEX_ENTRY_SELECTOR, ENTRY_PUBLISHED_TIMESTAMP_SELECTOR):
            entry['scraped_timestamp'] = scrape_timestamp
            index.append(entry)
    if not index:
        raise RuntimeError('No index entries were parsed; refusing to update the database.')
    return sorted(index, key=lambda entry: int(entry['chapter_uid']))


async def scrape_and_persist(config: ScrapeConfig | None = None, *, notify: Callable[[str], None] | None = None) -> list[str]:
    """One real scrape operation, with filesystem and external effects explicit."""
    config = config or ScrapeConfig()
    if not config.database.exists():
        raise FileNotFoundError('Run bootstrap_narou_sqlite.py before starting the updater.')
    local_index = await scrape_index(config)
    mismatches = find_mismatches(local_index, get_index(config.database))
    if not mismatches:
        logger.info('No new or modified entries found')
        return []

    if notify:
        notify(f'NarouDB found {len(mismatches)} new or modified chapter(s).')
    scrape_results = await async_scrape_url_list([
        ScrapeInstruction(entry['narou_link'], [CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR]) for entry in mismatches
    ], transport=config.transport)
    chapter_timestamp = config.clock()
    parsed_by_uid, saved_uids = {}, []
    (Path(config.datastore_root) / 'chapters').mkdir(parents=True, exist_ok=True)
    for scrape_result in scrape_results:
        (Path(config.datastore_root) / 'chapters' / f'{to_filename_friendly(scrape_result["scraped_url"])}.json').write_text(
            json.dumps(scrape_result, ensure_ascii=False, indent=4), encoding='utf-8'
        )
        success, uid = save_chapter(scrape_result, config.datastore_root)
        if not success:
            raise RuntimeError(f'Could not save provenance for {scrape_result["scraped_url"]}')
        saved_uids.append(uid)
        chapter = parse_narou_chapter_html(scrape_result, CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR)
        chapter['scraped_timestamp'] = chapter_timestamp
        parsed_by_uid[int(chapter['chapter_uid'])] = chapter

    expected_uids = {int(entry['chapter_uid']) for entry in mismatches}
    if set(parsed_by_uid) != expected_uids:
        raise RuntimeError('Scrape result UIDs do not match the requested index updates.')

    def apply_updates(connection):
        for index_entry in mismatches:
            upsert_chapter(connection, index_entry, parsed_by_uid[int(index_entry['chapter_uid'])])

    atomic_update(config.database, apply_updates)
    return saved_uids


async def update_once() -> int:
    require_clean_current_master(REPOSITORY)
    saved_uids = await scrape_and_persist(notify=lambda message: send_discord_message(message, ping=True))
    if not saved_uids:
        return 0
    commit_datastores_and_push(REPOSITORY, f'Chapter update for {", ".join(map(str, saved_uids))}')
    return len(saved_uids)


async def main() -> None:
    load_dotenv()
    while True:
        try:
            await update_once()
        except Exception as exc:
            logger.exception('NarouDB update stopped: {}', exc)
            send_discord_message(f'NarouDB update stopped: {exc}', ping=True)
            return
        sleep_with_progress(900)


if __name__ == '__main__':
    asyncio.run(main())
