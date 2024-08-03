from requester import post_to_narou_db_api
import json

from custom_modules.narou_parser import parse_narou_index_html, parse_narou_chapter_html
from custom_modules.utilities import get_current_unix_timestamp

import asyncio
import json
import pprint

from dotenv import load_dotenv
from custom_modules.webscraper import async_playwright_webscrape

class ScrapeInstruction:

    def __init__(self, scrape_url: str, query_selectors: list):
        self.url = scrape_url
        self.query_selectors = query_selectors

async def async_scrape_url_list(scrape_instructions_list: list[ScrapeInstruction]):

    tasks = [async_playwright_webscrape(instruction.url, instruction.query_selectors) for instruction in scrape_instructions_list]
    resutls = await asyncio.gather(*tasks)

    return resutls

async def main():

    load_dotenv()

    url_list = [
        'https://ncode.syosetu.com/n2267be/?p=1',
        'https://ncode.syosetu.com/n2267be/?p=2',
        'https://ncode.syosetu.com/n2267be/?p=3',
        'https://ncode.syosetu.com/n2267be/?p=4',
        'https://ncode.syosetu.com/n2267be/?p=5',
        'https://ncode.syosetu.com/n2267be/?p=6',
        'https://ncode.syosetu.com/n2267be/?p=7',
        'https://ncode.syosetu.com/n2267be/?p=8',
    ]
    query_selectors = ['.index_box']

    instructions_list = [ScrapeInstruction(url, query_selectors) for url in url_list]

    scrape_timestamp = get_current_unix_timestamp()
    scrape_results = await async_scrape_url_list(instructions_list)

    with open('datastores/index_scrape_resuslts.json', 'w', encoding='utf-8') as json_file:
        json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))

    with open('datastores/index_scrape_resuslts.json', 'r', encoding='utf-8') as index_scrape_file:
        index_scrape_results = json.loads(index_scrape_file.read())

    complete_index_parse_results = []

    for index_page in index_scrape_results:
        if index_page['scrape_results']['.index_box']:
            parse_results = parse_narou_index_html(index_page['scrape_results']['.index_box'])
            for parse_result in parse_results:
                parse_result['scrape_timestamp'] = scrape_timestamp
                complete_index_parse_results.append(parse_result)

    cf_worker_url = 'http://127.0.0.1:8787/update/index'

    chapter_links = [parse_result['chapter_url'] for parse_result in complete_index_parse_results]
    print(chapter_links)

    tasks = [post_to_narou_db_api(cf_worker_url, index_entry) for index_entry in complete_index_parse_results]

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())