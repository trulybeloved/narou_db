"""Transport contracts that require neither production access nor a browser."""

import asyncio
from dataclasses import replace
from email.utils import formatdate
import json
import time

from aiohttp import web
from aiohttp.test_utils import TestServer
from bs4 import BeautifulSoup
import pytest

from custom_modules.custom_exceptions import WebPageLoadError
from custom_modules import webscraper as scraper
from custom_modules.webscraper import (
    ScrapeInstruction, TransportConfig, async_get_http_response_status,
    async_http_webscrape, async_scrape_url_list, get_http_response_status,
    sync_http_webscrape,
)


@pytest.mark.parametrize('kwargs', [
    {'request_timeout_seconds': value} for value in (0, -1, float('nan'), float('inf'))
] + [
    {'retry_max_seconds': value} for value in (0, -1, float('nan'), float('inf'))
] + [{'max_tries': 0}, {'max_tries': 1.5}, {'max_tries': True},
     {'retry_base_seconds': -1}, {'retry_base_seconds': float('nan')}])
def test_invalid_transport_limits(kwargs):
    with pytest.raises(ValueError, match='Invalid'):
        TransportConfig(**kwargs)


@pytest.mark.parametrize('encoding,declared', [('utf-8', True), ('utf-8', False), ('shift_jis', True)])
def test_html_extraction_encoding_and_diagnostics(tmp_path, encoding, declared):
    async def run():
        html = ('<meta charset="' + encoding + '"><h1>　日本語 &amp; 本文</h1>'
                '<h1>Second</h1><div class="body"><p>　朝。\n</p>'
                '<p><ruby>物語<rt>ものがたり</rt></ruby></p></div>')
        seen = []
        async def handler(request):
            seen.append((request.method, request.headers['User-Agent']))
            return web.Response(body=html.encode(encoding), headers={
                'Content-Type': 'text/html' + ('; charset=' + encoding if declared else '')})
        app = web.Application()
        app.router.add_get('/', handler)
        async with TestServer(app) as server:
            config = TransportConfig(diagnostics_dir=tmp_path)
            url = str(server.make_url('/'))
            result = await async_http_webscrape(url, ['h1', '.body', '.missing'], transport=config)
            assert result['scraped_url'] == url
            assert BeautifulSoup(result['scrape_results']['h1'], 'html.parser').text == '　日本語 & 本文'
            assert result['scrape_results']['.missing'] is None
            body = BeautifulSoup(result['scrape_results']['.body'], 'html.parser')
            assert len(body.select('p')) == 2
            assert body.p.text == '　朝。\n'
            assert body.select_one('ruby rt').text == 'ものがたり'
            sync_result = await asyncio.to_thread(sync_http_webscrape, url, 'h1', transport=config)
            assert sync_result['scrape_results']['h1'] == result['scrape_results']['h1']
            assert seen == [('GET', 'NarouDB/1.0')] * 2
        diagnostics = [json.loads(path.read_text()) for path in tmp_path.glob('*.json')]
        assert len(diagnostics) == 2
        assert all(record['status'] == 200 and record['error'] is None for record in diagnostics)
        assert (tmp_path / diagnostics[0]['response_file']).read_bytes() == html.encode(encoding)
    asyncio.run(run())


@pytest.mark.parametrize('status,attempts', [(404, 1), (403, 1), (500, 4), (503, 4)])
def test_http_failure_attempt_limits(status, attempts, tmp_path):
    async def run():
        hits = []
        async def handler(request):
            hits.append(request.method)
            return web.Response(status=status, text='Unavailable')
        app = web.Application()
        app.router.add_route('*', '/', handler)
        async with TestServer(app) as server:
            url = str(server.make_url('/'))
            config = TransportConfig(retry_base_seconds=0, diagnostics_dir=tmp_path)
            with pytest.raises(WebPageLoadError, match=f'HTTP {status}'):
                await async_http_webscrape(url, 'h1', transport=config)
            assert len(hits) == attempts
            assert await async_get_http_response_status(url, transport=config) == status
            assert await asyncio.to_thread(get_http_response_status, url, transport=config) == status
            assert len(hits) == attempts + 2  # HEAD reports status without retries.
        records = [json.loads(path.read_text()) for path in tmp_path.glob('*.json')]
        assert all(record['status'] == status and record['error'] == f'HTTP {status}' for record in records)
    asyncio.run(run())


def test_retry_recovery_and_retry_after():
    async def run():
        hits = []
        async def handler(request):
            hits.append(time.monotonic())
            if len(hits) == 1:
                return web.Response(status=500)
            if len(hits) == 2:
                return web.Response(status=429, headers={'Retry-After': '0.05'})
            return web.Response(text='<h1>Recovered</h1>')
        app = web.Application()
        app.router.add_get('/', handler)
        async with TestServer(app) as server:
            result = await async_http_webscrape(str(server.make_url('/')), 'h1',
                                               transport=TransportConfig(retry_base_seconds=0))
            assert result['scrape_results']['h1'] == '<h1>Recovered</h1>'
            assert len(hits) == 3
            assert hits[2] - hits[1] >= .045
    asyncio.run(run())


def test_retry_after_dates_and_invalid_values(monkeypatch):
    monkeypatch.setattr(scraper.time, 'time', lambda: 1700000000)
    assert scraper._retry_after(formatdate(1700000006, usegmt=True)) == 6
    assert scraper._retry_after(formatdate(1699999999, usegmt=True)) == 0
    for value in (None, 'invalid', '-1', 'NaN', 'inf'):
        assert scraper._retry_after(value) == 0


def test_retry_after_beyond_budget_fails_without_sleeping():
    async def run():
        hits = []
        async def handler(request):
            hits.append(request.path)
            return web.Response(status=429, headers={'Retry-After': '60'})
        app = web.Application()
        app.router.add_get('/', handler)
        async with TestServer(app) as server:
            started = time.monotonic()
            with pytest.raises(WebPageLoadError, match='Retry deadline exhausted after HTTP 429'):
                await async_http_webscrape(str(server.make_url('/')), 'h1', transport=TransportConfig(
                    retry_base_seconds=0, retry_max_seconds=.1))
            assert time.monotonic() - started < .5
            assert len(hits) == 1
    asyncio.run(run())


def test_connection_failure_retries_and_head_reports_zero(tmp_path):
    async def run():
        server = TestServer(web.Application())
        await server.start_server()
        url = str(server.make_url('/'))
        await server.close()
        config = TransportConfig(max_tries=2, retry_base_seconds=0, diagnostics_dir=tmp_path)
        with pytest.raises(WebPageLoadError, match='ClientConnectorError') as error:
            await async_http_webscrape(url, 'h1', transport=config)
        assert url in str(error.value)
        assert await async_get_http_response_status(url, transport=config) == 0
        records = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
        assert sorted(record['attempt'] for record in records if record['method'] == 'GET') == [1, 2]
        assert all(record['status'] is None and 'ClientConnectorError' in record['error'] for record in records)
    asyncio.run(run())


@pytest.mark.parametrize('redirects,succeeds', [(10, True), (11, False)])
def test_redirect_limit_and_original_url(redirects, succeeds):
    async def run():
        hits = []
        async def handler(request):
            hop = int(request.match_info['hop'])
            hits.append(hop)
            if hop < redirects:
                return web.Response(status=302, headers={'Location': str(hop + 1)})
            return web.Response(text='<h1>Final</h1>')
        app = web.Application()
        app.router.add_get('/{hop}', handler)
        async with TestServer(app) as server:
            url = str(server.make_url('/0'))
            if succeeds:
                result = await async_http_webscrape(url, 'h1')
                assert result == {'scraped_url': url, 'scrape_results': {'h1': '<h1>Final</h1>'}}
            else:
                with pytest.raises(WebPageLoadError, match='Too many redirects'):
                    await async_http_webscrape(url, 'h1')
            assert len(hits) == 11
    asyncio.run(run())


def test_deadline_bounds_redirects_and_timeout_retries(tmp_path):
    async def run():
        hits = []
        async def handler(request):
            hits.append(request.path)
            await asyncio.sleep(.08)
            return web.Response(status=302, headers={'Location': '/hop'})
        app = web.Application()
        app.router.add_get('/{path:.*}', handler)
        async with TestServer(app) as server:
            config = TransportConfig(request_timeout_seconds=.03, retry_max_seconds=.07,
                                     retry_base_seconds=0, diagnostics_dir=tmp_path)
            started = time.monotonic()
            with pytest.raises(WebPageLoadError, match='TimeoutError'):
                await async_http_webscrape(str(server.make_url('/')), 'h1', transport=config)
            assert time.monotonic() - started < .3
            assert 1 <= len(hits) <= 3
            assert await async_get_http_response_status(str(server.make_url('/')), transport=config) == 0
            # A redirect chain shares one deadline, even if each hop would finish in time.
            config = replace(config, request_timeout_seconds=1, retry_max_seconds=.12, max_tries=1)
            before = len(hits)
            with pytest.raises(WebPageLoadError, match='TimeoutError'):
                await async_http_webscrape(str(server.make_url('/')), 'h1', transport=config)
            assert len(hits) - before == 2
        records = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
        assert any('CancelledError' in (record['error'] or '') for record in records)
    asyncio.run(run())


def test_batch_order_concurrency_and_session_reuse(monkeypatch):
    async def run():
        active, maximum = 0, 0
        connections = set()
        async def handler(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            connections.add(request.transport)
            try:
                uid = int(request.match_info['uid'])
                await asyncio.sleep(.005 * (15 - uid))
                return web.Response(text=f'<h1>{uid}</h1>')
            finally:
                active -= 1
        app = web.Application()
        app.router.add_get('/{uid}', handler)
        original = scraper._session
        sessions = []
        def session(config):
            result = original(config)
            sessions.append(result)
            return result
        monkeypatch.setattr(scraper, '_session', session)
        async with TestServer(app) as server:
            instructions = [ScrapeInstruction(str(server.make_url(f'/{uid}')), ['h1']) for uid in range(15)]
            results = await async_scrape_url_list(instructions)
            assert [r['scrape_results']['h1'] for r in results] == [f'<h1>{uid}</h1>' for uid in range(15)]
            assert maximum == 10
            assert len(connections) <= 10
            assert len(sessions) == 1 and sessions[0].closed
    asyncio.run(run())


def test_batch_failure_cancels_other_requests_and_closes_session(monkeypatch, tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(request):
            started.set()
            await release.wait()
            return web.Response(text='<h1>Late</h1>')
        async def failure(request):
            await started.wait()
            return web.Response(status=404)
        app = web.Application()
        app.router.add_get('/slow', slow)
        app.router.add_get('/failure', failure)
        sessions = []
        original = scraper._session
        def session(config):
            result = original(config)
            sessions.append(result)
            return result
        monkeypatch.setattr(scraper, '_session', session)
        async with TestServer(app) as server:
            try:
                instructions = [ScrapeInstruction(str(server.make_url(path)), ['h1']) for path in ('/slow', '/failure')]
                with pytest.raises(WebPageLoadError, match='HTTP 404'):
                    await async_scrape_url_list(instructions, transport=TransportConfig(diagnostics_dir=tmp_path))
                assert sessions[0].closed
                records = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
                assert any(record['error'].startswith('CancelledError') for record in records)
            finally:
                release.set()
    asyncio.run(run())


def test_external_cancellation_propagates():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            started.set()
            await release.wait()
            return web.Response(text='late')
        app = web.Application()
        app.router.add_get('/', handler)
        async with TestServer(app) as server:
            task = asyncio.create_task(async_http_webscrape(str(server.make_url('/')), 'h1'))
            await started.wait()
            try:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
    asyncio.run(run())


def test_sync_helpers_reject_active_event_loop():
    async def run():
        for helper, args in ((sync_http_webscrape, ('https://example.org/', 'h1')),
                             (get_http_response_status, ('https://example.org/',))):
            with pytest.raises(RuntimeError, match='active event loop; use async_'):
                helper(*args)
    asyncio.run(run())
