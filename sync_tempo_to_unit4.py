"""
Sync Tempo worklogs to Unit4.

Usage:
    # Dry-run (default) - shows what would happen
    python sync_tempo_to_unit4.py 202605

    # Execute - actually creates entries
    python sync_tempo_to_unit4.py 202605 --execute

    # With cutover date (only sync from this date onwards)
    python sync_tempo_to_unit4.py 202605 --cutover 2026-01-29 --execute
"""

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from playwright.async_api import Frame, Page, async_playwright

# ============================================================================
# Constants
# ============================================================================

UNIT4_URL = "https://ubw.unit4cloud.com/YOUR_TENANT/Default.aspx"
SESSION_FILE = "session.json"
CONFIG_FILE = "config.json"
MAPPING_FILE = "account_to_arbauft_mapping.json"
ACCOUNT_FIELD = "customfield_10048"
TIMEOUT = 3000
DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class TempoWorklog:
    """A worklog entry from Tempo."""

    worklog_id: int
    issue_id: int
    issue_key: str
    issue_summary: str
    date: str  # YYYY-MM-DD
    hours: float
    description: str
    account_key: str | None
    account_name: str | None
    arbauft: str | None  # Mapped from account_key


@dataclass
class Unit4Entry:
    """An entry in Unit4."""

    ticketno: str
    arbauft: str
    text: str
    worklog_id: int | None  # Extracted from [WL:xxx] in text


# ============================================================================
# Config & Mapping
# ============================================================================


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


# ============================================================================
# Week Utilities
# ============================================================================


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


# ============================================================================
# Tempo API
# ============================================================================


def fetch_tempo_worklogs(config: dict, date_from: str, date_to: str) -> list[dict]:
    """Fetch worklogs from Tempo API for current user."""
    # Get my Jira account ID first
    base_url = config["jira"]["base_url"]
    email = config["jira"]["user_email"]
    token = config["jira"]["api_token"]
    tempo_token = config["tempo"]["api_token"]

    # Get my account ID
    r = requests.get(
        f"{base_url}/rest/api/3/myself",
        auth=(email, token),
        headers={"Accept": "application/json"},
        timeout=10,
    )
    r.raise_for_status()
    my_account_id = r.json()["accountId"]

    # Fetch worklogs from Tempo
    worklogs = []
    url = f"https://api.tempo.io/4/worklogs/user/{my_account_id}"
    params = {"from": date_from, "to": date_to, "limit": 1000}

    while url:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {tempo_token}", "Accept": "application/json"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()

        worklogs.extend(data.get("results", []))

        # Handle pagination
        url = data.get("metadata", {}).get("next")
        params = {}  # Clear params for pagination URLs

    return worklogs


# ============================================================================
# Jira API
# ============================================================================


def fetch_jira_issue(config: dict, issue_id: int) -> dict | None:
    """Fetch issue details from Jira (key, summary, account field)."""
    base_url = config["jira"]["base_url"]
    email = config["jira"]["user_email"]
    token = config["jira"]["api_token"]

    r = requests.get(
        f"{base_url}/rest/api/3/issue/{issue_id}",
        auth=(email, token),
        headers={"Accept": "application/json"},
        params={"fields": f"key,summary,{ACCOUNT_FIELD}"},
        timeout=10,
    )

    if r.status_code == 200:
        return r.json()
    return None


# ============================================================================
# Worklog Processing
# ============================================================================


def process_worklogs(
    config: dict, raw_worklogs: list[dict], mapping: dict
) -> tuple[list[TempoWorklog], list[TempoWorklog]]:
    """Process raw Tempo worklogs, fetch Jira details, apply mapping.

    Returns:
        - valid_worklogs: Worklogs with complete mapping
        - unmapped_worklogs: Worklogs with unknown account
    """
    valid_worklogs = []
    unmapped_worklogs = []

    # Cache for issue details
    issue_cache: dict[int, dict] = {}

    print(f"[*] Processing {len(raw_worklogs)} worklogs...")

    for wl in raw_worklogs:
        worklog_id = wl["tempoWorklogId"]
        issue_id = wl.get("issue", {}).get("id")
        date = wl["startDate"]
        hours = wl["timeSpentSeconds"] / 3600
        description = wl.get("description", "")

        # Fetch issue details if not cached
        if issue_id and issue_id not in issue_cache:
            issue_data = fetch_jira_issue(config, issue_id)
            if issue_data:
                fields = issue_data.get("fields", {})
                account_field = fields.get(ACCOUNT_FIELD)

                if isinstance(account_field, dict):
                    account_key = str(account_field.get("key") or account_field.get("id") or "")
                    account_name = account_field.get("name") or account_field.get("value") or ""
                else:
                    account_key = ""
                    account_name = ""

                issue_cache[issue_id] = {
                    "key": issue_data.get("key", f"ID:{issue_id}"),
                    "summary": fields.get("summary", "")[:100],
                    "account_key": account_key,
                    "account_name": account_name,
                }
            else:
                issue_cache[issue_id] = {
                    "key": f"ID:{issue_id}",
                    "summary": "?",
                    "account_key": "",
                    "account_name": "",
                }

        issue_info = issue_cache.get(issue_id, {})
        account_key = issue_info.get("account_key", "")

        # Apply mapping
        arbauft = None
        if account_key and account_key in mapping:
            arbauft = mapping[account_key]["unit4_arbauft"]

        worklog = TempoWorklog(
            worklog_id=worklog_id,
            issue_id=issue_id,
            issue_key=issue_info.get("key", "?"),
            issue_summary=issue_info.get("summary", "?"),
            date=date,
            hours=hours,
            description=description,
            account_key=account_key,
            account_name=issue_info.get("account_name", ""),
            arbauft=arbauft,
        )

        if arbauft:
            valid_worklogs.append(worklog)
        else:
            unmapped_worklogs.append(worklog)

    return valid_worklogs, unmapped_worklogs


def ask_for_arbauft(worklog: TempoWorklog, mapping: dict) -> str | None:
    """Interactively ask user for ArbAuft for an unmapped worklog."""
    print()
    print(f"  Unknown Account: {worklog.account_key} ({worklog.account_name})")
    print(f"    Ticket: {worklog.issue_key}")
    print(f"    Summary: {worklog.issue_summary[:60]}")
    print()
    print("  Enter ArbAuft (e.g., 1234-56789-001) or SKIP to skip: ", end="")

    arbauft = input().strip()

    if arbauft.upper() == "SKIP" or not arbauft:
        return None

    # Validate format
    if not re.match(r"^\d{4}-\d{5}-\d{3}$", arbauft):
        print(f"  [!] Invalid format '{arbauft}', expected: XXXX-XXXXX-XXX")
        return None

    # Save to mapping
    mapping[worklog.account_key] = {
        "unit4_arbauft": arbauft,
        "tempo_name": worklog.account_name or "?",
        "sample_ticket": worklog.issue_key,
    }
    save_mapping(mapping)
    print(f"  [+] Saved mapping: {worklog.account_key} -> {arbauft}")

    return arbauft


# ============================================================================
# Unit4 Browser Automation
# ============================================================================


async def get_content_frame(page: Page) -> Frame:
    """Get the iframe with actual content."""
    for frame in page.frames:
        if "ContentContainer" in frame.url:
            return frame
    return page.main_frame


async def login_and_navigate(page: Page, context) -> Frame:
    """Login to Unit4 and navigate to Zeiterfassung."""
    print("[*] Opening Unit4...")
    await page.goto(UNIT4_URL)
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(2)

    if "Login" in await page.title():
        print("[!] Please log in (2FA), then ENTER...")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await context.storage_state(path=SESSION_FILE)
        await asyncio.sleep(2)

    # Navigate to Zeiterfassung
    print("[*] Opening Zeiterfassung...")
    try:
        menu = page.get_by_text("Zeiterfassung - Standard", exact=True).first
        if await menu.count() > 0:
            await menu.click(timeout=5000)
    except Exception:
        print("[!] Navigate to Zeiterfassung manually, then ENTER...")
        await asyncio.get_event_loop().run_in_executor(None, input)

    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(3)

    return await get_content_frame(page)


async def set_week(frame: Frame, page: Page, week: str) -> bool:
    """Set the week in Unit4."""
    print(f"[*] Setting week {week}...", end=" ", flush=True)
    try:
        week_input = frame.get_by_label("Woche", exact=False).first
        if await week_input.count() > 0:
            await week_input.click(timeout=TIMEOUT, force=True)
            await week_input.press("Control+a")
            await week_input.type(week, delay=30)
            await page.keyboard.press("Tab")
            await asyncio.sleep(3)
            print("OK")
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False


async def extract_unit4_entries(frame: Frame) -> list[Unit4Entry]:
    """Extract current entries from Unit4 (looking for [WL:xxx] markers)."""
    entries = []
    seen_wl_ids: set[int] = set()  # Deduplicate by worklog_id

    try:
        rows = await frame.locator("tr").all()
        for row in rows:
            try:
                row_text = await row.inner_text(timeout=500)

                # Look for [WL:xxx] marker
                wl_match = re.search(r"\[WL:(\d+)\]", row_text)
                if not wl_match:
                    continue

                worklog_id = int(wl_match.group(1))

                # Skip if already seen (nested tr elements can match same text)
                if worklog_id in seen_wl_ids:
                    continue
                seen_wl_ids.add(worklog_id)

                # Look for ticket pattern
                ticket_match = re.search(r"([A-Z]{3,10}-\d+)", row_text)

                # Look for ArbAuft pattern
                arbauft_match = re.search(r"(\d{4}-\d{5}-\d{3})", row_text)

                if ticket_match and arbauft_match:
                    entries.append(
                        Unit4Entry(
                            ticketno=ticket_match.group(1),
                            arbauft=arbauft_match.group(1),
                            text=row_text[:100],
                            worklog_id=worklog_id,
                        )
                    )

            except Exception:
                continue

    except Exception as e:
        print(f"    Error extracting: {e}")

    return entries


async def read_zeitdetails_structure(frame: Frame) -> dict[str, str]:
    """Read the Zeitdetails table structure to understand which dates are available."""
    date_to_label = {}

    try:
        day_rows = await frame.locator("text=/^(Mo|Di|Mi|Do|Fr|Sa|So) \\d+\\/\\d+/").all()

        for row in day_rows:
            try:
                label = await row.inner_text(timeout=500)
                label = label.strip()

                match = re.match(r"^(Mo|Di|Mi|Do|Fr|Sa|So)\s+(\d+)/(\d+)", label)
                if match:
                    day_name, month, day = match.groups()
                    month = int(month)
                    day = int(day)

                    current_year = datetime.now().year
                    current_month = datetime.now().month
                    if month == 12 and current_month == 1:
                        year = current_year - 1
                    elif month == 1 and current_month == 12:
                        year = current_year + 1
                    else:
                        year = current_year

                    date_str = f"{year}-{month:02d}-{day:02d}"
                    date_to_label[date_str] = label
            except Exception:
                continue

    except Exception as e:
        print(f"    Error reading Zeitdetails structure: {e}")

    return date_to_label


async def expand_zeitdetails(frame: Frame, page: Page) -> bool:
    """Expand the Zeitdetails section if collapsed."""
    print("    Expanding Zeitdetails...", end=" ", flush=True)

    # Multiple patterns for day rows (different date formats)
    day_patterns = [
        "text=/^(Mo|Di|Mi|Do|Fr|Sa|So) \\d+\\/\\d+/",  # Mo 1/26
        "text=/^(Mo|Di|Mi|Do|Fr|Sa|So) \\d+\\.\\d+/",  # Mo 26.01
        "text=/^(Mo|Di|Mi|Do|Fr|Sa|So)\\s+\\d/",       # Mo 26
    ]

    async def check_expanded():
        for pattern in day_patterns:
            try:
                elem = frame.locator(pattern).first
                if await elem.count() > 0 and await elem.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    # First check if already expanded
    if await check_expanded():
        print("already open")
        return True

    # Find and click the Zeitdetails header (only once!)
    zeit_locators = [
        frame.locator("legend:has-text('Zeitdetails')").first,
        frame.locator("text=/[≫»▸▾].*Zeitdetails/").first,  # With expand icons
        frame.locator("text='Zeitdetails'").first,
        frame.locator("div:has-text('Zeitdetails')").first,
    ]

    for locator in zeit_locators:
        try:
            if await locator.count() > 0 and await locator.is_visible(timeout=500):
                text = await locator.inner_text(timeout=300)
                print(f"clicking '{text[:20]}'...", end=" ", flush=True)
                await locator.click(timeout=TIMEOUT)
                await asyncio.sleep(2)  # Wait longer for animation

                if await check_expanded():
                    print("OK")
                    return True
                else:
                    print("waiting...", end=" ", flush=True)
                    await asyncio.sleep(1)
                    if await check_expanded():
                        print("OK")
                        return True

                # Don't try other locators - we already clicked once
                break
        except Exception as e:
            continue

    # If still not expanded, return False but the caller will retry
    print("not expanded yet")
    return False


async def fill_hours_by_date(frame: Frame, page: Page, hours: float, date_str: str) -> bool:
    """Fill hours for a specific date in Zeitdetails."""
    hours_str = str(hours)

    # Try multiple times
    for attempt in range(5):
        if attempt > 0:
            print(f"    Retry {attempt}...", flush=True)
            await asyncio.sleep(1)

        # Try to expand Zeitdetails
        await expand_zeitdetails(frame, page)
        await asyncio.sleep(1.0)

        # Read structure (even if expand failed, try anyway)
        date_to_label = await read_zeitdetails_structure(frame)

        if date_str not in date_to_label:
            if attempt < 4:
                print(f"    Date {date_str} not in structure, retrying...", flush=True)
                # Try clicking Zeitdetails again
                zeit = frame.locator("text=/.*Zeitdetails/").first
                if await zeit.count() > 0:
                    await zeit.click(timeout=TIMEOUT)
                    await asyncio.sleep(1.5)
                continue
            print(f"    [!] Date {date_str} not found. Available: {list(date_to_label.keys())}")
            return False

        day_label = date_to_label[date_str]
        day_name = day_label.split()[0]

        print(f"    Zeitdetails ({day_name}): {hours_str}h ... ", end="", flush=True)

        try:
            day_cell = frame.locator(f"text=/^{day_name} \\d/").first
            if await day_cell.count() == 0:
                print(f"{day_name} not visible, retry...", flush=True)
                continue

            print(f"found row ... ", end="", flush=True)
            row = day_cell.locator("xpath=ancestor::tr[1]")

            # Find the editable cell (with numeric value like 0.00)
            all_cells = await row.locator("td").all()
            erfasst_cell = None

            for cell in reversed(all_cells):
                try:
                    if not await cell.is_visible(timeout=200):
                        continue
                    text = (await cell.inner_text(timeout=300)).strip()
                    if text and re.match(r"^[\d:,.]+$", text):
                        erfasst_cell = cell
                        print(f"cell '{text}' ... ", end="", flush=True)
                        break
                except Exception:
                    continue

            if not erfasst_cell:
                print("no cell visible, retry...", flush=True)
                continue

            # Double-click to edit
            await erfasst_cell.dblclick(timeout=TIMEOUT)
            await asyncio.sleep(0.5)

            # Find active input and fill
            active_input = frame.locator("input:focus").first
            if await active_input.count() == 0:
                active_input = frame.locator("input.x-form-field:visible").first
            if await active_input.count() == 0:
                active_input = frame.locator("input[type='text']:visible").last

            if await active_input.count() > 0:
                await active_input.fill(hours_str)
                await page.keyboard.press("Tab")
                await asyncio.sleep(0.3)
                print("OK")
                return True
            else:
                # Blind typing fallback
                await page.keyboard.press("Control+a")
                await page.keyboard.type(hours_str, delay=50)
                await page.keyboard.press("Tab")
                await asyncio.sleep(0.3)
                print("OK (blind)")
                return True

        except Exception as e:
            print(f"error: {e}, retry...", flush=True)
            continue

    print("FAILED after 5 attempts")
    return False


async def find_and_fill_by_label(frame: Frame, page: Page, label: str, value: str) -> bool:
    """Find input field by its label text and fill it."""
    # Also try with * suffix (required fields often have "Text*" etc)
    label_variants = [label, f"{label}*", f"{label} *"]

    for lbl in label_variants:
        strategies = [
            lambda l=lbl: frame.get_by_label(l, exact=False),
            lambda l=lbl: frame.locator(f"text='{l}'").locator("xpath=following::input[1]"),
            lambda l=lbl: frame.locator(f"text='{l}'")
            .locator("xpath=ancestor::*[.//input][1]//input")
            .first,
            # Also try textarea for multi-line text fields
            lambda l=lbl: frame.locator(f"text='{l}'").locator("xpath=following::textarea[1]"),
        ]

        for strategy in strategies:
            try:
                elem = strategy()
                if await elem.count() > 0:
                    if await elem.first.is_visible(timeout=1000):
                        await elem.first.click(timeout=TIMEOUT)
                        await asyncio.sleep(0.2)
                        await elem.first.press("Control+a")
                        await elem.first.fill(value, timeout=TIMEOUT)
                        await page.keyboard.press("Tab")
                        await asyncio.sleep(0.3)
                        return True
            except Exception:
                continue
    return False


async def find_and_click_button(frame: Frame, text: str) -> bool:
    """Find and click a button by text."""
    strategies = [
        lambda: frame.get_by_text(text, exact=True).first,
        lambda: frame.get_by_role("button", name=text),
        lambda: frame.locator(f"button:has-text('{text}')").first,
        lambda: frame.locator(f"a:has-text('{text}')").first,
        lambda: frame.locator(f"[value='{text}']").first,
    ]

    for strategy in strategies:
        try:
            elem = strategy()
            if await elem.count() > 0:
                if await elem.is_visible(timeout=1000):
                    await elem.click(timeout=TIMEOUT)
                    return True
        except Exception:
            continue
    return False


async def delete_entry_by_worklog_id(
    frame: Frame, page: Page, worklog_id: int, dry_run: bool
) -> bool:
    """Delete an entry by finding its row (via [WL:xxx] marker) and clicking delete."""
    print(f"    Deleting [WL:{worklog_id}]...", end=" ", flush=True)

    if dry_run:
        print("SKIPPED (dry-run)")
        return True

    try:
        # Find the row with this worklog ID
        row = frame.locator(f"tr:has-text('[WL:{worklog_id}]')").first
        if await row.count() == 0:
            print("row not found")
            return False

        # Click on the row to select it (try clicking the first cell/zoom icon)
        print("selecting row...", end=" ", flush=True)

        # Try to find and click the zoom/detail icon in this row first
        zoom_icon = row.locator("[title*='Detail']").first
        if await zoom_icon.count() > 0:
            await zoom_icon.click(timeout=TIMEOUT)
            print("opened detail...", end=" ", flush=True)
            await asyncio.sleep(1)

            # Now click "Löschen" in the dialog
            if await find_and_click_button(frame, "Löschen"):
                await asyncio.sleep(0.5)
                # Confirm if needed
                await find_and_click_button(frame, "Ja")
                await find_and_click_button(frame, "OK")
                await asyncio.sleep(0.5)
                print("OK")
                return True
            else:
                # Close dialog and try alternative
                await find_and_click_button(frame, "Abbrechen")
                await asyncio.sleep(0.3)

        # Alternative: Select row and use toolbar delete button
        await row.click(timeout=TIMEOUT)
        await asyncio.sleep(0.3)

        # Click "Löschen" button in toolbar
        if await find_and_click_button(frame, "Löschen"):
            await asyncio.sleep(0.5)
            # Confirm if needed (some UIs have confirmation dialogs)
            await find_and_click_button(frame, "Ja")
            await find_and_click_button(frame, "OK")
            await asyncio.sleep(0.3)
            print("OK")
            return True

    except Exception as e:
        print(f"error: {e}")

    print("FAILED")
    return False


async def add_unit4_entry(frame: Frame, page: Page, worklog: TempoWorklog, dry_run: bool) -> bool:
    """Add a single entry to Unit4."""
    # Use worklog description if available, otherwise fall back to issue summary
    description = worklog.description.strip() if worklog.description else worklog.issue_summary
    text = f"[WL:{worklog.worklog_id}] {description[:60]}"

    print(f"    {worklog.issue_key} | {worklog.hours}h | {worklog.date}...", end=" ", flush=True)

    if dry_run:
        print("SKIPPED (dry-run)")
        return True

    # Click "Ergänzen" to create new row
    if not await find_and_click_button(frame, "Ergänzen"):
        print("FAILED (no Ergänzen)")
        return False
    await asyncio.sleep(1)

    # Click first zoom icon (new row)
    try:
        zoom_icons = await frame.locator("[title*='Detail']").all()
        if zoom_icons:
            await zoom_icons[0].click(timeout=TIMEOUT)
        else:
            print("FAILED (no zoom)")
            return False
    except Exception as e:
        print(f"FAILED (zoom: {e})")
        return False
    await asyncio.sleep(1)

    # Fill form - ArbAuft first (it auto-fills Text), then Text LAST to override
    print("filling ArbAuft...", end=" ", flush=True)
    arbauft_ok = await find_and_fill_by_label(frame, page, "ArbAuft", worklog.arbauft)
    print("OK" if arbauft_ok else "FAIL", end=" | ", flush=True)
    await asyncio.sleep(0.5)  # Wait for auto-fill to complete

    print("Aktivität...", end=" ", flush=True)
    aktivitaet_ok = await find_and_fill_by_label(frame, page, "Aktivität", "TEMPO")
    print("OK" if aktivitaet_ok else "FAIL", end=" | ", flush=True)

    # Text to override the auto-filled value from ArbAuft
    print("Text...", end=" ", flush=True)
    text_ok = await find_and_fill_by_label(frame, page, "Text", text)
    print("OK" if text_ok else "FAIL", end=" | ", flush=True)

    # Ticketno LAST - so it doesn't get overwritten
    print("Ticketno...", end=" ", flush=True)
    ticketno_ok = await find_and_fill_by_label(frame, page, "Ticketno", worklog.issue_key)
    print("OK" if ticketno_ok else "FAIL")

    if not (arbauft_ok and text_ok):
        print(f"FAILED (ArbAuft={arbauft_ok}, Text={text_ok})")
        # Click Cancel/Abbrechen to close dialog
        await find_and_click_button(frame, "Abbrechen")
        return False

    # Fill Zeitdetails
    zeit_ok = await fill_hours_by_date(frame, page, worklog.hours, worklog.date)
    if not zeit_ok:
        print(f"    [!] Zeit konnte nicht eingetragen werden - Eintrag wird abgebrochen")
        await find_and_click_button(frame, "Abbrechen")
        return False

    # Click OK to close dialog
    if not await find_and_click_button(frame, "OK"):
        print("FAILED (OK)")
        return False

    await asyncio.sleep(0.5)
    print("OK")
    return True


# ============================================================================
# Main Sync Logic
# ============================================================================


async def sync(week: str, cutover: str | None, execute: bool):
    """Main sync function."""
    dry_run = not execute
    mode = "EXECUTE" if execute else "DRY-RUN"

    print()
    print("=" * 70)
    print(f"SYNC TEMPO -> UNIT4 | Week {week} | Mode: {mode}")
    print("=" * 70)
    print()

    # Load config and mapping
    config = load_config()
    mapping = load_mapping()
    print(f"[*] Loaded mapping with {len(mapping)} accounts")

    # Get week dates
    date_from, date_to = get_week_dates(week)
    print(f"[*] Week {week}: {date_from} to {date_to}")

    # Apply cutover if specified
    if cutover:
        date_from = cutover
        print(f"[*] Cutover: starting from {cutover}")

    # Fetch Tempo worklogs
    print()
    print(f"[1] Fetching Tempo worklogs ({date_from} to {date_to})...")
    raw_worklogs = fetch_tempo_worklogs(config, date_from, date_to)
    print(f"    Found {len(raw_worklogs)} worklogs")

    # Process worklogs
    print()
    print("[2] Processing worklogs (Jira lookup + mapping)...")
    valid_worklogs, unmapped_worklogs = process_worklogs(config, raw_worklogs, mapping)
    print(f"    Valid: {len(valid_worklogs)}, Unmapped: {len(unmapped_worklogs)}")

    # Handle unmapped worklogs interactively
    if unmapped_worklogs:
        print()
        print("[!] Found unmapped worklogs. Enter ArbAuft or SKIP:")
        for wl in unmapped_worklogs:
            arbauft = ask_for_arbauft(wl, mapping)
            if arbauft:
                wl.arbauft = arbauft
                valid_worklogs.append(wl)

    # Show summary
    print()
    print("[3] Summary of worklogs to sync:")
    total_hours = 0
    for wl in sorted(valid_worklogs, key=lambda x: (x.date, x.issue_key)):
        print(
            f"    {wl.date} | {wl.hours:5.2f}h | {wl.issue_key:<15} | {wl.arbauft} [WL:{wl.worklog_id}]"
        )
        total_hours += wl.hours
    print(f"    {'─' * 60}")
    print(f"    Total: {total_hours:.2f}h across {len(valid_worklogs)} entries")

    if not valid_worklogs:
        print()
        print("[*] No worklogs to sync. Done.")
        return

    # Connect to Unit4
    print()
    print("[4] Connecting to Unit4...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=100,
            args=["--start-maximized"],
        )

        if os.path.exists(SESSION_FILE):
            context = await browser.new_context(
                storage_state=SESSION_FILE,
                no_viewport=True,  # Use full window size
            )
        else:
            context = await browser.new_context(no_viewport=True)

        page = await context.new_page()
        frame = await login_and_navigate(page, context)

        # Set week
        await set_week(frame, page, week)

        # Extract existing entries with [WL:...] markers
        print()
        print("[5] Scanning existing entries for [WL:...] markers...")
        existing_entries = await extract_unit4_entries(frame)
        existing_wl_ids = {e.worklog_id for e in existing_entries}
        print(f"    Found {len(existing_entries)} synced entries")

        # Find which worklogs already exist in Unit4
        already_exists = [wl for wl in valid_worklogs if wl.worklog_id in existing_wl_ids]

        print()
        print("[6] Status:")
        print(f"    - Already in Unit4: {len(already_exists)} entries")
        print(f"    - New: {len(valid_worklogs) - len(already_exists)} entries")

        # For each existing entry, mark it and ask user to delete
        if already_exists and not dry_run:
            print()
            print("[6.1] Marking existing entries for deletion...")
            for wl in already_exists:
                print()
                print(f"    >>> {wl.issue_key} | {wl.hours}h | {wl.date} [WL:{wl.worklog_id}]")

                # Try to find the row and click the checkbox in first column
                try:
                    row = frame.locator(f"tr:has-text('[WL:{wl.worklog_id}]')").first
                    if await row.count() > 0:
                        # Click the actual checkbox input element
                        checkbox = row.locator("input[type='checkbox']").first
                        if await checkbox.count() > 0:
                            await checkbox.click(timeout=TIMEOUT)
                            await asyncio.sleep(0.5)
                            print("    ^^^ Row selected. Click 'Löschen' button, then ENTER...")
                        else:
                            print("    [!] Checkbox not found in row")
                    else:
                        print(f"    [!] Row not found. Please find and delete [WL:{wl.worklog_id}] manually, then ENTER...")
                except Exception as e:
                    print(f"    [!] Could not mark row: {e}")
                    print(f"    Please find and delete [WL:{wl.worklog_id}] manually, then ENTER...")

                await asyncio.get_event_loop().run_in_executor(None, input)

            # Re-scan after deletions
            print()
            print("    Re-scanning...")
            existing_entries = await extract_unit4_entries(frame)
            existing_wl_ids = {e.worklog_id for e in existing_entries}
            still_exists = [wl for wl in valid_worklogs if wl.worklog_id in existing_wl_ids]

            if still_exists:
                print(f"    [!] Warning: {len(still_exists)} entries still exist (not deleted)")
                for wl in still_exists:
                    print(f"        - {wl.issue_key} [WL:{wl.worklog_id}]")

        # All worklogs will be created (user should have deleted existing ones)
        to_create = [wl for wl in valid_worklogs if wl.worklog_id not in existing_wl_ids]
        print()
        print(f"    Entries to create: {len(to_create)}")

        if dry_run:
            print()
            if already_exists:
                print("[DRY-RUN] Would mark for deletion (user deletes manually):")
                for wl in already_exists:
                    print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date} [WL:{wl.worklog_id}]")
                print()
            print("[DRY-RUN] Would create:")
            for wl in valid_worklogs:  # Show all, assuming user deletes existing
                print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date} [WL:{wl.worklog_id}]")
            print()
            print("Run with --execute to apply changes.")
        else:
            # Create new entries
            if to_create:
                print()
                print("[7] Creating new entries...")
                errors = []
                for wl in to_create:
                    success = await add_unit4_entry(frame, page, wl, dry_run)
                    if not success:
                        errors.append(wl)

                if errors:
                    print()
                    print(f"[!] Failed to create {len(errors)} entries:")
                    for wl in errors:
                        print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date}")

            # Close any open dialog before saving
            print()
            print("[7.5] Closing dialog...")
            if await find_and_click_button(frame, "OK"):
                await asyncio.sleep(0.5)
                print("    Dialog closed")
            else:
                print("    No dialog open (or already closed)")

            # Save - try frame first, then page
            print()
            print("[8] Saving...")
            saved = await find_and_click_button(frame, "Speichern")
            if not saved:
                # Try on main page (button might be outside content frame)
                saved = await find_and_click_button(page, "Speichern")
            if not saved:
                # Try keyboard shortcut Ctrl+S
                await page.keyboard.press("Control+s")
                saved = True
            if saved:
                await asyncio.sleep(2)
                print("    Saved!")
            else:
                print("    [!] Click Speichern manually")
                await asyncio.get_event_loop().run_in_executor(None, input)

        print()
        print("[*] Press ENTER to close browser...")
        await asyncio.get_event_loop().run_in_executor(None, input)

        await browser.close()

    print()
    print("[*] Done.")


# ============================================================================
# CLI
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Sync Tempo worklogs to Unit4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Dry-run (default) - shows what would happen
    python sync_tempo_to_unit4.py 202605

    # Execute - actually creates entries
    python sync_tempo_to_unit4.py 202605 --execute

    # With cutover date (only sync from this date onwards)
    python sync_tempo_to_unit4.py 202605 --cutover 2026-01-29 --execute
        """,
    )

    parser.add_argument(
        "week", nargs="?", default=None, help="Week to sync (YYYYWW), default: current week"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually execute changes (default: dry-run)"
    )
    parser.add_argument("--cutover", help="Only sync from this date onwards (YYYY-MM-DD)")

    args = parser.parse_args()

    week = args.week or get_current_week()

    # Validate week format
    if not re.match(r"^\d{6}$", week):
        print(f"Error: Invalid week format '{week}'. Expected YYYYWW (e.g., 202605)")
        return 1

    # Validate cutover format
    if args.cutover and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.cutover):
        print(f"Error: Invalid cutover format '{args.cutover}'. Expected YYYY-MM-DD")
        return 1

    asyncio.run(sync(week, args.cutover, args.execute))
    return 0


if __name__ == "__main__":
    exit(main())
