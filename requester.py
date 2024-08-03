import asyncio
import json

import backoff
import aiohttp
from aiohttp import ClientError
from dotenv import load_dotenv
import os
from loguru import logger


@backoff.on_exception(
    backoff.expo,
    exception=(ClientError),
    max_tries=3)
async def get_from_narou_db_api(url: str):
    # Get url and auth key
    auth_token = os.getenv('AUTH_TOKEN')

    headers = {
        "Authorization": f"{auth_token}",
    }

    async with aiohttp.ClientSession() as session:
        result = await session.get(url=url, headers=headers)
        if result.status != 200:
            logger.error(f'Request generated an abnormal response code: {result.status}')
            logger.error(f'{result.content}')
            raise ClientError
        text = await result.text()
    print(result)
    print(text)
    logger.success(f'Function complete')


@backoff.on_exception(
    backoff.expo,
    exception=(ClientError),
    max_tries=3)
async def post_to_narou_db_api(url: str, index_entry: dict):
    # Get url and auth key
    auth_token = os.getenv('AUTH_TOKEN')

    headers = {
        "Authorization": f"{auth_token}",
    }

    content = json.dumps(index_entry)

    async with aiohttp.ClientSession() as session:
        result = await session.put(url=url, headers=headers, data=content)
        if result.status != 200:
            logger.error(f'Request generated an abnormal response code: {result.status}')
            logger.error(f'{result.content}')
            raise ClientError
        text = await result.text()
    # print(result)
    print(f'STATUS CODE: {result.status}')
    print(text)

if __name__ == "__main__":
    load_dotenv()

    url = 'http://127.0.0.1:8787/query/index'
    # url = 'https://rzwndb.rekai.app/'
    # url = 'https://rz-wn-dbm.toshiroakari.workers.dev/'
    index_entry = {
        'uid': 698,
        'title': '第九章４\u3000\u3000『オタンコナス』',
        'chapter_url': 'https://ncode.syosetu.com/n2267be/698/',
        'publication_timestamp': 1722376800,
        'chapter_edited': 0,
        'edit_timestamp': 0,
        'scrape_timestamp': 1234,
        'upload_timestamp': 1234}

    asyncio.run(get_from_narou_db_api(url))
    # asyncio.run(post_to_narou_db_api(index_entry))
