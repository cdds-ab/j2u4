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

from clients import JiraClient, TempoClient, ACCOUNT_FIELD
from models import TempoWorklog
from patterns import Patterns
from unit4_browser import Unit4Browser
from utils import (
    get_current_week,
    get_week_dates,
    load_config,
    load_mapping,
    save_mapping,
)


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
    issue_cache: dict[int, dict] = {}

    jira = JiraClient(config)

    print(f"[*] Processing {len(raw_worklogs)} worklogs...")

    for wl in raw_worklogs:
        worklog_id = wl["tempoWorklogId"]
        issue_id = wl.get("issue", {}).get("id")
        date = wl["startDate"]
        hours = wl["timeSpentSeconds"] / 3600
        description = wl.get("description", "")

        # Fetch issue details if not cached
        if issue_id and issue_id not in issue_cache:
            issue_data = jira.get_issue_details(issue_id)
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
    if not Patterns.ARBAUFT.match(arbauft):
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
    unit4_url = config.get("unit4", {}).get("url")
    if not unit4_url:
        print("[!] Error: unit4.url not configured in config.json")
        return
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

    jira = JiraClient(config)
    tempo = TempoClient(config)

    account_id = jira.get_my_account_id()
    raw_worklogs = tempo.fetch_worklogs(account_id, date_from, date_to)
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

    async with Unit4Browser(config) as unit4:
        frame = await unit4.navigate_to_zeiterfassung()

        # Set week
        if not await unit4.set_week(week):
            print("[!] Failed to set week - page may not have loaded correctly")
            print("    Waiting for page to stabilize...")
            await asyncio.sleep(5)
            await unit4.set_week(week)

        # Wait for page to be ready
        await unit4.wait_for_ready()

        # Wait for table to load
        print()
        print("[5] Scanning existing entries for [WL:...] markers...")
        print("    Waiting for table to load...", end=" ", flush=True)
        await asyncio.sleep(3)

        # Deselect any selected row
        await unit4.page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        print("OK")

        existing_entries = await unit4.extract_entries(debug=True)
        print(f"    Found {len(existing_entries)} synced entries")

        print()
        print("[6] Status:")
        print(f"    - Existing [WL:] entries to delete: {len(existing_entries)}")
        print(f"    - Tempo worklogs to create: {len(valid_worklogs)}")

        if dry_run:
            print()
            if existing_entries:
                print(f"[DRY-RUN] Would DELETE {len(existing_entries)} existing [WL:] entries:")
                for entry in existing_entries:
                    print(f"    - {entry.ticketno} [WL:{entry.worklog_id}]")
                print()
            print(f"[DRY-RUN] Would CREATE {len(valid_worklogs)} entries:")
            for wl in valid_worklogs:
                print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date} [WL:{wl.worklog_id}]")
            print()
            print("Run with --execute to apply changes.")
        else:
            # Delete existing entries
            if existing_entries:
                print()
                print("[6.1] Deleting ALL existing [WL:] entries...")
                await unit4.delete_entries(existing_entries)

                # Re-scan and repeat if needed
                for delete_pass in range(3):
                    print()
                    print(f"    Re-scanning (pass {delete_pass + 1})...")
                    await asyncio.sleep(2)
                    remaining = await unit4.extract_entries()
                    if not remaining:
                        print("    All [WL:] entries deleted successfully")
                        break
                    print(f"    {len(remaining)} [WL:] entries still exist, deleting again...")
                    await unit4.delete_entries(remaining)

            # Create new entries
            print()
            print("[7] Creating new entries...")
            errors = []
            for wl in valid_worklogs:
                success = await unit4.create_entry(wl)
                if not success:
                    errors.append(wl)

            if errors:
                print()
                print(f"[!] Failed to create {len(errors)} entries:")
                for wl in errors:
                    print(f"    - {wl.issue_key} | {wl.hours}h | {wl.date}")

            # Close any open dialog
            print()
            print("[7.5] Closing dialog...")
            frame = await unit4.frame_manager.get_content_frame()
            if await unit4._click_button(frame, "OK"):
                await asyncio.sleep(0.5)
                print("    Dialog closed")
            else:
                print("    No dialog open (or already closed)")

            # Save
            print()
            print("[8] Saving...")
            if await unit4.save():
                print("    Saved!")
            else:
                print("    [!] Click Speichern manually")
                await asyncio.get_event_loop().run_in_executor(None, input)

        print()
        print("[*] Press ENTER to close browser...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except EOFError:
            await asyncio.sleep(3)

    print()
    print("[*] Done.")


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
    if not Patterns.WEEK_FORMAT.match(week):
        print(f"Error: Invalid week format '{week}'. Expected YYYYWW (e.g., 202605)")
        return 1

    # Validate cutover format
    if args.cutover and not Patterns.DATE_FORMAT.match(args.cutover):
        print(f"Error: Invalid cutover format '{args.cutover}'. Expected YYYY-MM-DD")
        return 1

    asyncio.run(sync(week, args.cutover, args.execute))
    return 0


if __name__ == "__main__":
    exit(main())
