import os
import time
from datetime import datetime
from discord_webhook import DiscordWebhook, DiscordEmbed
from loguru import logger

from custom_modules.utilities import get_current_unix_timestamp, remove_whitespace
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
WCT_WEBHOOK_URL = os.getenv('WCT_WEBHOOK_URL')
PING_USER_ID = os.getenv('PING_USER_ID')

test_chapter_parse_results = {}
test_chapter_parse_results['chapter_text'] = "TEST TEXT"
test_chapter_parse_results['chapter_id'] = "TEST_ID"
test_chapter_parse_results['chapter_title'] = "TEST TITLE"
test_chapter_parse_results['narou_link'] = "https://ncode.syosetu.com/n2267be/685/"


def send_discord_message(message: str, ping: bool):
    unix_time_stamp = get_current_unix_timestamp()

    content = f'<t:{unix_time_stamp}>: {message}\n<@{PING_USER_ID}>' if ping else f'<t:{unix_time_stamp}>: {message}'

    webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, content=content)
    try:
        response = webhook.execute()
    except Exception as e:
        logger.error(f'Discord message could not be sent. Error: {e}')
        response = None
        return None

    return webhook


class RekaiWebhook:

    def __init__(self, webhook_name, webhook_url):
        pass


if __name__ == "__main__":
    pass
