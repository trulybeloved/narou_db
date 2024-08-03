import typing
import asyncio
import sys
import requests
from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright, Browser
from loguru import logger
import aiohttp
import backoff
from custom_modules.custom_exceptions import WebPageLoadError


class ScrapeInstruction:

    def __init__(self, scrape_url: str, query_selectors: list):
        self.url = scrape_url
        self.query_selectors = query_selectors


def get_http_response_status(url: str) -> int:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        }
        response = requests.head(url, headers=headers)
        return response.status_code
    except requests.RequestException as e:
        logger.exception(f'There was an exception while sending an http request for url: {url} \n Exception: {e}')
        return 0


async def async_get_http_response_status(url: str) -> int:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.head(url) as response:
                return response.status
    except aiohttp.ClientError as e:
        logger.exception(f'There was an exception while sending an http request for url: {url} \n Exception: {e}')
        return 0


@backoff.on_exception(
    backoff.expo,
    exception=(WebPageLoadError, Exception),
    max_tries=4,
    max_time=120)
async def async_playwright_webscrape(
        url: str,
        query_selectors: typing.Union[str, list],
        semaphore: asyncio.Semaphore = asyncio.Semaphore(value=10),
        browser: Browser = None) -> dict:
    if isinstance(query_selectors, str):
        query_selectors = [query_selectors]

    results = {'scraped_url': url}
    scrape_results = {}
    async with semaphore:
        async with async_playwright() as playwright:
            if not browser:
                browser = await playwright.chromium.launch(headless=True, timeout=0)
                browser_launched_within_function = True
            else:
                browser_launched_within_function = False

            page = await browser.new_page()

            try:
                print(f'Scraping {url}')
                await page.goto(url=url, timeout=0, wait_until='domcontentloaded')

            except Exception as e:
                logger.error(f'There was an error while loading the page for {url}: {e}')
                if browser_launched_within_function:
                    await browser.close()
                raise WebPageLoadError()

            for query_selector in query_selectors:
                element = await page.query_selector(query_selector)

                if element:
                    outer_html = await element.evaluate('element => element.outerHTML')
                    scrape_results[query_selector] = outer_html
                else:
                    scrape_results[query_selector] = None

            if browser_launched_within_function:
                await browser.close()

    results['scrape_results'] = scrape_results
    return results


@backoff.on_exception(
    backoff.expo,
    exception=(WebPageLoadError),
    max_tries=4,
    max_time=120)
def sync_playwright_webscrape(
        url: str,
        query_selectors: typing.Union[str, list]) -> dict:
    if isinstance(query_selectors, str):
        query_selectors = [query_selectors]

    results = {'scraped_url': url}
    scrape_results = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=0)
        page = browser.new_page()

        try:
            page.goto(url=url, timeout=0, wait_until='domcontentloaded')

        except Exception as e:
            logger.error(f'There was an error while loading the page for {url}: {e}')
            browser.close()
            raise WebPageLoadError()

        for query_selector in query_selectors:
            element = page.query_selector(query_selector)

            if element:
                outer_html = element.evaluate('element => element.outerHTML')
                scrape_results[query_selector] = outer_html
            else:
                scrape_results[query_selector] = None

        browser.close()

    results['scrape_results'] = scrape_results
    return results


async def async_scrape_url_list(scrape_instructions_list: list[ScrapeInstruction]):
    tasks = [async_playwright_webscrape(instruction.url, instruction.query_selectors) for instruction in
             scrape_instructions_list]
    results = await asyncio.gather(*tasks)

    return results


if __name__ == "__main__":
    index_url = "https://ncode.syosetu.com/n2267be/?p=7"
    # index_scrape_results = asyncio.run(async_playwright_webscrape(index_url, ['.index_box']))
    index_scrape_results = sync_playwright_webscrape(index_url, ['.index_box'])
    print(index_scrape_results)
