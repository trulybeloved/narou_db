"""Browser scraping with an optional, isolated mock transport."""

import asyncio
from dataclasses import dataclass
import random
import time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright, Browser
import requests

from custom_modules.custom_exceptions import WebPageLoadError

STATUS_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}


@dataclass(frozen=True)
class TransportConfig:
    mock_origin: str | None = None
    navigation_timeout_ms: int = 0
    max_tries: int = 4
    retry_base_seconds: float = 1
    retry_max_seconds: float = 120
    jitter: bool = True
    trace_dir: Path | None = None

    def __post_init__(self):
        if self.max_tries < 1 or self.navigation_timeout_ms < 0 or self.retry_base_seconds < 0 or self.retry_max_seconds < 0:
            raise ValueError('Invalid timeout or retry configuration')
        if self.mock_origin:
            parsed = urlsplit(self.mock_origin)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise ValueError('mock_origin must be an HTTP origin without a path')

    def allows(self, url):
        if not self.mock_origin:
            return True
        parsed, allowed = urlsplit(url), urlsplit(self.mock_origin)
        return (parsed.scheme, parsed.hostname, parsed.port) == (allowed.scheme, allowed.hostname, allowed.port) and not parsed.username and not parsed.password

    def map_url(self, url):
        if not self.mock_origin:
            return url
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) == ('https', 'ncode.syosetu.com'):
            target = urlsplit(self.mock_origin)
            return urlunsplit((target.scheme, target.netloc, parsed.path, parsed.query, parsed.fragment))
        if self.allows(url):
            return url
        raise ValueError(f'Blocked request outside the mock origin: {url}')

    def delay(self, attempt):
        delay = self.retry_base_seconds * 2 ** attempt
        return random.uniform(0, delay) if self.jitter else delay


class ScrapeInstruction:
    def __init__(self, scrape_url: str, query_selectors: list):
        self.url = scrape_url
        self.query_selectors = query_selectors


def get_http_response_status(url: str, *, transport: TransportConfig | None = None) -> int:
    config = transport or TransportConfig()
    target = config.map_url(url)
    try:
        if not config.mock_origin:
            return requests.head(target, headers=STATUS_HEADERS,
                                 timeout=config.navigation_timeout_ms / 1000 or None).status_code
        with requests.Session() as session:
            if config.mock_origin:
                session.trust_env = False
            for _ in range(10):
                if not config.allows(target):
                    raise ValueError('Blocked redirect outside the mock origin')
                response = session.head(target, headers=STATUS_HEADERS, allow_redirects=False,
                                        timeout=config.navigation_timeout_ms / 1000 or None)
                if response.status_code in (301, 302, 303, 307, 308) and 'Location' in response.headers:
                    target = urljoin(target, response.headers['Location'])
                else:
                    return response.status_code
            return 0
    except requests.RequestException:
        return 0


async def async_get_http_response_status(url: str, *, transport: TransportConfig | None = None) -> int:
    config = transport or TransportConfig()
    target = config.map_url(url)
    try:
        timeout = {'timeout': aiohttp.ClientTimeout(total=config.navigation_timeout_ms / 1000)} if config.navigation_timeout_ms else {}
        async with aiohttp.ClientSession(headers=STATUS_HEADERS, **timeout) as session:
            if not config.mock_origin:
                async with session.head(target) as response:
                    return response.status
            for _ in range(10):
                if not config.allows(target):
                    raise ValueError('Blocked redirect outside the mock origin')
                async with session.head(target, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308) and 'Location' in response.headers:
                        target = urljoin(target, response.headers['Location'])
                    else:
                        return response.status
            return 0
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return 0


async def async_playwright_webscrape(url: str, query_selectors: str | list,
                                    semaphore: asyncio.Semaphore | None = None,
                                    browser: Browser = None, *, transport: TransportConfig | None = None) -> dict:
    config = transport or TransportConfig()
    target = config.map_url(url)
    selectors = [query_selectors] if isinstance(query_selectors, str) else query_selectors
    started = time.monotonic()
    async with semaphore or asyncio.Semaphore(10):
        for attempt in range(config.max_tries):
            try:
                async with async_playwright() as playwright:
                    owned = browser is None
                    active = browser or await playwright.chromium.launch(headless=True, timeout=config.navigation_timeout_ms)
                    context = await active.new_context(service_workers='block' if config.mock_origin else 'allow')
                    try:
                        if config.trace_dir:
                            await context.tracing.start(screenshots=True, snapshots=True, sources=True)
                        if config.mock_origin:
                            async def guard(route):
                                target_url = route.request.url
                                # Browser routing alone does not intercept every hop
                                # of a redirect chain. Resolve hops without following
                                # redirects automatically, checking each destination.
                                for _ in range(10):
                                    if not config.allows(target_url):
                                        await route.abort()
                                        return
                                    response = await route.fetch(url=target_url, max_redirects=0,
                                                                 timeout=config.navigation_timeout_ms)
                                    if response.status in (301, 302, 303, 307, 308) and 'location' in response.headers:
                                        target_url = urljoin(target_url, response.headers['location'])
                                        await response.dispose()
                                    else:
                                        await route.fulfill(response=response)
                                        await response.dispose()
                                        return
                                await route.abort()
                            await context.route('**/*', guard)
                        page = await context.new_page()
                        await page.goto(target, timeout=config.navigation_timeout_ms, wait_until='domcontentloaded')
                        results = {}
                        for selector in selectors:
                            element = await page.query_selector(selector)
                            results[selector] = await element.evaluate('e => e.outerHTML') if element else None
                        return {'scraped_url': url, 'scrape_results': results}
                    finally:
                        try:
                            if config.trace_dir:
                                Path(config.trace_dir).mkdir(parents=True, exist_ok=True)
                                await context.tracing.stop(path=str(Path(config.trace_dir) / f'{uuid4().hex}.zip'))
                        finally:
                            await context.close()
                            if owned:
                                await active.close()
            except Exception as exc:
                elapsed = time.monotonic() - started
                if attempt + 1 >= config.max_tries or elapsed >= config.retry_max_seconds:
                    raise WebPageLoadError(f'Could not scrape {url}: {exc}') from exc
                await asyncio.sleep(min(config.delay(attempt), max(0, config.retry_max_seconds - elapsed)))


def sync_playwright_webscrape(url: str, query_selectors: str | list, *, transport: TransportConfig | None = None) -> dict:
    config = transport or TransportConfig()
    target = config.map_url(url)
    selectors = [query_selectors] if isinstance(query_selectors, str) else query_selectors
    started = time.monotonic()
    for attempt in range(config.max_tries):
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, timeout=config.navigation_timeout_ms)
                try:
                    context = browser.new_context(service_workers='block' if config.mock_origin else 'allow')
                    if config.mock_origin:
                        def guard(route):
                            target_url = route.request.url
                            for _ in range(10):
                                if not config.allows(target_url):
                                    route.abort()
                                    return
                                response = route.fetch(url=target_url, max_redirects=0, timeout=config.navigation_timeout_ms)
                                if response.status in (301, 302, 303, 307, 308) and 'location' in response.headers:
                                    target_url = urljoin(target_url, response.headers['location'])
                                    response.dispose()
                                else:
                                    route.fulfill(response=response)
                                    response.dispose()
                                    return
                            route.abort()
                        context.route('**/*', guard)
                    page = context.new_page()
                    page.goto(target, timeout=config.navigation_timeout_ms, wait_until='domcontentloaded')
                    results = {}
                    for selector in selectors:
                        element = page.query_selector(selector)
                        results[selector] = element.evaluate('e => e.outerHTML') if element else None
                    return {'scraped_url': url, 'scrape_results': results}
                finally:
                    browser.close()
        except Exception as exc:
            elapsed = time.monotonic() - started
            if attempt + 1 >= config.max_tries or elapsed >= config.retry_max_seconds:
                raise WebPageLoadError(f'Could not scrape {url}: {exc}') from exc
            time.sleep(min(config.delay(attempt), max(0, config.retry_max_seconds - elapsed)))


async def async_scrape_url_list(scrape_instructions_list: list[ScrapeInstruction], *, transport: TransportConfig | None = None):
    semaphore = asyncio.Semaphore(10)
    tasks = [asyncio.create_task(async_playwright_webscrape(instruction.url, instruction.query_selectors, semaphore, transport=transport))
             for instruction in scrape_instructions_list]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
