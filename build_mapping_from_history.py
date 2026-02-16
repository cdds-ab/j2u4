"""
Build ArbAuft mapping by reverse-engineering from Unit4 history.

1. Read Unit4 entries from recent weeks (Ticketno + ArbAuft)
2. For each ticket, fetch Jira issue to get Contract Position (customfield_10048)
3. Build mapping: Tempo Account -> Unit4 ArbAuft

Usage:
    # Scan last 8 weeks (default)
    python build_mapping_from_history.py

    # Scan last N weeks
    python build_mapping_from_history.py --weeks 12

    # Scan specific range
    python build_mapping_from_history.py --from 202601 --to 202610
"""

import argparse
import asyncio
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Frame
import requests

from utils import load_config_safe, SESSION_FILE, MAPPING_FILE

ACCOUNT_FIELD = "customfield_10048"


def get_week_string(weeks_ago: int = 0) -> str:
    """Get week string YYYYWW for N weeks ago."""
    target = datetime.now() - timedelta(weeks=weeks_ago)
    return target.strftime("%G%V")


def get_weeks_range(weeks_back: int = 8, week_from: str = None, week_to: str = None) -> list[str]:
    """Generate list of weeks to scan.

    Args:
        weeks_back: Number of weeks back from current week (default: 8)
        week_from: Start week YYYYWW (optional, overrides weeks_back)
        week_to: End week YYYYWW (optional, defaults to current week)

    Returns:
        List of week strings in YYYYWW format
    """
    if week_from and week_to:
        # Explicit range specified
        weeks = []
        current = week_from
        while current <= week_to:
            weeks.append(current)
            # Calculate next week
            year = int(current[:4])
            week = int(current[4:])
            week += 1
            if week > 52:
                # Simple handling - ISO weeks can be 52 or 53
                # Check if week 53 exists for this year
                dec_31 = datetime(year, 12, 31)
                max_week = int(dec_31.strftime("%V"))
                if week > max_week:
                    week = 1
                    year += 1
            current = f"{year}{week:02d}"
        return weeks
    else:
        # Generate last N weeks
        return [get_week_string(i) for i in range(weeks_back - 1, -1, -1)]


async def get_content_frame(page: Page) -> Frame:
    """Get the iframe with actual content."""
    for frame in page.frames:
        if "ContentContainer" in frame.url:
            return frame
    return page.main_frame


async def set_week(frame: Frame, page: Page, week: str) -> bool:
    """Set the week in Unit4."""
    print(f"    Setting week {week}...", end=" ", flush=True)
    try:
        week_input = frame.get_by_label("Woche", exact=False).first
        if await week_input.count() > 0:
            await week_input.click(timeout=3000, force=True)
            await week_input.press("Control+a")
            await week_input.type(week, delay=30)
            await page.keyboard.press("Tab")
            await asyncio.sleep(3)  # Wait for data to load
            print("OK")
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False


async def extract_entries_from_week(frame: Frame, page: Page, known_arbaufts: set) -> list[dict]:
    """Extract entries (Ticketno + ArbAuft) from current week view.

    Skips entries where ArbAuft is already in known_arbaufts.
    """
    entries = []
    skipped = 0

    try:
        # Find all rows in the time entry grid
        rows = await frame.locator("tr").all()
        print(f"    Found {len(rows)} rows, scanning...")

        for row in rows:
            try:
                # Get only direct child cells (avoid nested table tds)
                cells = await row.locator(":scope > td").all()
                if len(cells) < 10:
                    continue

                # Extract ticket and ArbAuft from individual cells
                ticket_match = None
                arbauft_match = None
                for cell in cells:
                    cell_text = await cell.inner_text(timeout=500)
                    if not ticket_match:
                        m = re.search(r"([A-Z]{3,10}-\d+)", cell_text)
                        if m:
                            ticket_match = m
                    if not arbauft_match:
                        m = re.search(r"(\d{4}-\d{5}-\d{3})", cell_text)
                        if m:
                            arbauft_match = m

                if ticket_match and arbauft_match:
                    arbauft = arbauft_match.group(1)

                    # Skip if already known
                    if arbauft in known_arbaufts:
                        skipped += 1
                        continue

                    entry = {
                        "ticketno": ticket_match.group(1),
                        "arbauft": arbauft,
                    }
                    if entry not in entries:
                        entries.append(entry)
                        print(f"      NEW: {entry['ticketno']} -> {entry['arbauft']}")

            except Exception:
                continue

    except Exception as e:
        print(f"    Error extracting: {e}")

    if skipped:
        print(f"    (skipped {skipped} already known)")

    return entries


def fetch_jira_account(config: dict, ticket: str) -> dict | None:
    """Fetch the Account field (customfield_10048) from Jira for a ticket."""
    base_url = config["jira"]["base_url"]
    email = config["jira"]["user_email"]
    token = config["jira"]["api_token"]

    try:
        r = requests.get(
            f"{base_url}/rest/api/3/issue/{ticket}",
            auth=(email, token),
            headers={"Accept": "application/json"},
            params={"fields": f"key,summary,{ACCOUNT_FIELD}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            account_field = data.get("fields", {}).get(ACCOUNT_FIELD)
            if account_field and isinstance(account_field, dict):
                return {
                    "key": account_field.get("key") or account_field.get("id"),
                    "name": account_field.get("name") or account_field.get("value"),
                }
    except Exception:
        pass
    return None


async def main(weeks_to_scan: list[str]):
    print("=" * 70)
    print("BUILD MAPPING FROM UNIT4 HISTORY")
    print("=" * 70)
    print()

    config = load_config_safe()
    if config is None:
        return

    unit4_url = config.get("unit4", {}).get("url")
    if not unit4_url:
        print("[!] Error: unit4.url not configured in config.json")
        return

    # Load existing mapping first
    mapping = {}
    if os.path.exists(MAPPING_FILE):
        with open(MAPPING_FILE) as f:
            mapping = json.load(f)
        print(f"[*] Loaded existing mapping with {len(mapping)} entries")

    # Build set of known ArbAufts for fast lookup
    known_arbaufts = {info["unit4_arbauft"] for info in mapping.values()}
    print(f"[*] Known ArbAufts: {len(known_arbaufts)}")

    # Collect entries from Unit4
    all_entries = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)

        if os.path.exists(SESSION_FILE):
            print("[*] Loading session...")
            context = await browser.new_context(storage_state=SESSION_FILE, locale='de')
        else:
            context = await browser.new_context(locale='de')

        page = await context.new_page()

        # === LOGIN ===
        print("[*] Opening Unit4...")
        await page.goto(unit4_url)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        if "Login" in await page.title():
            print("[!] Please log in (2FA), then ENTER...")
            await asyncio.get_event_loop().run_in_executor(None, input)
            await context.storage_state(path=SESSION_FILE)
            await asyncio.sleep(2)

        # === NAVIGATION ===
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

        frame = await get_content_frame(page)

        # === SCAN WEEKS ===
        print(f"\n[*] Scanning {len(weeks_to_scan)} weeks: {weeks_to_scan[0]} to {weeks_to_scan[-1]}...")

        # Track ArbAufts found in this session (to avoid duplicates in output)
        session_arbaufts = set()

        for week in weeks_to_scan:
            print(f"\n  Week {week}:")

            if await set_week(frame, page, week):
                entries = await extract_entries_from_week(frame, page, known_arbaufts | session_arbaufts)
                for entry in entries:
                    session_arbaufts.add(entry["arbauft"])
                all_entries.extend(entries)

            await asyncio.sleep(1)

        print(f"\n[*] Found {len(all_entries)} entries total")

        await browser.close()

    # === DEDUPLICATE ===
    unique_entries = []
    seen = set()
    for entry in all_entries:
        key = (entry["ticketno"], entry["arbauft"])
        if key not in seen:
            seen.add(key)
            unique_entries.append(entry)

    print(f"[*] Unique ticket-arbauft pairs: {len(unique_entries)}")

    # === FETCH JIRA ACCOUNTS ===
    print(f"\n[*] Fetching Jira Account field for {len(unique_entries)} new entries...")

    # Collect: account_key -> {arbauft -> count}
    account_arbauft_counts = defaultdict(lambda: defaultdict(int))
    account_names = {}

    for entry in unique_entries:
        ticket = entry["ticketno"]
        arbauft = entry["arbauft"]

        print(f"    {ticket} ({arbauft})...", end=" ", flush=True)

        account = fetch_jira_account(config, ticket)
        if account:
            account_key = str(account["key"])  # Ensure string for consistency
            account_name = account["name"]
            print(f"Account: {account_key} ({account_name})")

            account_arbauft_counts[account_key][arbauft] += 1
            account_names[account_key] = account_name
            known_arbaufts.add(arbauft)
        else:
            print("(no account found)")

    # Resolve mappings (flag conflicts for user)
    conflicts = []
    print("\n[*] Resolving mappings...")
    for account_key, arbauft_counts in account_arbauft_counts.items():
        account_name = account_names.get(account_key, "?")

        if account_key in mapping:
            existing_arbauft = mapping[account_key]["unit4_arbauft"]
            # Check if new scan found a different ArbAuft
            new_arbaufts = set(arbauft_counts.keys())
            if new_arbaufts == {existing_arbauft}:
                # Consistent with existing mapping, just update metadata
                mapping[account_key]["tempo_name"] = account_name
                continue
            # Conflict with existing mapping
            new_arbaufts.add(existing_arbauft)
            if len(new_arbaufts) > 1:
                conflicts.append((account_key, account_name, sorted(new_arbaufts)))
                continue

        new_arbaufts = list(arbauft_counts.keys())
        if len(new_arbaufts) > 1:
            # Multiple different ArbAufts found in scan
            conflicts.append((account_key, account_name, sorted(new_arbaufts)))
            continue

        # Unambiguous: exactly one ArbAuft
        mapping[account_key] = {
            "unit4_arbauft": new_arbaufts[0],
            "tempo_name": account_name,
        }

    # === HANDLE CONFLICTS ===
    if conflicts:
        print()
        print("=" * 70)
        print(f"CONFLICTS ({len(conflicts)} accounts have multiple ArbAufts)")
        print("=" * 70)
        print()
        print("These accounts were found with different ArbAuft codes.")
        print("Please choose the correct one for each:\n")

        for account_key, account_name, arbaufts in conflicts:
            print(f"  Account {account_key} ({account_name}):")
            for i, a in enumerate(arbaufts, 1):
                existing = " (current)" if account_key in mapping and mapping[account_key]["unit4_arbauft"] == a else ""
                print(f"    [{i}] {a}{existing}")

            while True:
                choice = input(f"  Choose [1-{len(arbaufts)}] or SKIP: ").strip()
                if choice.upper() == "SKIP":
                    print(f"  -> Skipped\n")
                    break
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(arbaufts):
                        mapping[account_key] = {
                            "unit4_arbauft": arbaufts[idx],
                            "tempo_name": account_name,
                        }
                        print(f"  -> {arbaufts[idx]}\n")
                        break
                except ValueError:
                    pass
                print(f"  Invalid input. Enter 1-{len(arbaufts)} or SKIP.")

    # === SAVE RESULTS ===
    print()
    print("=" * 70)
    print(f"MAPPING RESULTS ({len(mapping)} accounts)")
    print("=" * 70)
    print()
    print(f"{'Account Key':<15} {'Unit4 ArbAuft':<20} {'Name'}")
    print("-" * 70)
    for key, info in sorted(mapping.items(), key=lambda x: str(x[0])):
        arbauft = info["unit4_arbauft"]
        name = info.get("tempo_name", "?")[:40]
        print(f"{key:<15} {arbauft:<20} {name}")

    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    print()
    print(f"[*] Saved to {MAPPING_FILE}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build account->ArbAuft mapping from Unit4 history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Scan last 8 weeks (default)
    python build_mapping_from_history.py

    # Scan last 12 weeks
    python build_mapping_from_history.py --weeks 12

    # Scan specific range
    python build_mapping_from_history.py --from 202601 --to 202610

    # Scan a single week
    python build_mapping_from_history.py --from 202605 --to 202605
        """,
    )
    parser.add_argument(
        "--weeks", type=int, default=8,
        help="Number of weeks back from current week to scan (default: 8)"
    )
    parser.add_argument(
        "--from", dest="week_from", metavar="YYYYWW",
        help="Start week (e.g., 202601). Overrides --weeks."
    )
    parser.add_argument(
        "--to", dest="week_to", metavar="YYYYWW",
        help="End week (e.g., 202610). Defaults to current week if --from is set."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Determine weeks to scan
    if args.week_from:
        week_to = args.week_to or get_week_string(0)
        weeks = get_weeks_range(week_from=args.week_from, week_to=week_to)
    else:
        weeks = get_weeks_range(weeks_back=args.weeks)

    print(f"Will scan {len(weeks)} weeks: {weeks[0]} to {weeks[-1]}")

    asyncio.run(main(weeks))
