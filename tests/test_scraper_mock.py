import asyncio
from dataclasses import replace
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from autoscrape_autoupdate import scrape_and_persist
from custom_modules.custom_exceptions import WebPageLoadError
from custom_modules.narou_database import connect, content_hash, get_index, validate
from custom_modules.webscraper import (
    TransportConfig, async_get_http_response_status, async_http_webscrape,
    get_http_response_status, sync_http_webscrape,
)
from sandbox_scrape import prepare_sandbox, run_sandbox

CANONICAL = 'https://ncode.syosetu.com/n2267be/'


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
    ('body-only', []), ('title-change', [2]),
    ('pagination-boundary', [4, 5]), ('duplicate-entry', []),
])
def test_current_change_detection_contract(isolated_scraper, mock_service, state, expected):
    assert update(isolated_scraper) == [1, 2, 3]
    before = records(isolated_scraper)
    mock_service.state(state)
    assert update(isolated_scraper) == expected
    after = records(isolated_scraper)
    if not expected:
        assert after == before


@pytest.mark.parametrize('state', ['malformed-timestamp', 'legacy', 'removed-listing', 'incomplete-index'])
def test_invalid_index_preserves_database(isolated_scraper, mock_service, state):
    if state == 'removed-listing':
        update(isolated_scraper)
    before = isolated_scraper.database.read_bytes()
    mock_service.state(state)
    with pytest.raises((RuntimeError, ValueError)):
        update(isolated_scraper)
    assert isolated_scraper.database.read_bytes() == before


def test_missing_index_and_failed_chapter_contract(isolated_scraper, mock_service):
    mock_service.state('missing-selector')
    with pytest.raises(RuntimeError, match='Missing index selector'):
        update(isolated_scraper)
    mock_service.api('reset', {})
    manifest = mock_service.api('manifest')
    manifest['states']['baseline']['routes'] += [
        {'path': '/n2267be/2/', 'responses': [{'status': 500, 'body': 'Error'}]}
    ]
    mock_service.api('load', {'manifest': manifest})
    before = isolated_scraper.database.read_bytes()
    provenance_before = {
        path.relative_to(isolated_scraper.datastore_root): path.read_bytes()
        for folder in ('chapters', 'chapter_raws_html', 'chapter_raws_txt')
        for path in (isolated_scraper.datastore_root / folder).glob('*')
    }
    with pytest.raises(WebPageLoadError, match='HTTP 500'):
        update(isolated_scraper)
    assert isolated_scraper.database.read_bytes() == before
    # The failed batch leaves the earlier successful chapter's provenance intact.
    assert {
        path.relative_to(isolated_scraper.datastore_root): path.read_bytes()
        for folder in ('chapters', 'chapter_raws_html', 'chapter_raws_txt')
        for path in (isolated_scraper.datastore_root / folder).glob('*')
    } == provenance_before


def test_transport_status_redirects_and_timeouts(mock_service):
    config = TransportConfig(mock_origin=mock_service.origin, request_timeout_seconds=1, max_tries=1)
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 200
    assert asyncio.run(async_get_http_response_status(CANONICAL + '?p=1', transport=config)) == 200
    mock_service.state('429')
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 429
    # HEAD and GET sequence counters are independent.
    mock_service.state('fail-then-recover')
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 500
    assert get_http_response_status(CANONICAL + '?p=1', transport=config) == 200
    with pytest.raises(WebPageLoadError, match='HTTP 500'):
        sync_http_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=config)
    result = sync_http_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=config)
    assert result['scrape_results']['.p-eplist']
    mock_service.state('redirect')
    assert sync_http_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=config)['scraped_url'] == CANONICAL + '?p=1'
    mock_service.state('delayed')
    with pytest.raises(WebPageLoadError):
        asyncio.run(async_http_webscrape(CANONICAL + '?p=1', '.p-eplist', transport=replace(config, request_timeout_seconds=.05)))


def test_off_origin_redirects_and_subresources_are_blocked(mock_service, monkeypatch):
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
            monkeypatch.setenv('HTTP_PROXY', outside)
            monkeypatch.setenv('HTTPS_PROXY', outside)
            monkeypatch.setenv('NO_PROXY', '')
            monkeypatch.setenv('no_proxy', '')
            config = TransportConfig(mock_origin=mock_service.origin, request_timeout_seconds=1, max_tries=1)
            with pytest.raises(ValueError, match='Blocked'):
                await async_get_http_response_status(CANONICAL, transport=config)
            with pytest.raises(ValueError, match='Blocked'):
                await asyncio.to_thread(get_http_response_status, CANONICAL, transport=config)
            with pytest.raises(WebPageLoadError):
                await async_http_webscrape(CANONICAL, 'h1', transport=config)
            with pytest.raises(WebPageLoadError):
                await asyncio.to_thread(sync_http_webscrape, CANONICAL, 'h1', transport=config)
            assert (await async_http_webscrape('https://ncode.syosetu.com/resources', 'h1', transport=config))['scrape_results']['h1'] == '<h1>Local</h1>'
            assert not hits
    asyncio.run(run())


def test_control_panel_delivery_and_api(mock_service, artifact_dir):
    from bs4 import BeautifulSoup

    with urlopen(mock_service.origin + '/__mock/') as response:
        panel = BeautifulSoup(response.read(), 'html.parser')
    for selector in ('#badge', '#state', '#apply', '#refresh', '#requests', '#reset',
                     '#editor', '#apply-json', '#scenario', '#load', '#export'):
        assert panel.select_one(selector) is not None
    mock_service.state('revised')
    assert mock_service.api('status')['state'] == 'revised'
    with urlopen(mock_service.origin + '/n2267be/?p=1'):
        pass
    assert mock_service.api('requests')['requests']
    mock_service.api('reset', {})
    assert mock_service.api('status')['state'] == 'baseline'
    with pytest.raises(HTTPError) as error:
        mock_service.api('load', {'manifest': {'schema_version': 99}})
    assert error.value.code == 400
    assert mock_service.api('status')['state'] == 'baseline'
    mock_service.api('load', {'id': 'generic'})
    assert mock_service.api('status')['scenario'] == 'generic'
    exported = mock_service.api('manifest')
    (artifact_dir / 'export.json').write_text(json.dumps(exported, indent=2), encoding='utf-8')
    assert exported['id'] == 'generic'


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
