import asyncio
import json
import os

from custom_modules.utilities import get_current_unix_timestamp, to_filename_friendly, list_files, sleep_with_progress
from custom_modules.narou_parser import parse_narou_index_html, parse_narou_chapter_html
from custom_modules.requester import get_index_from_d1_db_api, post_to_index_on_d1_db_api, post_chapter_to_d1_db_api
from custom_modules.webscraper import ScrapeInstruction, async_scrape_url_list
from dotenv import load_dotenv

async def main():

    load_dotenv()

    TERMINATE = False

    while not TERMINATE:

        url_list = [
            'https://ncode.syosetu.com/n2267be/?p=1',
            'https://ncode.syosetu.com/n2267be/?p=2',
            'https://ncode.syosetu.com/n2267be/?p=3',
            'https://ncode.syosetu.com/n2267be/?p=4',
            'https://ncode.syosetu.com/n2267be/?p=5',
            'https://ncode.syosetu.com/n2267be/?p=6',
            'https://ncode.syosetu.com/n2267be/?p=7',
            'https://ncode.syosetu.com/n2267be/?p=8',
            'https://ncode.syosetu.com/n2267be/?p=9',
        ]
        query_selectors = ['.index_box']

        instructions_list = [ScrapeInstruction(url, query_selectors) for url in url_list]

        scrape_timestamp = get_current_unix_timestamp()
        scrape_results = await async_scrape_url_list(instructions_list)

        with open('datastores/index_scrape_results.json', 'w', encoding='utf-8') as json_file:
            json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))

        with open('datastores/index_scrape_results.json', 'r', encoding='utf-8') as index_scrape_file:
            index_scrape_results = json.loads(index_scrape_file.read())

        local_index = []

        for index_page in index_scrape_results:
            if index_page['scrape_results']['.index_box']:
                parse_results = parse_narou_index_html(index_page['scrape_results']['.index_box'])
                for parse_result in parse_results:
                    parse_result['scraped_timestamp'] = scrape_timestamp
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

        print(processed_remote_index[0])

        mismatched_entries = []

        # Determine the length to iterate through
        min_length = min(len(local_index), len(remote_index))

        if min_length == 0:
            mismatched_entries = local_index
        else:
            # Compare elements in the range of the shorter list's length
            for local_entry, remote_entry in zip(local_index, remote_index):
                uid_check = bool(local_entry['chapter_uid'] == remote_entry['chapter_uid'])
                edit_check = bool(local_entry['chapter_edited'] == remote_entry['chapter_edited'])
                edit_timestamp_check = bool(local_entry['edit_timestamp'] == remote_entry['edit_timestamp'])

                if uid_check and edit_check and edit_timestamp_check:
                    continue
                else:
                    mismatched_entries.append(local_entry)

        # Add remaining elements from the longer list if there are any
        if len(local_index) > len(remote_index):
            mismatched_entries.extend(local_index[min_length:])
        elif len(remote_index) > len(local_index):
            mismatched_entries.extend(remote_index[min_length:])

        print(mismatched_entries)

        print(f'Mismatched Entry Count = {len(mismatched_entries)}')

        if mismatched_entries:

            urls_to_scrape = [index_entry['narou_link'] for index_entry in mismatched_entries]

            query_selectors = ['.novel_subtitle', '#novel_honbun']

            instructions_list = [ScrapeInstruction(url, query_selectors) for url in urls_to_scrape]

            chapter_scrape_timestamp = get_current_unix_timestamp()
            scrape_results = await async_scrape_url_list(instructions_list)

            for scrape_result in scrape_results:
                with open(f'datastores/chapters/{to_filename_friendly(scrape_result["scraped_url"])}.json', 'w', encoding='utf-8') as chapter_scrape_savefile:
                    chapter_scrape_savefile.write(json.dumps(scrape_result, ensure_ascii=False, indent=4))

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

        sleep_with_progress(600)


if __name__ == "__main__":
    asyncio.run(main())
