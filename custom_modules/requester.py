import asyncio
import json

import backoff
import aiohttp
from aiohttp import ClientError
from dotenv import load_dotenv
import os
from loguru import logger

# @backoff.on_exception(
#     backoff.expo,
#     exception=(ClientError),
#     max_tries=3)
# async def get_from_narou_db_api(url: str):
#     # Get url and auth key
#     auth_token = os.getenv('AUTH_TOKEN')
#
#     headers = {
#         "Authorization": f"{auth_token}",
#     }
#
#     async with aiohttp.ClientSession() as session:
#         result = await session.get(url=url, headers=headers)
#         if result.status != 200:
#             logger.error(f'Request generated an abnormal response code: {result.status}')
#             logger.error(f'{result.content}')
#             raise ClientError
#         text = await result.text()
#         body = await result.json()
#     # print(result)
#     print(text)
#     # print(body)
#     logger.success(f'Function complete')
#
#     return body


@backoff.on_exception(
    backoff.expo,
    exception=(ClientError),
    max_tries=3)
async def get_index_from_d1_db_api():
    # Get url and auth key
    auth_token = os.getenv('AUTH_TOKEN')
    url = f'{os.getenv("D1_API_URL")}{os.getenv("D1_API_GET_INDEX_PATH")}'

    headers = {
        "Authorization": f"{auth_token}",
    }

    async with aiohttp.ClientSession() as session:
        result = await session.get(url=url, headers=headers)
        if result.status != 200:
            logger.error(f'Request generated an abnormal response code: {result.status}')
            logger.error(f'{result.content}')
            raise ClientError
        body = await result.json()

    return body

@backoff.on_exception(
    backoff.expo,
    exception=(ClientError),
    max_tries=3)
async def post_to_index_on_d1_db_api(index_entry: dict):
    # Get url and auth key
    auth_token = os.getenv('AUTH_TOKEN')
    url = f'{os.getenv("D1_API_URL")}{os.getenv("D1_API_PUT_INDEX_PATH")}'

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

    print(f'STATUS CODE: {result.status}')
    print(text)


@backoff.on_exception(
    backoff.expo,
    exception=(ClientError),
    max_tries=1)
async def post_chapter_to_d1_db_api(chapter_entry: dict):
    # Get url and auth key
    auth_token = os.getenv('AUTH_TOKEN')
    url = f'{os.getenv("D1_API_URL")}{os.getenv("D1_API_PUT_CHAPTER_PATH")}'

    headers = {
        "Authorization": f"{auth_token}",
    }

    content = json.dumps(chapter_entry)

    async with aiohttp.ClientSession() as session:
        result = await session.put(url=url, headers=headers, data=content)
        if result.status != 200:
            logger.error(f'Request generated an abnormal response code: {result.status}')
            logger.error(f'{result.content}')
            raise ClientError
        text = await result.text()

    print(f'STATUS CODE: {result.status}')
    print(text)


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(get_index_from_d1_db_api())

