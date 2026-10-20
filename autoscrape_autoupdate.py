"""Single-writer Narou scraper using the Git-tracked SQLite database."""

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from loguru import logger

from U_save_chapters_as_txt import process_chapter_for_save
from custom_modules.git_workflow import require_clean_current_master, runtime_directory, _git
from custom_modules.narou_database import atomic_update, connect, get_index, upsert_chapter, validate
from custom_modules.narou_parser import parse_narou_chapter_html, parse_narou_index_html
from custom_modules.utilities import get_current_unix_timestamp, to_filename_friendly
from custom_modules.webscraper import ScrapeInstruction, TransportConfig, async_scrape_url_list
from custom_modules.updater_safety import FatalUpdaterError, ProcessLock, SourceDataError, atomic_bytes, atomic_copy
from custom_modules.updater_publication import Publication

NAROU_INDEX_SELECTOR = '.p-eplist'
INDEX_ENTRY_SELECTOR = '.p-eplist__sublist'
ENTRY_PUBLISHED_TIMESTAMP_SELECTOR = '.p-eplist__update'
CHAPTER_TITLE_SELECTOR = '.p-novel__title'
CHAPTER_TEXT_SELECTOR = '.p-novel__body'
INDEX_URLS = [f'https://ncode.syosetu.com/n2267be/?p={page}' for page in range(1, 10)]
REPOSITORY = Path(__file__).resolve().parent


@dataclass
class ScrapeConfig:
    datastore_root: Path = field(default_factory=lambda: REPOSITORY / 'datastores')
    index_urls: list[str] | None = None
    max_index_pages: int = 100
    transport: TransportConfig = field(default_factory=TransportConfig)
    clock: Callable[[], int] = get_current_unix_timestamp

    def __post_init__(self):
        self.datastore_root = Path(self.datastore_root)
        if isinstance(self.max_index_pages, bool) or not isinstance(self.max_index_pages, int) or self.max_index_pages < 1:
            raise ValueError('max_index_pages must be a positive integer')
        if self.index_urls is not None and (not self.index_urls or len(set(self.index_urls)) != len(self.index_urls)):
            raise ValueError('Explicit index pages must be nonempty and unique')

    @property
    def database(self):
        return Path(self.datastore_root) / 'narou.sqlite'


def _index_signature(entry: dict) -> tuple:
    return (
        (entry.get('chapter_title') or '').strip(), entry.get('narou_link'), entry.get('publication_timestamp'),
        int(bool(entry.get('chapter_edited', 0))), entry.get('edit_timestamp') or 0,
    )


def find_mismatches(local_index: list[dict], stored_index: list[dict]) -> list[dict]:
    stored_by_uid = {int(entry['chapter_uid']): entry for entry in stored_index}
    return [entry for entry in local_index if _index_signature(entry) != _index_signature(stored_by_uid.get(int(entry['chapter_uid']), {}))]


async def _scrape_index_snapshot(config: ScrapeConfig) -> tuple[list[dict], list[dict]]:
    scrape_timestamp = config.clock()
    canonical = 'https://ncode.syosetu.com/n2267be/'
    pending = list(config.index_urls or [canonical + '?p=1'])
    results, visited, by_uid, page_signatures = [], set(), {}, set()
    try:
        while pending:
            if len(visited) + len(pending) > config.max_index_pages:
                raise SourceDataError('Index pagination exceeds the configured page limit')
            batch = await async_scrape_url_list([
                ScrapeInstruction(url, [NAROU_INDEX_SELECTOR, 'html']) for url in pending
            ], transport=config.transport)
            visited.update(pending)
            discovered = set()
            for page in batch:
                html = page['scrape_results'].get(NAROU_INDEX_SELECTOR)
                if not html:
                    raise SourceDataError(f'Missing index selector: {page["scraped_url"]}')
                entries = parse_narou_index_html(html, INDEX_ENTRY_SELECTOR, ENTRY_PUBLISHED_TIMESTAMP_SELECTOR)
                if config.index_urls is None and not entries:
                    raise SourceDataError('Discovered index page contains no chapters')
                signature = tuple(int(entry['chapter_uid']) for entry in entries)
                if signature and signature in page_signatures:
                    raise SourceDataError('Pagination returned the same chapter page more than once')
                page_signatures.add(signature)
                for entry in entries:
                    uid = int(entry['chapter_uid'])
                    if uid < 1 or entry['narou_link'] != f'{canonical}{uid}/' or not entry['chapter_title'].strip():
                        raise SourceDataError('Invalid chapter identity in index')
                    if uid in by_uid and _index_signature(by_uid[uid]) != _index_signature(entry):
                        raise SourceDataError(f'Conflicting index entries for chapter {uid}')
                    entry['scraped_timestamp'] = scrape_timestamp
                    by_uid[uid] = entry
                if config.index_urls is None:
                    body = page['scrape_results'].get('html')
                    if not body:
                        raise SourceDataError('Index document is missing; pagination cannot be validated')
                    soup = BeautifulSoup(body, 'html.parser')
                    for anchor in soup.select('a[href]'):
                        target = urlsplit(urljoin(canonical, anchor['href']))
                        query = parse_qs(target.query, keep_blank_values=True)
                        if 'p' not in query:
                            continue
                        same_index = target.path.rstrip('/') == '/n2267be'
                        if not same_index:
                            continue
                        if target.scheme != 'https' or target.netloc != 'ncode.syosetu.com':
                            raise SourceDataError('Index pagination points outside the canonical origin')
                        if set(query) != {'p'} or len(query['p']) != 1 or not query['p'][0].isdigit():
                            raise SourceDataError('Invalid index pagination link')
                        number = int(query['p'][0])
                        if not 1 <= number <= config.max_index_pages:
                            raise SourceDataError('Index pagination exceeds the configured page limit')
                        discovered.update(f'{canonical}?p={n}' for n in range(1, number + 1))
                # Preserve the existing provenance format without duplicating the full body.
                results.append({'scraped_url': page['scraped_url'], 'scrape_results': {NAROU_INDEX_SELECTOR: html}})
            pending = sorted(discovered - visited)
        if not by_uid:
            raise SourceDataError('No index entries were parsed; refusing to update the database')
        return sorted(by_uid.values(), key=lambda entry: int(entry['chapter_uid'])), results
    except (ValueError, KeyError, AttributeError, TypeError, IndexError) as exc:
        raise SourceDataError(f'Malformed index snapshot: {type(exc).__name__}') from exc


async def scrape_index(config: ScrapeConfig | None = None) -> list[dict]:
    """Read and validate an index without writing tracked diagnostics."""
    entries, _ = await _scrape_index_snapshot(config or ScrapeConfig())
    return entries


async def scrape_and_persist(config: ScrapeConfig | None = None, *, notify: Callable[[str], None] | None = None) -> list[str]:
    """One real scrape operation, with filesystem and external effects explicit."""
    config = config or ScrapeConfig()
    resolved_root = config.datastore_root.resolve()
    if resolved_root.name == 'datastores' and (resolved_root.parent / '.git').exists():
        raise FatalUpdaterError('Use locked update_once() to publish a Git checkout; scrape_and_persist() requires staging or sandbox storage')
    if not config.database.exists():
        raise FileNotFoundError('Run bootstrap_narou_sqlite.py before starting the updater.')
    local_index, index_results = await _scrape_index_snapshot(config)
    stored_index = get_index(config.database)
    missing = {int(row['chapter_uid']) for row in stored_index} - {int(row['chapter_uid']) for row in local_index}
    if missing:
        raise SourceDataError(f'Index is missing {len(missing)} previously stored chapters')
    mismatches = find_mismatches(local_index, stored_index)
    if not mismatches:
        logger.info('No new or modified entries found')
        return []

    scrape_results = await async_scrape_url_list([
        ScrapeInstruction(entry['narou_link'], [CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR]) for entry in mismatches
    ], transport=config.transport)
    chapter_timestamp = config.clock()
    parsed_by_uid, saved_uids = {}, []
    provenance = {}
    expected_urls = {entry['narou_link'] for entry in mismatches}
    expected_by_uid = {int(entry['chapter_uid']): entry for entry in mismatches}
    try:
        for scrape_result in scrape_results:
            if scrape_result['scraped_url'] not in expected_urls:
                raise SourceDataError('Chapter response has an unexpected URL')
            if not all(scrape_result['scrape_results'].get(key) for key in (CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR)):
                raise SourceDataError('Chapter is missing its title or body selector')
            chapter = parse_narou_chapter_html(scrape_result, CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR)
            uid = int(chapter['chapter_uid'])
            if uid in parsed_by_uid or not chapter['chapter_body'].strip() or not chapter['chapter_title'].strip():
                raise SourceDataError('Duplicate or empty chapter response')
            if uid not in expected_by_uid or chapter['chapter_title'].strip() != expected_by_uid[uid]['chapter_title'].strip():
                raise SourceDataError('Chapter title or UID disagrees with the index snapshot')
            chapter['scraped_timestamp'] = chapter_timestamp
            parsed_by_uid[uid] = chapter
            text, html, saved_uid = process_chapter_for_save(scrape_result)
            saved_uids.append(saved_uid)
            provenance[f'chapters/{to_filename_friendly(scrape_result["scraped_url"])}.json'] = json.dumps(scrape_result, ensure_ascii=False, indent=4)
            provenance[f'chapter_raws_txt/{saved_uid}.txt'] = text
            provenance[f'chapter_raws_html/{saved_uid}.html'] = html
    except (ValueError, KeyError, AttributeError, TypeError, IndexError) as exc:
        raise SourceDataError(f'Malformed chapter snapshot: {type(exc).__name__}') from exc

    expected_uids = {int(entry['chapter_uid']) for entry in mismatches}
    if set(parsed_by_uid) != expected_uids:
        raise SourceDataError('Scrape result UIDs do not match the requested index updates.')

    def apply_updates(connection):
        for index_entry in mismatches:
            upsert_chapter(connection, index_entry, parsed_by_uid[int(index_entry['chapter_uid'])])

    # Candidate construction must finish successfully before touching any output.
    with tempfile.TemporaryDirectory(prefix='scrape-', dir=config.datastore_root.parent) as folder:
        staged = Path(folder)
        shutil.copyfile(config.database, staged / 'narou.sqlite')
        atomic_update(staged / 'narou.sqlite', apply_updates)
        provenance['index_scrape_results.json'] = json.dumps(index_results, ensure_ascii=False, indent=4)
        for relative, content in provenance.items():
            atomic_bytes(staged / relative, content.encode('utf-8'))
        for source in sorted(staged.rglob('*')):
            if source.is_file() and source.name != 'narou.sqlite':
                atomic_copy(source, config.datastore_root / source.relative_to(staged))
        atomic_copy(staged / 'narou.sqlite', config.database)
    if notify:
        notify(f'NarouDB found {len(mismatches)} new or modified chapter(s).')
    return saved_uids


async def update_once(repository: Path = REPOSITORY, *, phase=lambda value: None, max_index_pages: int = 100,
                      transport: TransportConfig | None = None) -> int:
    repository = Path(repository).resolve()
    state = runtime_directory(repository)
    with ProcessLock(state / 'writer.lock'):
        publication = Publication(repository, state)
        if publication.journal.exists():
            phase('recovering')
            return publication.finish(phase=phase)
        phase('preflight')
        require_clean_current_master(repository)
        if _git(repository, 'check-attr', 'filter', '--', 'datastores/narou.sqlite').endswith(': lfs'):
            _git(repository, 'lfs', 'version')
            if _git(repository, 'config', '--get', 'filter.lfs.required') != 'true':
                raise FatalUpdaterError('Git LFS filters are not installed; run git lfs install')
        with (repository / 'datastores' / 'narou.sqlite').open('rb') as stream:
            if stream.read(43).startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise FatalUpdaterError('Database is an LFS pointer; install Git LFS and run git lfs pull')
        connection = connect(repository / 'datastores' / 'narou.sqlite')
        try:
            validate(connection)
        finally:
            connection.close()
        base = _git(repository, 'rev-parse', 'HEAD')
        publication.prepare()
        phase('scraping')
        saved_uids = await scrape_and_persist(ScrapeConfig(
            datastore_root=publication.stage, max_index_pages=max_index_pages,
            transport=transport or TransportConfig(diagnostics_dir=state / 'diagnostics'),
        ))
        if not saved_uids:
            shutil.rmtree(publication.root)
            return 0
        record = publication.record(base, saved_uids)
        return publication.finish(record, phase=phase)


async def main() -> None:
    from custom_modules.updater_runtime import run_service, RuntimeConfig
    await run_service(RuntimeConfig.from_cli())


if __name__ == '__main__':
    from custom_modules.updater_runtime import cli
    cli()
