"""Compatibility entry point for one SQLite-backed update run."""

import asyncio

from autoscrape_autoupdate import update_once


if __name__ == '__main__':
    asyncio.run(update_once())
