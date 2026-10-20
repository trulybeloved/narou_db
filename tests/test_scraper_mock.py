import asyncio
from dataclasses import replace
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from aiohttp import web
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright
import pytest

from autoscrape_autoupdate import scrape_and_persist
from custom_modules.custom_exceptions import WebPageLoadError
from custom_modules.narou_database import connect, content_hash, get_index, validate
from custom_modules.webscraper import (
    TransportConfig, async_get_http_response_status, async_playwright_webscrape,
    get_http_response_status, sync_playwright_webscrape,
)
from sandbox_scrape import prepare_sandbox, run_sandbox

CANONICAL = 'https://ncode.syosetu.com/n2267be/'
pytestmark = pytest.mark.browser


def update(config):
    return [int(uid) for uid in asyncio.run(scrape_and_persist(config))]


def records(config):
    connection = connect(config.database)
    try:
        validate(connection)
        return [dict(row) for row in connection.execute('SELECT * FROM chapters ORDER BY chapter_uid')]
    finally:
        connection.close()


def test_repeatable_lifecycle(isolated_scraper, mock_service):
    config = isolated_scraper
    assert update(config) == [1, 2, 3]
    baseline = records(config)
    assert all(row['narou_link'] == CANONICAL + str(row['chapter_uid']) + '/' for row in baseline)
    assert all(row['scraped_timestamp'] == 1735689600 for row in baseline)
    assert baseline[0]['chapter_body'].startswith('　静かな朝。\n\n')
    assert baseline[0]['publication_timestamp'] == 1735700400
    assert update(config) == []
    mock_service.state('revised')
    assert update(config) == [2]
    revised = records(config)
    assert revised[0] == baseline[0]
    assert revised[1]['chapter_body'] == '　改稿された道。\n\n'
    assert revised[1]['content_sha256'] == content_hash(revised[1]['chapter_body'])
    assert revised[1]['chapter_edited'] == 1
    assert revised[1]['edit_timestamp'] == 1735864200
    # appended is a complete snapshot: chapter 2 returns to its original state.
    mock_service.state('appended')
    assert update(config) == [2, 4]
    assert update(config) == []
    history = mock_service.api('requests')['requests']
    chapters = [r['url'] for r in history if r['url'].rstrip('/').split('/')[-1].isdigit()]
    assert sorted(chapters) == sorted(['/n2267be/1/', '/n2267be/2/', '/n2267be/3/', '/n2267be/2/', '/n2267be/2/', '/n2267be/4/'])
    paths = list((config.datastore_root / 'chapters').glob('*.json'))
    assert len(paths) == 4
    for path in paths:
        assert '127.0.0.1' not in path.name
        assert json.loads(path.read_text(encoding='utf-8'))['scraped_url'].startswith(CANONICAL)
    assert len(list((config.datastore_root / 'chapter_raws_txt').glob('*.txt'))) == 4
    assert '<ruby>' in (config.datastore_root / 'chapter_raws_html' / '00001.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('state,expected', [
    ('body-only', []), ('removed-listing', []), ('title-change', [2]),
    ('pagination-boundary', [4, 5]), ('duplicate-entry', []), ('incomplete-index', []),
])
def test_current_change_detection_contract(isolated_scraper, mock_service, state, expected):
    assert update(isolated_scraper) == [1, 2, 3]
    before = records(isolated_scraper)
    mock_service.state(state)
    assert update(isolated_scraper) == expected
    after = records(isolated_scraper)
    if not expected:
        assert after == before


@pytest.mark.parametrize('state', ['malformed-timestamp', 'legacy'])
def test_invalid_index_preserves_database(isolated_scraper, mock_service, state):
    before = isolated_scraper.database.read_bytes()
    mock_service.state(state)
    with pytest.raises((RuntimeError, ValueError)):
        update(isolated_scraper)
    assert isolated_scraper.database.read_bytes() == before


def test_missing_index_and_failed_chapter_contract(isolated_scraper, mock_service):
    mock_service.state('missing-selector')
    # Other index pages are still accepted by today's scraper.
    assert update(isolated_scraper) == [3]
    mock_service.api('reset', {})
    manifest = mock_service.api('manifest')
    manifest['states']['baseline']['routes'] = [
        {'path': '/n2267be/2/', 'responses': [{'status': 500, 'body': 'Error'}]}
    ]
    mock_service.api('load', {'manifest': manifest})
    before = isolated_scraper.database.read_bytes()
    with pytest.raises(RuntimeError, match='provenance'):
        update(isolated_scraper)
    assert isolated_scraper.database.read_bytes() == before
    # Provenance is currently written before the atomic SQLite transaction.
    assert (isolated_scraper.datastore_root / 'chapters' / 'https___ncode.syosetu.com_n2267be_2_.json').exists()


def test_transport_status_redirects_and_timeouts(mock_service):
    config = TransportConfig(mock_origin=mock_service.origin, navigation_timeout_ms=1000, max_tries=1)
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 200
    assert asyncio.run(async_get_http_response_status(CANONICAL + '?p=1', transport=config)) == 200
    mock_service.state('429')
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 429
    # HEAD and GET sequence counters are independent.
    mock_service.state('fail-then-recover')
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 500
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 200
    result = sync_playwright_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=config)
    assert result['scrape_results']['.p-eplist'] is None  # HTTP 500 does not trigger a browser retry.
    result = sync_playwright_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=config)
    assert result['scrape_results']['.p-eplist']
    mock_service.state('redirect')
    assert sync_playwright_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=config)['scraped_url'] == CANONICAL + '?p=1'
    mock_service.state('delayed')
    with pytest.raises(WebPageLoadError):
        asyncio.run(async_playwright_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=replace(config, navigation_timeout_ms=50)))


def test_off_origin_redirects_and_subresources_are_blocked(mock_service):
    async def run():
        hits = []
        async def sink(request):
            hits.append(request.path)
            return web.Response(text='external')
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', sink)
        async with TestServer(app) as external:
            outside = str(external.make_url('/external'))
            manifest = {'schema_version': 1, 'id': 'egress', 'initial_state': 'start', 'states': {'start': {'routes': [
                {'path': '/n2267be/', 'responses': [{'status': 302, 'headers': {'Location': '/hop'}}]},
                {'path': '/hop', 'responses': [{'status': 302, 'headers': {'Location': outside}}]},
                {'path': '/resources', 'responses': [{'body': f'<h1>Local</h1><img src="{outside}"><script src="{outside}"></script>'}]},
            ]}}}
            mock_service.api('load', {'manifest': manifest})
            config = TransportConfig(mock_origin=mock_service.origin, navigation_timeout_ms=1000, max_tries=1)
            with pytest.raises(ValueError, match='Blocked'):
                await async_get_http_response_status(CANONICAL, transport=config)
            with pytest.raises(ValueError, match='Blocked'):
                await asyncio.to_thread(get_http_response_status, CANONICAL, transport=config)
            with pytest.raises(WebPageLoadError):
                await async_playwright_webscrape(CANONICAL, 'h1', transport=config)
            with pytest.raises(WebPageLoadError):
                await asyncio.to_thread(sync_playwright_webscrape, CANONICAL, 'h1', transport=config)
            assert (await async_playwright_webscrape('https://ncode.syosetu.com/resources', 'h1', transport=config))['scrape_results']['h1'] == '<h1>Local</h1>'
            assert not hits
    asyncio.run(run())


def test_control_panel(mock_service, artifact_dir):
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(accept_downloads=True)
            await context.tracing.start(screenshots=True, snapshots=True)
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                await page.goto(mock_service.origin + '/__mock/')
                await page.wait_for_selector('#badge:text("narou / baseline")')
                await page.select_option('#state', 'revised')
                await page.click('#apply')
                await page.wait_for_selector('#badge:text("narou / revised")')
                assert mock_service.api('status')['state'] == 'revised'
                urlopen(mock_service.origin + '/n2267be/?p=1').close()
                await page.click('#refresh')
                await page.wait_for_selector('#requests tr')
                assert await page.locator('#requests').inner_text()
                await page.click('#reset')
                await page.wait_for_selector('#badge:text("narou / baseline")')
                await page.fill('#editor', '{"schema_version":99}')
                await page.click('#apply-json')
                await page.wait_for_selector('#message.error')
                assert mock_service.api('status')['state'] == 'baseline'
                await page.select_option('#scenario', 'generic')
                await page.click('#load')
                await page.wait_for_selector('#badge:text("generic / baseline")')
                async with page.expect_download() as info:
                    await page.click('#export')
                download = await info.value
                await download.save_as(artifact_dir / 'export.json')
                exported = json.loads((artifact_dir / 'export.json').read_text(encoding='utf-8'))
                assert exported['id'] == 'generic'
                await page.screenshot(path=str(artifact_dir / 'panel.png'), full_page=True)
                assert errors == []
            finally:
                await context.tracing.stop(path=str(artifact_dir / 'panel-trace.zip'))
                await browser.close()
    asyncio.run(run())


def test_sandbox_runner(mock_service, tmp_path):
    report = asyncio.run(run_sandbox(mock_service.origin, tmp_path / 'run'))
    assert report['changed_uids'] == [1, 2, 3]
    assert [event['type'] for event in report['events']] == ['notification', 'publish-request']
    assert asyncio.run(run_sandbox(mock_service.origin, tmp_path / 'run'))['changed_uids'] == []


def test_sandbox_refuses_production_or_unmarked_directory(tmp_path):
    with pytest.raises(ValueError, match='production'):
        prepare_sandbox(Path(__file__).resolve().parents[1] / 'datastores')
    (tmp_path / 'existing').write_text('keep')
    with pytest.raises(ValueError, match='nonempty'):
        prepare_sandbox(tmp_path)
