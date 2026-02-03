"""Utility functions for Tempo to Unit4 sync."""

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Callable, TypeVar

T = TypeVar("T")

# File paths
SESSION_FILE = "session.json"
CONFIG_FILE = "config.json"
MAPPING_FILE = "account_to_arbauft_mapping.json"


def load_config() -> dict:
    """Load config.json with Jira and Tempo credentials."""
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_mapping() -> dict:
    """Load account-to-arbauft mapping."""
    if os.path.exists(MAPPING_FILE):
        with open(MAPPING_FILE) as f:
            return json.load(f)
    return {}


def save_mapping(mapping: dict) -> None:
    """Save account-to-arbauft mapping."""
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def get_week_dates(week_str: str) -> tuple[str, str]:
    """Get start and end date (Mon-Sun) for a week string YYYYWW."""
    year = int(week_str[:4])
    week = int(week_str[4:])
    # ISO week: Jan 4 is always in week 1
    jan4 = datetime(year, 1, 4)
    start_of_week1 = jan4 - timedelta(days=jan4.weekday())
    week_start = start_of_week1 + timedelta(weeks=week - 1)
    week_end = week_start + timedelta(days=6)
    return week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")


def get_current_week() -> str:
    """Get current week as YYYYWW."""
    return datetime.now().strftime("%G%V")


async def retry_async(
    operation: Callable[[], T],
    max_attempts: int = 3,
    delay: float = 1.0,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T | None:
    """Execute an async operation with retries.

    Args:
        operation: Async callable to execute
        max_attempts: Maximum number of attempts
        delay: Delay in seconds between attempts
        on_retry: Optional callback when retrying (attempt_num, exception)

    Returns:
        Result of operation or None if all attempts failed
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                if on_retry:
                    on_retry(attempt + 1, e)
                await asyncio.sleep(delay)
    return None
