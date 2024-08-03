import asyncio
import json
from loguru import logger
import os

from custom_modules.webscraper import async_playwright_webscrape
from custom_modules.utilities import Git

class ScrapeInstruction:

    def __init__(self, scrape_url: str, query_selectors: list):
        self.url = scrape_url
        self.query_selectors = query_selectors

async def async_scrape_url_list(scrape_instructions_list: list[ScrapeInstruction]):


    tasks = [async_playwright_webscrape(instruction.url, instruction.query_selectors) for instruction in scrape_instructions_list]
    resutls = await asyncio.gather(*tasks)

    return resutls

async def scrape_narou_index():
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

    scrape_results = await async_scrape_url_list(instructions_list)

    with open('datastores/index_scrape_results.json', 'w', encoding='utf-8') as json_file:
        json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))

    print('COMPLETED NAROU INDEX SCRAPE. JSON file saved to datastores/index_scrape_results.json')




# async def main():
#     url_list = [
#         'https://ncode.syosetu.com/n2267be/1/',
#     ]
#     query_selectors = ['.novel_subtitle', '#novel_honbun']
#
#     instructions_list = [ScrapeInstruction(url, query_selectors) for url in url_list]
#
#     scrape_results = await async_scrape_url_list(instructions_list)
#
#     with open('datastores/chapter_scrape_resuslt.json', 'w', encoding='utf-8') as json_file:
#         json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))
#
if __name__ == "__main__":
    asyncio.run(scrape_narou_index())