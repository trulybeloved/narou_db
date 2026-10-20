"""HTTP-only HTML scraping with bounded retries and an isolated mock transport."""

import asyncio
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import logging
import math
from pathlib import Path
import random
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import aiohttp
from bs4 import BeautifulSoup

from custom_modules.custom_exceptions import WebPageLoadError

STATUS_HEADERS = {'User-Agent': 'NarouDB/1.0'}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 10


@dataclass(frozen=True)
class TransportConfig:
    mock_origin: str | None = None
    request_timeout_seconds: float = 30
    max_tries: int = 4
    retry_base_seconds: float = 1
    retry_max_seconds: float = 120
    jitter: bool = True
    diagnostics_dir: Path | None = None

    def __post_init__(self):
        if (not isinstance(self.max_tries, int) or isinstance(self.max_tries, bool) or self.max_tries < 1
                or not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0
                or not math.isfinite(self.retry_max_seconds) or self.retry_max_seconds <= 0
                or not math.isfinite(self.retry_base_seconds) or self.retry_base_seconds < 0):
            raise ValueError('Invalid timeout or retry configuration')
        if self.mock_origin:
            parsed = urlsplit(self.mock_origin)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
                raise ValueError('mock_origin must be an HTTP origin without a path')
            parsed.port  # Reject malformed ports before making requests.

    def allows(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return False
        if not self.mock_origin:
            return True
        allowed = urlsplit(self.mock_origin)
        def origin(parts):
            return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)
        return origin(parsed) == origin(allowed)

    def map_url(self, url):
        parsed = urlsplit(url)
        if self.mock_origin and (parsed.scheme, parsed.netloc) == ('https', 'ncode.syosetu.com'):
            target = urlsplit(self.mock_origin)
            return urlunsplit((target.scheme, target.netloc, parsed.path, parsed.query, parsed.fragment))
        if self.allows(url):
            return url
        raise ValueError(f'Blocked request outside the allowed HTTP origin: {url}')

    def delay(self, attempt):
        delay = self.retry_base_seconds * 2 ** attempt
        return random.uniform(0, delay) if self.jitter else delay


class ScrapeInstruction:
    def __init__(self, scrape_url: str, query_selectors: list):
        self.url = scrape_url
        self.query_selectors = query_selectors


class _HTTPError(WebPageLoadError):
    def __init__(self, status, retry_after):
        super().__init__(f'HTTP {status}')
        self.retryable = status == 429 or 500 <= status <= 599
        self.retry_after = retry_after


class _RedirectLimitError(WebPageLoadError):
    pass


def _retry_after(value):
    if value is None:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def _diagnostic(config, metadata, body):
    if config.diagnostics_dir is None:
        return
    try:
        root = Path(config.diagnostics_dir)
        root.mkdir(parents=True, exist_ok=True)
        name = uuid4().hex
        if body is not None:
            (root / f'{name}.html').write_bytes(body)
            metadata['response_file'] = f'{name}.html'
        (root / f'{name}.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        logging.getLogger(__name__).warning('Could not write HTTP diagnostics', exc_info=True)


def _session(config):
    return aiohttp.ClientSession(
        headers=STATUS_HEADERS, trust_env=False,
        timeout=aiohttp.ClientTimeout(total=config.request_timeout_seconds),
        connector=aiohttp.TCPConnector(limit=10),
    )


async def _request(session, method, target, config, *, original_url, attempt):
    """Follow redirects manually so mock egress is checked before every hop."""
    for hop in range(MAX_REDIRECTS + 1):
        if not config.allows(target):
            raise ValueError(f'Blocked redirect outside the allowed HTTP origin: {target}')
        started = time.monotonic()
        body = None
        metadata = {'url': target, 'original_url': original_url, 'method': method,
                    'attempt': attempt, 'redirect_hop': hop, 'status': None, 'error': None}
        try:
            async with session.request(method, target, allow_redirects=False) as response:
                metadata['status'] = response.status
                # HEAD does not consume the body; GET diagnostics retain even error responses.
                body = await response.read() if method == 'GET' else None
                if response.status in REDIRECT_STATUSES and 'Location' in response.headers:
                    if hop == MAX_REDIRECTS:
                        raise _RedirectLimitError(f'Too many redirects (limit {MAX_REDIRECTS})')
                    target = urljoin(str(response.url), response.headers['Location'])
                    continue
                if response.status >= 400:
                    metadata['error'] = f'HTTP {response.status}'
                return response.status, body, response.charset, response.headers.get('Retry-After')
        except BaseException as exc:
            metadata['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            metadata['duration_seconds'] = time.monotonic() - started
            _diagnostic(config, metadata, body)


def _run_sync(async_function, *args, **kwargs):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(async_function(*args, **kwargs))
    raise RuntimeError(f'Cannot use a synchronous HTTP helper inside an active event loop; use {async_function.__name__}() instead.')


def get_http_response_status(url: str, *, transport: TransportConfig | None = None) -> int:
    return _run_sync(async_get_http_response_status, url, transport=transport)


async def async_get_http_response_status(url: str, *, transport: TransportConfig | None = None) -> int:
    config = transport or TransportConfig()
    target = config.map_url(url)
    try:
        async with _session(config) as session:
            async with asyncio.timeout(min(config.request_timeout_seconds, config.retry_max_seconds)):
                status, _, _, _ = await _request(session, 'HEAD', target, config, original_url=url, attempt=1)
                return status
    except (aiohttp.ClientError, TimeoutError, _RedirectLimitError):
        return 0


async def _scrape(session, url, query_selectors, config):
    selectors = [query_selectors] if isinstance(query_selectors, str) else query_selectors
    deadline = time.monotonic() + config.retry_max_seconds
    try:
        target = config.map_url(url)
        for attempt in range(config.max_tries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Retry deadline exhausted')
            try:
                async with asyncio.timeout(min(config.request_timeout_seconds, remaining)):
                    status, body, charset, retry_after = await _request(
                        session, 'GET', target, config, original_url=url, attempt=attempt + 1)
                if not 200 <= status < 300:
                    raise _HTTPError(status, _retry_after(retry_after))
                soup = BeautifulSoup(body, 'html.parser', from_encoding=charset)
                results = {}
                for selector in selectors:
                    element = soup.select_one(selector)
                    results[selector] = str(element) if element is not None else None
                return {'scraped_url': url, 'scrape_results': results}
            except (aiohttp.ClientError, TimeoutError, _HTTPError) as exc:
                if isinstance(exc, _HTTPError) and not exc.retryable:
                    raise
                if attempt + 1 >= config.max_tries:
                    raise
                delay = max(config.delay(attempt), exc.retry_after if isinstance(exc, _HTTPError) else 0)
                if delay >= deadline - time.monotonic():
                    raise TimeoutError(f'Retry deadline exhausted after {exc}') from exc
                await asyncio.sleep(delay)
    except (aiohttp.ClientError, TimeoutError, ValueError, WebPageLoadError) as exc:
        raise WebPageLoadError(f'Could not scrape {url}: {type(exc).__name__}: {exc}') from exc


async def async_http_webscrape(url: str, query_selectors: str | list, *, transport: TransportConfig | None = None) -> dict:
    config = transport or TransportConfig()
    async with _session(config) as session:
        return await _scrape(session, url, query_selectors, config)


def sync_http_webscrape(url: str, query_selectors: str | list, *, transport: TransportConfig | None = None) -> dict:
    return _run_sync(async_http_webscrape, url, query_selectors, transport=transport)


async def async_scrape_url_list(scrape_instructions_list: list[ScrapeInstruction], *, transport: TransportConfig | None = None):
    config = transport or TransportConfig()
    semaphore = asyncio.Semaphore(10)
    async with _session(config) as session:
        async def scrape(instruction):
            async with semaphore:
                return await _scrape(session, instruction.url, instruction.query_selectors, config)
        tasks = [asyncio.create_task(scrape(instruction)) for instruction in scrape_instructions_list]
        try:
            return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
