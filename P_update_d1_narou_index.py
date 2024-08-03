import asyncio
import json
import os

from custom_modules.utilities import get_current_unix_timestamp, to_filename_friendly, list_files
from custom_modules.narou_parser import parse_narou_index_html, parse_narou_chapter_html
from custom_modules.requester import get_index_from_d1_db_api, post_to_index_on_d1_db_api, post_chapter_to_d1_db_api
from custom_modules.webscraper import ScrapeInstruction, async_scrape_url_list
from dotenv import load_dotenv

async def main():

    load_dotenv()

    with open('datastores/index_scrape_results.json', 'r', encoding='utf-8') as index_scrape_file:
        index_scrape_results = json.loads(index_scrape_file.read())

    local_index = []

    for index_page in index_scrape_results:
        if index_page['scrape_results']['.index_box']:
            parse_results = parse_narou_index_html(index_page['scrape_results']['.index_box'])
            for parse_result in parse_results:
                parse_result['scraped_timestamp'] = get_current_unix_timestamp()
                local_index.append(parse_result)
    print('Local Index compiled')

    try:
        remote_index = await get_index_from_d1_db_api()
        print('Remote Index obtained')
    except Exception as e:
        raise e

    print(local_index[0])
    # print(remote_index[0])

    local_index = sorted(local_index, key=lambda x: x['chapter_uid'])
    remote_index = sorted(remote_index, key=lambda x: x['chapter_uid'])

    processed_remote_index = []
    for entry in remote_index:
        del entry['id']
        del entry['upload_timestamp']
        processed_remote_index.append(entry)

    # print(processed_remote_index[0])

    mismatched_entries = []
    for i in range(0, len(local_index)-1):
        try:
            if local_index[i] == remote_index[i]:
                continue
            else:
                mismatched_entries.append(local_index[i])
        except IndexError as e:
            mismatched_entries.append(local_index[i])
            continue

    # print(mismatched_entries)
    print(f'Mismatched Entry Count = {len(mismatched_entries)}')

    urls_to_scrape = [index_entry['narou_link'] for index_entry in mismatched_entries]
    # print(urls_to_scrape)

    query_selectors = ['.novel_subtitle', '#novel_honbun']

    instructions_list = [ScrapeInstruction(url, query_selectors) for url in urls_to_scrape]

    chapter_scrape_timestamp = get_current_unix_timestamp()
    scrape_results = await async_scrape_url_list(instructions_list)

    for scrape_result in scrape_results:
        with open(f'datastores/chapters/{to_filename_friendly(scrape_result["scraped_url"])}.json', 'w', encoding='utf-8') as chapter_scrape_savefile:
            chapter_scrape_savefile.write(json.dumps(scrape_result))

    # chapter_file_list = list_files(os.path.join(os.getcwd(), 'datastores', 'chapters'))
    #
    # scrape_results = []
    # for chapter_file in chapter_file_list:
    #     with open(chapter_file, 'r', encoding='utf-8') as chapter_scrape_json:
    #         scrape_results.append(json.loads(chapter_scrape_json.read()))

    chapter_parse_results = [parse_narou_chapter_html(scrape_result) for scrape_result in scrape_results]

    for chapter_parse_result in chapter_parse_results:
        chapter_parse_result['scraped_timestamp'] = chapter_scrape_timestamp

    print(chapter_parse_results[0])

    # Update Chapters
    tasks = [post_chapter_to_d1_db_api(chapter_parse_result) for chapter_parse_result in chapter_parse_results]
    await asyncio.gather(*tasks)

    # Update Index
    tasks = [post_to_index_on_d1_db_api(index_entry) for index_entry in mismatched_entries]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
