import asyncio
import json
import os

from custom_modules.utilities import Git
from custom_modules.webscraper import ScrapeInstruction, async_scrape_url_list

from autoscrape_autoupdate import NAROU_INDEX_SELECTOR

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

    query_selectors = [NAROU_INDEX_SELECTOR]

    instructions_list = [ScrapeInstruction(url, query_selectors) for url in url_list]

    scrape_results = await async_scrape_url_list(instructions_list)

    with open('datastores/index_scrape_results.json', 'w', encoding='utf-8') as json_file:
        json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))

    print('COMPLETED NAROU INDEX SCRAPE. JSON file saved to datastores/index_scrape_results.json')

    # Git.git_commit_all(os.getcwd(), 'automated commit')
    # Git.git_push(os.getcwd(), 'master')

    return scrape_results

if __name__ == "__main__":
    asyncio.run(scrape_narou_index())