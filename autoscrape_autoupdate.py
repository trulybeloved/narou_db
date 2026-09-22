"""Single-writer Narou scraper using the Git-tracked SQLite database."""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from U_save_chapters_as_txt import save_chapter
from custom_modules.discord_integration import send_discord_message
from custom_modules.git_workflow import GitWorkflowError, commit_datastores_and_push, require_clean_current_master
from custom_modules.narou_database import DATABASE_PATH, atomic_update, get_index, upsert_chapter
from custom_modules.narou_parser import parse_narou_chapter_html, parse_narou_index_html
from custom_modules.utilities import get_current_unix_timestamp, sleep_with_progress, to_filename_friendly
from custom_modules.webscraper import ScrapeInstruction, async_scrape_url_list

NAROU_INDEX_SELECTOR = '.p-eplist'
INDEX_ENTRY_SELECTOR = '.p-eplist__sublist'
ENTRY_PUBLISHED_TIMESTAMP_SELECTOR = '.p-eplist__update'
CHAPTER_TITLE_SELECTOR = '.p-novel__title'
CHAPTER_TEXT_SELECTOR = '.p-novel__body'
INDEX_URLS = [f'https://ncode.syosetu.com/n2267be/?p={page}' for page in range(1, 10)]
REPOSITORY = Path(__file__).resolve().parent


def _index_signature(entry: dict) -> tuple:
    return (
        entry.get('chapter_title'), entry.get('narou_link'), entry.get('publication_timestamp'),
        int(bool(entry.get('chapter_edited', 0))), entry.get('edit_timestamp') or 0,
    )


def find_mismatches(local_index: list[dict], stored_index: list[dict]) -> list[dict]:
    stored_by_uid = {int(entry['chapter_uid']): entry for entry in stored_index}
    return [entry for entry in local_index if _index_signature(entry) != _index_signature(stored_by_uid.get(int(entry['chapter_uid']), {}))]


async def scrape_index() -> list[dict]:
    scrape_timestamp = get_current_unix_timestamp()
    results = await async_scrape_url_list([ScrapeInstruction(url, [NAROU_INDEX_SELECTOR]) for url in INDEX_URLS])
    Path('datastores/index_scrape_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding='utf-8')
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


async def update_once() -> int:
    require_clean_current_master(REPOSITORY)
    if not DATABASE_PATH.exists():
        raise FileNotFoundError('Run bootstrap_narou_sqlite.py before starting the updater.')
    local_index = await scrape_index()
    mismatches = find_mismatches(local_index, get_index())
    if not mismatches:
        logger.info('No new or modified entries found')
        return 0

    send_discord_message(f'NarouDB found {len(mismatches)} new or modified chapter(s).', ping=True)
    scrape_results = await async_scrape_url_list([
        ScrapeInstruction(entry['narou_link'], [CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR]) for entry in mismatches
    ])
    chapter_timestamp = get_current_unix_timestamp()
    parsed_by_uid, saved_uids = {}, []
    for scrape_result in scrape_results:
        Path(f'datastores/chapters/{to_filename_friendly(scrape_result["scraped_url"])}.json').write_text(
            json.dumps(scrape_result, ensure_ascii=False, indent=4), encoding='utf-8'
        )
        success, uid = save_chapter(scrape_result)
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

    atomic_update(DATABASE_PATH, apply_updates)
    commit_datastores_and_push(REPOSITORY, f'Chapter update for {", ".join(map(str, saved_uids))}')
    return len(mismatches)


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
