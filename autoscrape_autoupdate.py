import asyncio
import json
import os
import time

from loguru import logger
from dotenv import load_dotenv
from discord_webhook import DiscordWebhook

from custom_modules.utilities import get_current_unix_timestamp, to_filename_friendly, list_files, sleep_with_progress, Git
from custom_modules.narou_parser import parse_narou_index_html, parse_narou_chapter_html
from custom_modules.requester import get_index_from_d1_db_api, post_to_index_on_d1_db_api, post_chapter_to_d1_db_api
from custom_modules.webscraper import ScrapeInstruction, async_scrape_url_list
from custom_modules.discord_integration import send_discord_message, DISCORD_WEBHOOK_URL
from U_save_chapters_as_txt import save_chapter
from V_file_compare import get_differences

NAROU_INDEX_SELECTOR = '.p-eplist'
INDEX_ENTRY_SELECTOR = '.p-eplist__sublist'
INDEX_LINK_TITLE_SELECTOR = '.p-eplist__subtitle'
ENTRY_PUBLISHED_TIMESTAMP_SELECTOR = '.p-eplist__update'
CHAPTER_TITLE_SELECTOR = '.p-novel__title'
CHAPTER_TEXT_SELECTOR = '.p-novel__body'

async def main():

    load_dotenv()

    send_discord_message('NarouDB autorun has been initiated', ping=False)
    discord_status_webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, content=f"Autorun loop started: <t:{get_current_unix_timestamp()}>")

    try:
        discord_status_webhook.execute()
    except Exception as e:
        logger.error(f'Error during discord webhook call: {e}')
        pass

    while True:

        skip_loop_flag = False

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

        query_selectors = [NAROU_INDEX_SELECTOR]

        instructions_list = [ScrapeInstruction(url, query_selectors) for url in url_list]

        scrape_timestamp = get_current_unix_timestamp()
        try:
            scrape_results = await async_scrape_url_list(instructions_list)
        except Exception as e:
            logger.error("SCRAPE WAS UNSUCESSFUL")
            skip_loop_flag = True

        if not skip_loop_flag:

            with open('datastores/index_scrape_results.json', 'w', encoding='utf-8') as json_file:
                json_file.write(json.dumps(scrape_results, ensure_ascii=False, indent=4))

            with open('datastores/index_scrape_results.json', 'r', encoding='utf-8') as index_scrape_file:
                index_scrape_results = json.loads(index_scrape_file.read())

            local_index = []

            for index_page in index_scrape_results:
                if index_page['scrape_results'][NAROU_INDEX_SELECTOR]:
                    parse_results = parse_narou_index_html(
                        index_html=index_page['scrape_results'][NAROU_INDEX_SELECTOR],
                        index_entry_class_name=INDEX_ENTRY_SELECTOR,
                        entry_published_timestamp_class_name=ENTRY_PUBLISHED_TIMESTAMP_SELECTOR)
                    # print(parse_results)
                    for parse_result in parse_results:
                        parse_result['scraped_timestamp'] = scrape_timestamp
                        local_index.append(parse_result)
            print('\nLocal Index compiled\n')

            if not local_index:
                raise ValueError

            try:
                remote_index = await get_index_from_d1_db_api()
                print('\nRemote Index obtained\n')
                print(remote_index[0])
            except Exception as e:
                logger.error('COULD NOT OBTAIN REMOTE INDEX FROM API. Exiting to next iteration')
                continue

            print(local_index[0])

            local_index = sorted(local_index, key=lambda x: x['chapter_uid'])
            remote_index = sorted(remote_index, key=lambda x: x['chapter_uid'])

            # print(local_index)

            processed_remote_index = []
            for entry in remote_index:
                del entry['id']
                del entry['upload_timestamp']
                processed_remote_index.append(entry)

            # print(processed_remote_index[-1])

            # CHECK FOR DIFFERENCES
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
                    elif not edit_check or not edit_timestamp_check:
                        local_entry['mismatch_type'] = 'edit'
                        mismatched_entries.append(local_entry)
                    else:
                        local_entry['mismatch_type'] = 'new'
                        mismatched_entries.append(local_entry)

            # Add remaining elements from the longer list if there are any
            if len(local_index) > len(remote_index):
                mismatched_entries.extend(local_index[min_length:])
            elif len(remote_index) > len(local_index):
                mismatched_entries.extend(remote_index[min_length:])

            print(f'\nMismatched Entries: {mismatched_entries}\n')

            print(f'\nMismatched Entry Count = {len(mismatched_entries)}\n')

            if mismatched_entries:

                # for entry in mismatched_entries:
                #     try:
                #         entry_details = f"UID: {entry['chapter_uid']}\n{entry['chapter_title']}\n{entry['narou_link']}"
                #         send_discord_message(message=f'NarouDB autorun has found mismatched entries:\n\n{entry_details}', ping=True)
                #     except:
                #         pass

                urls_to_scrape = [index_entry['narou_link'] for index_entry in mismatched_entries]

                query_selectors = [CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR]

                instructions_list = [ScrapeInstruction(url, query_selectors) for url in urls_to_scrape]

                chapter_scrape_timestamp = get_current_unix_timestamp()

                try:
                    scrape_results = await async_scrape_url_list(instructions_list)
                except:
                    logger.error('CHAPTER SCRAPE FAILED. Exiting to next iteration.')
                    continue

                for scrape_result in scrape_results:
                    with open(f'datastores/chapters/{to_filename_friendly(scrape_result["scraped_url"])}.json', 'w', encoding='utf-8') as chapter_scrape_savefile:
                        chapter_scrape_savefile.write(json.dumps(scrape_result, ensure_ascii=False, indent=4))


                # chapter_file_list = list_files(os.path.join(os.getcwd(), 'datastores', 'chapters'))
                #
                # scrape_results = []
                # for chapter_file in chapter_file_list:
                #     with open(chapter_file, 'r', encoding='utf-8') as chapter_scrape_json:
                #         scrape_results.append(json.loads(chapter_scrape_json.read()))

                chapter_parse_results = [parse_narou_chapter_html(scrape_result, CHAPTER_TITLE_SELECTOR, CHAPTER_TEXT_SELECTOR) for scrape_result in scrape_results]

                uids = []

                for scrape_result in scrape_results:

                    try:
                        save_sucess, narou_uid = save_chapter(scrape_result)
                        print('chapter saved')
                        uids.append(narou_uid)
                        # for entry in mismatched_entries:
                            # if entry['mismatch_type'] == 'edit':
                                # formatted_diff_string = get_differences(narou_uid)
                                # send_discord_message(f'CHAPTER EDITED: {narou_uid}', ping=False)
                                # time.sleep(1)
                                # send_discord_message(formatted_diff_string, ping=False)
                    except Exception as e:
                        send_discord_message('FAILED TO GET DIFF', ping=False)
                        print(e)

                repo = os.getcwd()
                Git.git_commit_all(repo, f'Chapter Update for {", ".join(map(str, uids))}')
                for chapter_parse_result in chapter_parse_results:
                    chapter_parse_result['scraped_timestamp'] = chapter_scrape_timestamp

                print(f'CHAPTER PARSE RESULTS:\n{chapter_parse_results}\n')

                # Update Chapters
                try:
                    tasks = [post_chapter_to_d1_db_api(chapter_parse_result) for chapter_parse_result in chapter_parse_results]
                    await asyncio.gather(*tasks)
                except:
                    logger.error('CHAPTER PUT TO CF DB FAILED. Exiting to next iteration.')
                    continue

                # Update Index
                try:
                    tasks = [post_to_index_on_d1_db_api(index_entry) for index_entry in mismatched_entries]
                    await asyncio.gather(*tasks)
                except:
                    logger.error('INDEX UPDATE ON CF DB FAILED. Exiting to next iteration.')
                    continue

            else:
                logger.info('No new/modified entries found')
                discord_status_webhook.content = f'Last successful autorun loop: <t:{get_current_unix_timestamp()}> (<t:{get_current_unix_timestamp()}:R>)'

                try:
                    discord_status_webhook.edit()
                except Exception as e:
                    try:
                        discord_status_webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, content=f"Autorun loop started: <t:{get_current_unix_timestamp()}>")
                        discord_status_webhook.execute()
                    except Exception as e:
                        pass
                # send_discord_message(message='Narou Index for Re:ZERO scraped. No new/modified entries found', ping=False)

        sleep_with_progress(900)


if __name__ == "__main__":
    asyncio.run(main())
    # pass