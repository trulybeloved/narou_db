import asyncio
import json
import os
from X_scrape_narou_index import scrape_narou_index
from custom_modules.narou_parser import parse_narou_index_html
from custom_modules.utilities import get_current_unix_timestamp, Git
from custom_modules.webscraper import ScrapeInstruction, async_scrape_url_list
from autoscrape_autoupdate import NAROU_INDEX_SELECTOR, INDEX_ENTRY_SELECTOR, ENTRY_PUBLISHED_TIMESTAMP_SELECTOR, CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR

async def scrape_all_chapters():
    scrape_timestamp = get_current_unix_timestamp()
    index_scrape_results = await scrape_narou_index()

    complete_index_parse_results = []

    for index_page in index_scrape_results:
        if index_page['scrape_results']['.p-eplist']:
            parse_results = parse_narou_index_html(index_page['scrape_results'][NAROU_INDEX_SELECTOR], INDEX_ENTRY_SELECTOR, ENTRY_PUBLISHED_TIMESTAMP_SELECTOR)
            for parse_result in parse_results:
                parse_result['scraped_timestamp'] = scrape_timestamp
                complete_index_parse_results.append(parse_result)

    chapter_links = [parse_result['narou_link'] for parse_result in complete_index_parse_results]

    query_selectors = [CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR]

    instructions_list = [ScrapeInstruction(url, query_selectors) for url in chapter_links]

    scrape_results = await async_scrape_url_list(instructions_list)

    with open('datastores/chapter_scrape_results.json', 'w', encoding='utf-8') as json_file:
        json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))

    return scrape_results

if __name__ == "__main__":
    asyncio.run(scrape_all_chapters())
    # Git.git_commit_all(os.getcwd(), 'automated commit')
    # Git.git_push(os.getcwd(), 'master')
