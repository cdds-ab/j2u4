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


def validate_config(config: dict) -> list[str]:
    """Validate config structure and return list of error messages.

    Returns:
        Empty list if valid, otherwise list of error messages.
    """
    errors = []

    # Check required sections
    for section in ["jira", "tempo", "unit4"]:
        if section not in config:
            errors.append(f"Missing section '{section}' in config.json")

    # Check Jira credentials
    if "jira" in config:
        for key in ["base_url", "user_email", "api_token"]:
            if not config["jira"].get(key):
                errors.append(f"Missing jira.{key}")

    # Check Tempo
    if "tempo" in config:
        if not config["tempo"].get("api_token"):
            errors.append("Missing tempo.api_token")

    # Check Unit4
    if "unit4" in config:
        if not config["unit4"].get("url"):
            errors.append("Missing unit4.url")

    return errors


def load_config_safe() -> dict | None:
    """Load config with user-friendly error messages.

    Returns:
        Config dict if valid, None if errors occurred.
    """
    if not os.path.exists(CONFIG_FILE):
        print("[!] ERROR: config.json not found!")
        print()
        print("    Create config.json based on config.example.json:")
        print("    $ cp config.example.json config.json")
        print("    $ nano config.json  # Fill in your credentials")
        print()
        return None

    try:
        config = load_config()
    except json.JSONDecodeError as e:
        print("[!] ERROR: config.json is not valid JSON!")
        print(f"    Line {e.lineno}, column {e.colno}: {e.msg}")
        print()
        print("    Check for missing commas, quotes, or brackets.")
        return None

    errors = validate_config(config)
    if errors:
        print("[!] ERROR: config.json is incomplete:")
        for err in errors:
            print(f"    - {err}")
        print()
        print("    See config.example.json for the required structure.")
        return None

    return config


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
