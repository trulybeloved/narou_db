import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest

import autoscrape_autoupdate as updater
from custom_modules.updater_safety import SourceDataError
from custom_modules.webscraper import TransportConfig, async_http_webscrape
from custom_modules.custom_exceptions import WebPageLoadError


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('state', ['missing-selector', 'incomplete-index', 'malformed-timestamp', 'legacy'])
def test_bad_discovered_index_preserves_all_outputs(isolated_scraper, mock_service, state):
    config = replace(isolated_scraper, index_urls=None)
    before = snapshot(config.datastore_root)
    mock_service.state(state)
    with pytest.raises(SourceDataError):
        asyncio.run(updater.scrape_and_persist(config))
    assert snapshot(config.datastore_root) == before


@pytest.mark.parametrize('link', ['?p=0', '?p=', '?p=101', '?p=two', '?p=2&p=3', '?p=2&other=1',
                                 'https://outside.invalid/n2267be/?p=2'])
def test_invalid_pagination_is_rejected(isolated_scraper, mock_service, link):
    manifest = mock_service.api('manifest')
    baseline = manifest['states']['baseline']
    baseline['routes'].append({'path': '/n2267be/', 'query': {'p': '1'}, 'responses': [{'body':
        '<html><div class="p-eplist"><div class="p-eplist__sublist">'
        '<a href="/n2267be/1/">第一章１　始まり</a><div class="p-eplist__update">2025/01/01 12:00</div>'
        f'</div></div><nav><a href="{link}">Next</a></nav></html>'}]})
    mock_service.api('load', {'manifest': manifest})
    config = replace(isolated_scraper, index_urls=None)
    before = snapshot(config.datastore_root)
    with pytest.raises(SourceDataError, match='pagination'):
        asyncio.run(updater.scrape_and_persist(config))
    assert snapshot(config.datastore_root) == before


def test_page_bound_and_conflicting_duplicates(isolated_scraper, mock_service):
    with pytest.raises(SourceDataError, match='page limit'):
        asyncio.run(updater.scrape_and_persist(replace(isolated_scraper, index_urls=None, max_index_pages=1)))
    manifest = mock_service.api('manifest')
    manifest['states']['baseline']['chapters'].append({
        'uid': 1, 'title': '第一章１　Conflicting title', 'body_html': '<p>Conflict</p>',
    })
    mock_service.api('load', {'manifest': manifest})
    with pytest.raises(SourceDataError, match='Conflicting index'):
        asyncio.run(updater.scrape_and_persist(replace(isolated_scraper, index_urls=None)))


def test_repeated_page_is_rejected(isolated_scraper, mock_service):
    manifest = mock_service.api('manifest')
    state = manifest['states']['baseline']
    state['routes'] = []
    state['chapters'] += [dict(row) for row in state['chapters'][:2]]
    # Page 3 contains a duplicate page-1 UID sequence.
    state['chapters'] = state['chapters'][:2] + [state['chapters'][2], dict(state['chapters'][2], uid=4)] + state['chapters'][3:]
    mock_service.api('load', {'manifest': manifest})
    with pytest.raises(SourceDataError, match='same chapter page'):
        asyncio.run(updater.scrape_and_persist(replace(isolated_scraper, index_urls=None)))


def test_late_parse_failure_preserves_all_provenance(isolated_scraper):
    before = snapshot(isolated_scraper.datastore_root)
    real_parser = updater.parse_narou_chapter_html
    calls = 0

    def parser(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError('second chapter cannot be parsed')
        return real_parser(*args)

    with patch.object(updater, 'parse_narou_chapter_html', side_effect=parser):
        with pytest.raises(SourceDataError, match='Malformed chapter'):
            asyncio.run(updater.scrape_and_persist(isolated_scraper))
    assert snapshot(isolated_scraper.datastore_root) == before


@pytest.mark.parametrize('body', ['<div class="p-novel__body"></div>', '<h1>Missing selectors</h1>'])
def test_invalid_chapter_preserves_all_outputs(isolated_scraper, mock_service, body):
    manifest = mock_service.api('manifest')
    manifest['states']['baseline']['routes'].append({
        'path': '/n2267be/2/', 'responses': [{'body': '<html><h1 class="p-novel__title">第一章２　旅立ち</h1>' + body + '</html>'}],
    })
    mock_service.api('load', {'manifest': manifest})
    before = snapshot(isolated_scraper.datastore_root)
    with pytest.raises(SourceDataError):
        asyncio.run(updater.scrape_and_persist(isolated_scraper))
    assert snapshot(isolated_scraper.datastore_root) == before


def test_response_size_and_diagnostic_retention(mock_service, tmp_path):
    config = TransportConfig(mock_origin=mock_service.origin, max_response_bytes=10,
                             max_tries=1, diagnostics_dir=tmp_path)
    url = 'https://ncode.syosetu.com/n2267be/?p=1'
    with pytest.raises(WebPageLoadError, match='size limit'):
        asyncio.run(async_http_webscrape(url, '.p-eplist', transport=config))
    config = replace(config, max_response_bytes=1024 * 1024, diagnostics_max_records=2)
    for _ in range(4):
        asyncio.run(async_http_webscrape(url, '.p-eplist', transport=config))
    assert len(list(tmp_path.glob('*.json'))) == len(list(tmp_path.glob('*.html'))) == 2
