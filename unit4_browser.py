"""Unit4 browser automation for time entry management."""

import asyncio
import os
import re
from datetime import datetime

from playwright.async_api import Frame, Page, async_playwright, BrowserContext

from models import TempoWorklog, Unit4Entry
from patterns import Patterns
from utils import SESSION_FILE

TIMEOUT = 10000  # 10 seconds
DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


class FrameManager:
    """Manages frame navigation within Unit4."""

    def __init__(self, page: Page):
        self.page = page
        self._content_frame: Frame | None = None

    async def get_content_frame(self, refresh: bool = False) -> Frame:
        """Get the iframe with actual content."""
        if self._content_frame and not refresh:
            return self._content_frame

        # Try to find the content frame by URL pattern
        for frame in self.page.frames:
            if "ContentContainer" in frame.url:
                self._content_frame = frame
                return frame

        # Fallback: find frame that contains the "Woche" field
        for frame in self.page.frames:
            try:
                week_field = frame.get_by_label("Period*", exact=True)
                if await week_field.count() > 0:
                    self._content_frame = frame
                    return frame
            except Exception:
                continue

        return self.page.main_frame

    async def wait_for_element(
        self, selector: str, timeout: int = TIMEOUT, frame: Frame | None = None
    ) -> bool:
        """Wait for an element to be visible."""
        target = frame or await self.get_content_frame()
        try:
            elem = target.locator(selector).first
            if await elem.count() > 0:
                await elem.wait_for(state="visible", timeout=timeout)
                return True
        except Exception:
            pass
        return False


class Unit4Browser:
    """Context manager for Unit4 browser session."""

    def __init__(self, config: dict, headless: bool = False, slow_mo: int = 100):
        self.config = config
        self.headless = headless
        self.slow_mo = slow_mo
        self.unit4_url = config.get("unit4", {}).get("url")
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._frame_manager: FrameManager | None = None

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not initialized. Use 'async with' context.")
        return self._page

    @property
    def frame_manager(self) -> FrameManager:
        if not self._frame_manager:
            raise RuntimeError("Browser not initialized. Use 'async with' context.")
        return self._frame_manager

    async def __aenter__(self) -> "Unit4Browser":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=["--start-maximized"],
        )

        if os.path.exists(SESSION_FILE):
            self._context = await self._browser.new_context(
                storage_state=SESSION_FILE,
                no_viewport=True,
            )
        else:
            self._context = await self._browser.new_context(no_viewport=True)

        self._page = await self._context.new_page()
        self._frame_manager = FrameManager(self._page)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def check_session_valid(self) -> bool:
        """Quick check if session is still valid.

        Returns:
            True if session appears valid, False if login is required.
        """
        try:
            title = await self.page.title()
            if "Login" in title or "Anmelden" in title:
                return False
            return True
        except Exception:
            return False

    async def navigate_to_zeiterfassung(self) -> Frame:
        """Login to Unit4 and navigate to Zeiterfassung."""
        print("[*] Opening Unit4...")
        await self.page.goto(self.unit4_url)
        await self.page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        if not await self.check_session_valid():
            print("[!] Session expired or not logged in.")
            print("    Please log in (2FA may be required), then press ENTER...")
            await asyncio.get_event_loop().run_in_executor(None, input)
            await self._context.storage_state(path=SESSION_FILE)
            print("    Session saved for future use.")
            await asyncio.sleep(2)

        # Navigate to Zeiterfassung
        print("[*] Opening Zeiterfassung...", end=" ", flush=True)
        try:
            menu = self.page.get_by_text("Timesheets - standard", exact=True).first
            if await menu.count() > 0:
                await menu.click(timeout=5000)
                print("clicked...", end=" ", flush=True)
        except Exception:
            print()
            print("[!] Navigate to Zeiterfassung manually, then ENTER...")
            try:
                await asyncio.get_event_loop().run_in_executor(None, input)
            except EOFError:
                pass

        # Wait for page to fully load
        print("waiting...", end=" ", flush=True)
        await self.page.wait_for_load_state("networkidle")

        # Wait until "Woche" field is visible
        frame = None
        for i in range(15):
            frame = await self.frame_manager.get_content_frame(refresh=True)
            try:
                week_field = frame.get_by_label("Period*", exact=True)
                if await week_field.count() > 0 and await week_field.is_visible(timeout=500):
                    print("OK")
                    return frame
            except Exception:
                pass
            await asyncio.sleep(1)
            if i % 5 == 4:
                print(f"{i+1}s...", end=" ", flush=True)

        print("timeout, continuing anyway")
        return frame

    async def set_week(self, week: str) -> bool:
        """Set the week in Unit4."""
        print(f"[*] Setting week {week}...", end=" ", flush=True)

        for attempt in range(3):
            try:
                frame = await self.frame_manager.get_content_frame(refresh=attempt > 0)

                # Try multiple ways to find the week input
                week_input = None
                strategies = [
                    lambda: frame.get_by_label("Period*", exact=True),
                    lambda: frame.locator("input[id*='week']").first,
                    lambda: frame.locator("input[name*='week']").first,
                ]

                for strategy in strategies:
                    try:
                        candidate = strategy()
                        if await candidate.count() > 0 and await candidate.is_visible(timeout=500):
                            week_input = candidate
                            break
                    except Exception:
                        continue

                if week_input:
                    await week_input.click(timeout=TIMEOUT, force=True)
                    await asyncio.sleep(0.3)
                    await week_input.press("Control+a")
                    await week_input.type(week, delay=30)
                    await self.page.keyboard.press("Tab")
                    await asyncio.sleep(3)
                    print("OK")
                    return True
                else:
                    if attempt < 2:
                        print(f"retry {attempt + 1}...", end=" ", flush=True)
                        await asyncio.sleep(2)
                    else:
                        print("FAILED (field not found)")
            except Exception as e:
                if attempt < 2:
                    print(f"error, retry {attempt + 1}...", end=" ", flush=True)
                    await asyncio.sleep(2)
                else:
                    print(f"FAILED: {e}")

        return False

    async def extract_entries(self, debug: bool = False) -> list[Unit4Entry]:
        """Extract current entries from Unit4 (looking for [WL:xxx] markers)."""
        entries = []
        seen_wl_ids: set[int] = set()

        frames_to_search = self.page.frames
        if debug:
            print(f"    [DEBUG] Searching {len(frames_to_search)} frames")

        for search_frame in frames_to_search:
            entries.extend(
                await self._extract_entries_from_frame(search_frame, seen_wl_ids, debug)
            )

        return entries

    async def _extract_entries_from_frame(
        self, frame: Frame, seen_wl_ids: set[int], debug: bool
    ) -> list[Unit4Entry]:
        """Extract entries from a single frame."""
        entries = []

        try:
            # Strategy 1: Find elements with [WL: in title attribute
            entries.extend(
                await self._extract_from_title_attribute(frame, seen_wl_ids, debug)
            )

            # Strategy 2: Search input/textarea values
            entries.extend(
                await self._extract_from_inputs(frame, seen_wl_ids, debug)
            )

            # Strategy 3: Search visible text
            entries.extend(
                await self._extract_from_visible_text(frame, seen_wl_ids, debug)
            )

        except Exception as e:
            if debug:
                print(f"    [DEBUG] Error in frame: {e}")

        return entries

    async def _extract_from_title_attribute(
        self, frame: Frame, seen_wl_ids: set[int], debug: bool
    ) -> list[Unit4Entry]:
        """Extract entries from title attributes."""
        entries = []
        elements = await frame.locator("[title*='[WL:']").all()

        if debug and elements:
            print(f"    [DEBUG] Found {len(elements)} elements with [WL:] in title")

        for elem in elements:
            entry = await self._parse_element_to_entry(elem, seen_wl_ids, debug, "title")
            if entry:
                entries.append(entry)

        return entries

    async def _extract_from_inputs(
        self, frame: Frame, seen_wl_ids: set[int], debug: bool
    ) -> list[Unit4Entry]:
        """Extract entries from input/textarea values."""
        entries = []
        inputs = await frame.locator("input, textarea").all()

        for inp in inputs:
            try:
                val = await inp.input_value(timeout=100)
                if val and "[WL:" in val:
                    # Also read the whole row to find ticket key
                    row_text = val
                    try:
                        row = inp.locator("xpath=ancestor::tr[1]")
                        if await row.count() > 0:
                            row_inner = await row.inner_text(timeout=500)
                            row_text = val + " " + row_inner
                            if debug:
                                print(f"    [DEBUG] Row text for input: {row_inner[:80]}...")
                    except Exception as e:
                        if debug:
                            print(f"    [DEBUG] Could not read row: {e}")

                    entry = await self._parse_text_to_entry(row_text, seen_wl_ids, debug, "input")
                    if entry:
                        entries.append(entry)
            except Exception:
                continue

        return entries

    async def _extract_from_visible_text(
        self, frame: Frame, seen_wl_ids: set[int], debug: bool
    ) -> list[Unit4Entry]:
        """Extract entries from visible text."""
        entries = []
        wl_texts = await frame.locator("text=/\\[WL:\\d+\\]/").all()

        if debug and wl_texts:
            print(f"    [DEBUG] Found {len(wl_texts)} elements with [WL:] in visible text")

        for elem in wl_texts:
            try:
                text = await elem.inner_text(timeout=500)
                row = elem.locator("xpath=ancestor::tr[1]")
                row_text = text
                try:
                    if await row.count() > 0:
                        row_text = text + " " + await row.inner_text(timeout=500)
                except Exception:
                    pass

                entry = await self._parse_text_to_entry(row_text, seen_wl_ids, debug, "text")
                if entry:
                    entries.append(entry)
            except Exception:
                continue

        return entries

    async def _parse_element_to_entry(
        self, elem, seen_wl_ids: set[int], debug: bool, source: str
    ) -> Unit4Entry | None:
        """Parse an element with title attribute to Unit4Entry."""
        try:
            title = await elem.get_attribute("title", timeout=500)
            if not title:
                return None

            wl_match = Patterns.WORKLOG_MARKER.search(title)
            if not wl_match:
                return None

            worklog_id = int(wl_match.group(1))
            if worklog_id in seen_wl_ids:
                return None

            seen_wl_ids.add(worklog_id)
            if debug:
                print(f"    [DEBUG] Found [WL:{worklog_id}] in {source}: {title[:60]}...")

            # Try to get more context from the row
            row = elem.locator("xpath=ancestor::tr[1]")
            row_text = title
            try:
                if await row.count() > 0:
                    row_text = title + " " + await row.inner_text(timeout=500)
            except Exception:
                pass

            return self._create_entry_from_text(row_text, worklog_id)
        except Exception:
            return None

    async def _parse_text_to_entry(
        self, text: str, seen_wl_ids: set[int], debug: bool, source: str
    ) -> Unit4Entry | None:
        """Parse text content to Unit4Entry."""
        wl_match = Patterns.WORKLOG_MARKER.search(text)
        if not wl_match:
            return None

        worklog_id = int(wl_match.group(1))
        if worklog_id in seen_wl_ids:
            return None

        seen_wl_ids.add(worklog_id)
        if debug:
            print(f"    [DEBUG] Found [WL:{worklog_id}] in {source}: {text[:60]}...")

        return self._create_entry_from_text(text, worklog_id)

    def _create_entry_from_text(self, text: str, worklog_id: int) -> Unit4Entry:
        """Create a Unit4Entry from text content."""
        ticket_match = Patterns.TICKET_KEY.search(text)
        arbauft_match = Patterns.ARBAUFT.search(text)

        return Unit4Entry(
            ticketno=ticket_match.group(1) if ticket_match else "UNKNOWN",
            arbauft=arbauft_match.group(1) if arbauft_match else "0000-00000-000",
            text=text[:100],
            worklog_id=worklog_id,
        )

    async def delete_entries(self, entries: list[Unit4Entry], dry_run: bool = False) -> int:
        """Delete entries by marking checkboxes and clicking delete button."""
        if dry_run:
            print(f"    [DRY-RUN] Would delete {len(entries)} entries")
            return len(entries)

        frame = await self.frame_manager.get_content_frame()

        # Close any open detail view
        await self._close_detail_view()

        marked_count = 0
        for entry in entries:
            if await self._mark_entry_for_deletion(frame, entry):
                marked_count += 1

        if marked_count > 0:
            print()
            print(f"    Marked {marked_count} entries.")
            print("    >>> Check the browser - are all entries marked correctly?")
            print("    >>> Press ENTER to click 'Delete', or Ctrl+C to abort...")
            try:
                await asyncio.get_event_loop().run_in_executor(None, input)
            except EOFError:
                pass

            print("    Clicking 'Delete'...", end=" ", flush=True)
            if await self._click_button(frame, "Delete"):
                await asyncio.sleep(3)
                await self._click_button(frame, "Yes")
                await self._click_button(frame, "OK")
                await asyncio.sleep(2)
                print("deleted...", end=" ", flush=True)

                # Save after deletion
                print("saving...", end=" ", flush=True)
                await self.save()
                print("OK")
            else:
                print("button not found")

        return marked_count

    async def _close_detail_view(self):
        """Close any open detail view."""
        print("    Closing detail views...", end=" ", flush=True)
        try:
            clicked = False
            for search_frame in self.page.frames:
                if clicked:
                    break
                try:
                    ok_btn = search_frame.locator("button:has-text('OK'), input[value='OK']").first
                    if await ok_btn.count() > 0 and await ok_btn.is_visible(timeout=500):
                        await ok_btn.click(timeout=2000)
                        clicked = True
                        print("clicked OK...", end=" ", flush=True)
                        await asyncio.sleep(0.5)
                except Exception:
                    pass

            if not clicked:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
                print("pressed Escape...", end=" ", flush=True)
            print("done")
        except Exception as e:
            print(f"error: {e}")
        await asyncio.sleep(0.5)

    async def _mark_entry_for_deletion(self, frame: Frame, entry: Unit4Entry) -> bool:
        """Mark a single entry for deletion by clicking its checkbox."""
        print(f"    [WL:{entry.worklog_id}] {entry.ticketno}...", end=" ", flush=True)

        try:
            # Try multiple strategies to find the row
            row = None

            # Strategy 1: cell with title containing the WL marker
            cell = frame.locator(f"td[title*='[WL:{entry.worklog_id}]']").first
            if await cell.count() > 0:
                row = cell.locator("xpath=ancestor::tr[1]")

            # Strategy 2: any element with title
            if not row or await row.count() == 0:
                elem = frame.locator(f"[title*='[WL:{entry.worklog_id}]']").first
                if await elem.count() > 0:
                    row = elem.locator("xpath=ancestor::tr[1]")

            # Strategy 3: visible text
            if not row or await row.count() == 0:
                row = frame.locator(f"tr:has-text('[WL:{entry.worklog_id}]')").first

            if row and await row.count() > 0:
                checkbox = row.locator("input[type='checkbox']").first
                if await checkbox.count() > 0:
                    await checkbox.evaluate("""el => {
                        el.scrollIntoView({block: 'center', behavior: 'instant'});
                        el.click();
                    }""")
                    await asyncio.sleep(0.3)
                    print("marked")
                    return True
                else:
                    print("no checkbox")
            else:
                print("row not found")
        except Exception as e:
            print(f"error: {e}")

        return False

    async def create_entry(self, worklog: TempoWorklog, dry_run: bool = False) -> bool:
        """Add a single entry to Unit4."""
        description = worklog.description.strip() if worklog.description else worklog.issue_summary
        text = f"[WL:{worklog.worklog_id}] {description[:60]}"

        print(f"    {worklog.issue_key} | {worklog.hours}h | {worklog.date}...", end=" ", flush=True)

        if dry_run:
            print("SKIPPED (dry-run)")
            return True

        frame = await self.frame_manager.get_content_frame()

        # Click "Add" to create new row
        if not await self._click_button(frame, "Add"):
            print("FAILED (no Add)")
            return False
        await asyncio.sleep(1)

        # Click first zoom icon (new row)
        try:
            zoom_icons = await frame.locator("[title='Click to see more details']").all()
            if zoom_icons:
                await zoom_icons[0].click(timeout=TIMEOUT)
            else:
                print("FAILED (no zoom)")
                return False
        except Exception as e:
            print(f"FAILED (zoom: {e})")
            return False
        await asyncio.sleep(3)

        # Fill form fields
        print("filling Work order...", end=" ", flush=True)
        arbauft_ok = await self._fill_field(frame, "Work order", worklog.arbauft)
        print("OK" if arbauft_ok else "FAIL", end=" | ", flush=True)
        await asyncio.sleep(1)

        print("Activity...", end=" ", flush=True)
        aktivitaet_ok = await self._fill_field(frame, "Activity", "TEMPO")
        print("OK" if aktivitaet_ok else "FAIL", end=" | ", flush=True)

        print("Description...", end=" ", flush=True)
        text_ok = await self._fill_field(frame, "Description", text)
        print("OK" if text_ok else "FAIL", end=" | ", flush=True)

        print("Ticketno...", end=" ", flush=True)
        ticketno_ok = await self._fill_field(frame, "Ticketno", worklog.issue_key)
        print("OK" if ticketno_ok else "FAIL")

        if not (arbauft_ok and text_ok):
            print(f"FAILED (Work order={arbauft_ok}, Description={text_ok})")
            await self._click_button(frame, "Abbrechen") //TODO: unknown button
            return False

        # Fill hours in Zeitdetails
        zeit_ok = await self._fill_hours_by_date(frame, worklog.hours, worklog.date)
        if not zeit_ok:
            print("    [!] Zeit konnte nicht eingetragen werden - Eintrag wird abgebrochen")
            await self._click_button(frame, "Abbrechen") //TODO: unknown button
            return False

        # Click OK to close dialog
        ok_clicked = await self._click_button(frame, "OK")
        if not ok_clicked:
            await self.page.keyboard.press("Enter")
            await asyncio.sleep(1)
            await self._click_button(frame, "Abbrechen") //TODO: unknown button
            await self._click_button(frame, "OK")
            print("FAILED (OK) - dialog closed")
            return False

        await asyncio.sleep(3)

        # Verify dialog is closed
        ergaenzen = frame.get_by_text("Add", exact=True).first
        for _ in range(10):
            if await ergaenzen.count() > 0 and await ergaenzen.is_visible(timeout=500):
                break
            await asyncio.sleep(0.5)
            await self._click_button(frame, "OK")
            await self._click_button(frame, "Abbrechen") //TODO: unknown button

        await asyncio.sleep(1)
        print("OK")
        return True

    async def _fill_field(self, frame: Frame, label: str, value: str) -> bool:
        """Fill a form field by its label."""
        label_variants = [label, f"{label}*", f"{label} *"]

        for lbl in label_variants:
            strategies = [
                lambda l=lbl: frame.get_by_label(l, exact=False),
                lambda l=lbl: frame.locator(f"text='{l}'").locator("xpath=following::input[1]"),
                lambda l=lbl: frame.locator(f"text='{l}'").locator("xpath=ancestor::*[.//input][1]//input").first,
                lambda l=lbl: frame.locator(f"text='{l}'").locator("xpath=following::textarea[1]"),
            ]

            for strategy in strategies:
                try:
                    elem = strategy()
                    if await elem.count() > 0 and await elem.first.is_visible(timeout=1000):
                        await elem.first.click(timeout=TIMEOUT)
                        await asyncio.sleep(0.2)
                        await elem.first.press("Control+a")
                        await elem.first.fill(value, timeout=TIMEOUT)
                        await self.page.keyboard.press("Tab")
                        await asyncio.sleep(0.3)
                        return True
                except Exception:
                    continue
        return False

    async def _fill_hours_by_date(self, frame: Frame, hours: float, date_str: str) -> bool:
        """Fill hours for a specific date in Time details."""
        hours_str = str(hours)
        day_name = date_str

        for attempt in range(5):
            if attempt > 0:
                print(f"    Retry {attempt}...", flush=True)
                await asyncio.sleep(1)

            await self._expand_zeitdetails(frame)
            await asyncio.sleep(1.0)

            date_to_label = await self._read_zeitdetails_structure(frame)

            if date_str not in date_to_label:
                if attempt < 4:
                    print(f"    Date {date_str} not in structure, retrying...", flush=True)
                    zeit = frame.locator("text=/.*Time details/").first
                    if await zeit.count() > 0:
                        await zeit.click(timeout=TIMEOUT)
                        await asyncio.sleep(1.5)
                    continue
                print(f"    [!] Date {date_str} not found. Available: {list(date_to_label.keys())}")
                return False

            day_label = date_to_label[date_str]
            day_name = day_label.split()[0]

            print(f"    Time details ({day_name}): {hours_str}h ... ", end="", flush=True)

            try:
                day_cell = frame.locator(f"text=/^{day_name} \\d/").first
                if await day_cell.count() == 0:
                    print(f"{day_name} not visible, retry...", flush=True)
                    continue

                print("found row ... ", end="", flush=True)
                row = day_cell.locator("xpath=ancestor::tr[1]")

                # Find the editable cell
                all_cells = await row.locator("td").all()
                erfasst_cell = None

                for cell in reversed(all_cells):
                    try:
                        if not await cell.is_visible(timeout=200):
                            continue
                        text = (await cell.inner_text(timeout=300)).strip()
                        if text and Patterns.NUMERIC_CELL.match(text):
                            erfasst_cell = cell
                            print(f"cell '{text}' ... ", end="", flush=True)
                            break
                    except Exception:
                        continue

                if not erfasst_cell:
                    print("no cell visible, retry...", flush=True)
                    continue

                await erfasst_cell.dblclick(timeout=TIMEOUT)
                await asyncio.sleep(0.8)

                # Find active input
                active_input = None

                candidate = frame.locator("input:focus").first
                if await candidate.count() > 0:
                    active_input = candidate

                if not active_input:
                    candidate = erfasst_cell.locator("input:not([readonly])").first
                    if await candidate.count() > 0:
                        active_input = candidate

                if not active_input:
                    candidate = frame.locator("input[data-type='Double']:not([readonly]):not([disabled])").first
                    if await candidate.count() > 0:
                        active_input = candidate

                if active_input and await active_input.count() > 0:
                    try:
                        await active_input.fill(hours_str)
                    except Exception:
                        await active_input.evaluate(
                            f"el => {{ el.value = '{hours_str}'; el.dispatchEvent(new Event('change')); }}"
                        )
                    await self.page.keyboard.press("Tab")
                    await asyncio.sleep(0.5)
                    print("OK")
                    return True
                else:
                    print("no input found, retry...", flush=True)
                    continue

            except Exception as e:
                print(f"error: {e}, retry...", flush=True)
                continue

        # Manual fallback
        print()
        print(f"    [!] Could not fill {hours_str}h for {date_str} automatically.")
        print(f"    >>> Please enter {hours_str} in the Zeitdetails for {day_name} manually.")
        print("    >>> Press ENTER when done...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input)
        except EOFError:
            pass
        return True

    async def _expand_zeitdetails(self, frame: Frame) -> bool:
        """Expand the Zeitdetails section if collapsed."""
        print("    Expanding Zeitdetails...", end=" ", flush=True)

        day_patterns = [
            "text=/^(Mo|Di|Mi|Do|Fr|Sa|So) \\d+\\/\\d+/",
            "text=/^(Mo|Di|Mi|Do|Fr|Sa|So) \\d+\\.\\d+/",
            "text=/^(Mo|Di|Mi|Do|Fr|Sa|So)\\s+\\d/",
            "text=/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \\d+\\/\\d+/",
            "text=/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \\d+\\.\\d+/",
            "text=/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\\s+\\d/",
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

        if await check_expanded():
            print("already open")
            return True

        # Click the Zeitdetails header
        zeit_locators = [
            frame.locator("legend:has-text('Time details')").first,
            frame.locator("text=/[≫»▸▾].*Time details/").first,
            frame.locator("text='Time details'").first,
            frame.locator("div:has-text('Time details')").first,
        ]

        for locator in zeit_locators:
            try:
                if await locator.count() > 0 and await locator.is_visible(timeout=500):
                    text = await locator.inner_text(timeout=300)
                    print(f"clicking '{text[:20]}'...", end=" ", flush=True)
                    await locator.click(timeout=TIMEOUT)
                    await asyncio.sleep(2)

                    if await check_expanded():
                        print("OK")
                        return True
                    else:
                        print("waiting...", end=" ", flush=True)
                        await asyncio.sleep(1)
                        if await check_expanded():
                            print("OK")
                            return True
                    break
            except Exception:
                continue

        print("not expanded yet")
        return False

    async def _read_zeitdetails_structure(self, frame: Frame) -> dict[str, str]:
        """Read the Zeitdetails table structure."""
        date_to_label = {}

        try:
            day_rows = await frame.locator("text=/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \\d+\\.\\d+/").all()

            for row in day_rows:
                try:
                    label = await row.inner_text(timeout=500)
                    label = label.strip()

                    match = Patterns.DAY_DATE.match(label)
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

    async def _click_button(self, frame: Frame, text: str) -> bool:
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
                if await elem.count() > 0 and await elem.is_visible(timeout=1000):
                    await elem.click(timeout=TIMEOUT)
                    return True
            except Exception:
                continue
        return False

    async def save(self) -> bool:
        """Save changes in Unit4."""
        frame = await self.frame_manager.get_content_frame()

        saved = await self._click_button(frame, "Save")
        if not saved:
            saved = await self._click_button(self.page, "Save")
        if not saved:
            await self.page.keyboard.press("Control+s")
            saved = True

        if saved:
            await asyncio.sleep(2)
            await self._click_button(frame, "OK")
            await self._click_button(self.page, "OK")
            await asyncio.sleep(1)

        return saved

    async def wait_for_ready(self) -> bool:
        """Wait for the page to be ready (check for Ergänzen button).

        Returns:
            True if page is ready for editing
            False if week is already submitted or page didn't load
        """
        print("[*] Waiting for page to be ready...", end=" ", flush=True)
        frame = await self.frame_manager.get_content_frame()

        for i in range(10):
            # Check if week is already submitted
            is_submitted = await self._is_week_submitted(frame)
            if is_submitted:
                return False

            # Check for Add button (editable state)
            ergaenzen_btn = frame.get_by_text("Add", exact=True).first
            if await ergaenzen_btn.count() > 0 and await ergaenzen_btn.is_visible(timeout=1000):
                print("OK")
                return True
            await asyncio.sleep(1)
            if i == 9:
                print("TIMEOUT - 'Add' button not found!")
                return False

        return False

    async def _is_week_submitted(self, frame: Frame) -> bool:
        """Check if the week has already been submitted (Ready/Transferred)."""
        # Check for status indicators that mean the week is locked
        status_indicators = [
            ("Ready", "marked as ready"),
            ("Transferred", "already transferred"),
            ("Gesendet", "already sent"), # TODO: unknown en label
        ]

        for status_text, description in status_indicators:
            try:
                # Look for button or status text
                elem = frame.get_by_text(status_text, exact=True).first
                if await elem.count() > 0 and await elem.is_visible(timeout=500):
                    print(f"LOCKED ({description})")
                    print(f"    [!] Week is {description} and cannot be edited.")
                    return True
            except Exception:
                continue

        return False
