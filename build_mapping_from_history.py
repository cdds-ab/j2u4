"""
Build ArbAuft mapping by reverse-engineering from Unit4 history.

1. Read Unit4 entries from recent weeks (Ticketno + ArbAuft)
2. For each ticket, fetch Jira issue to get Contract Position (customfield_10048)
3. Build mapping: Tempo Account -> Unit4 ArbAuft
"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Frame
import requests

UNIT4_URL = "https://ubw.unit4cloud.com/YOUR_TENANT/Default.aspx"
SESSION_FILE = "session.json"
ACCOUNT_FIELD = "customfield_10048"
OUTPUT_FILE = "account_to_arbauft_mapping.json"


def load_config() -> dict:
    with open("config.json") as f:
        return json.load(f)


def get_week_string(weeks_ago: int = 0) -> str:
    """Get week string YYYYWW for N weeks ago."""
    target = datetime.now() - timedelta(weeks=weeks_ago)
    return target.strftime("%G%V")


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
                # Get all cells in this row
                cells = await row.locator("td").all()
                if len(cells) < 10:
                    continue

                # Try to find Ticketno and ArbAuft in the cells
                row_text = await row.inner_text(timeout=500)

                # Look for ticket pattern (e.g., PROJ-123, ACME-456)
                ticket_match = re.search(r"([A-Z]{3,10}-\d+)", row_text)

                # Look for ArbAuft pattern (e.g., 1234-56789-001)
                arbauft_match = re.search(r"(\d{4}-\d{5}-\d{3})", row_text)

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


async def main():
    print("=" * 70)
    print("BUILD MAPPING FROM UNIT4 HISTORY")
    print("=" * 70)
    print()

    config = load_config()

    # Load existing mapping first
    mapping = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
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
            context = await browser.new_context(storage_state=SESSION_FILE)
        else:
            context = await browser.new_context()

        page = await context.new_page()

        # === LOGIN ===
        print("[*] Opening Unit4...")
        await page.goto(UNIT4_URL)
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

        # === SCAN SPECIFIC WEEKS ===
        # January 2026: weeks 01-05, December 2025: weeks 49-52
        weeks_to_scan = [
            "202601", "202602", "202603", "202604", "202605",
            "202549", "202550", "202551", "202552",
        ]
        print(f"\n[*] Scanning {len(weeks_to_scan)} weeks (Jan 2026 + Dec 2025)...")

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

    # Count occurrences: account_key -> {arbauft -> count}
    from collections import defaultdict
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

    # Resolve conflicts: majority wins
    print("\n[*] Resolving mappings (majority wins)...")
    for account_key, arbauft_counts in account_arbauft_counts.items():
        if account_key in mapping:
            # Already have this account - add to counts
            existing_arbauft = mapping[account_key]["unit4_arbauft"]
            arbauft_counts[existing_arbauft] += 1  # Give existing mapping a vote

        # Find the most common ArbAuft
        best_arbauft = max(arbauft_counts.keys(), key=lambda a: arbauft_counts[a])
        total_votes = sum(arbauft_counts.values())

        if len(arbauft_counts) > 1:
            # There was a conflict
            votes_str = ", ".join([f"{a}: {c}" for a, c in sorted(arbauft_counts.items())])
            print(f"    {account_key}: {votes_str} -> winner: {best_arbauft}")

        mapping[account_key] = {
            "unit4_arbauft": best_arbauft,
            "tempo_name": account_names.get(account_key, mapping.get(account_key, {}).get("tempo_name", "?")),
            "vote_count": arbauft_counts[best_arbauft],
            "total_seen": total_votes,
        }

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

    with open(OUTPUT_FILE, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    print()
    print(f"[*] Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
