"""
Inspect Unit4 UI elements to find stable, language-independent selectors.

Opens Unit4 in a browser, navigates to Zeiterfassung, and dumps HTML
attributes of key UI elements. Share the output to help make the
automation work with non-German UI languages.

Usage:
    uv run python inspect_ui.py
"""

import asyncio
import json
import os
from playwright.async_api import async_playwright, Frame

from utils import load_config_safe, SESSION_FILE

ELEMENTS_TO_INSPECT = [
    {
        "name": "Menu: Zeiterfassung",
        "description": "Navigation menu item to open time recording",
        "hint": "DE: 'Zeiterfassung - Standard'",
        "phase": "before_navigate",
    },
    {
        "name": "Week field",
        "description": "Input field for the week number (e.g., 202603)",
        "hint": "DE label: 'Woche'",
        "phase": "after_navigate",
    },
    {
        "name": "Add button",
        "description": "Button to add a new time entry row",
        "hint": "DE: 'Ergänzen'",
        "phase": "after_navigate",
    },
    {
        "name": "Status indicators",
        "description": "Status texts like Ready/Transferred/Sent",
        "hint": "DE: 'Bereit', 'Transferiert', 'Gesendet'",
        "phase": "after_navigate",
    },
    {
        "name": "Cancel button",
        "description": "Button to cancel/close dialogs",
        "hint": "DE: 'Abbrechen'",
        "phase": "after_navigate",
    },
    {
        "name": "OK button",
        "description": "Button to confirm dialogs",
        "hint": "DE: 'OK'",
        "phase": "after_navigate",
    },
]


async def dump_outer_html(frame: Frame, selector: str, limit: int = 3) -> list[str]:
    """Get outer HTML of elements matching a selector."""
    results = []
    try:
        elements = await frame.locator(selector).all()
        for elem in elements[:limit]:
            try:
                html = await elem.evaluate("el => el.outerHTML")
                # Truncate long innerHTML but keep attributes
                if len(html) > 500:
                    tag_end = html.index(">") + 1
                    html = html[:tag_end] + "..."
                results.append(html)
            except Exception:
                continue
    except Exception:
        pass
    return results


async def inspect_frame(frame: Frame) -> dict:
    """Inspect all interesting elements in a frame."""
    findings = {}

    # Buttons and links (most important for navigation)
    print("  Scanning buttons and links...")
    for selector, label in [
        ("button", "buttons"),
        ("a[href]", "links"),
        ("input[type='button']", "input_buttons"),
        ("input[type='submit']", "submit_buttons"),
    ]:
        elements = []
        try:
            locators = await frame.locator(selector).all()
            for loc in locators[:30]:
                try:
                    info = await loc.evaluate("""el => ({
                        tag: el.tagName,
                        id: el.id || null,
                        name: el.name || null,
                        class: el.className || null,
                        text: el.textContent?.trim().substring(0, 80) || null,
                        value: el.value || null,
                        title: el.title || null,
                        type: el.type || null,
                        'aria-label': el.getAttribute('aria-label'),
                        'data-action': el.getAttribute('data-action'),
                        'data-command': el.getAttribute('data-command'),
                        onclick: el.getAttribute('onclick')?.substring(0, 120) || null,
                        href: el.href || null,
                        visible: el.offsetParent !== null,
                    })""")
                    # Skip invisible or empty
                    if info.get("text") or info.get("value") or info.get("id"):
                        elements.append(info)
                except Exception:
                    continue
        except Exception:
            pass
        if elements:
            findings[label] = elements

    # Labeled inputs (form fields)
    print("  Scanning form fields...")
    inputs = []
    try:
        locators = await frame.locator("input, select, textarea").all()
        for loc in locators[:50]:
            try:
                info = await loc.evaluate("""el => {
                    // Find associated label
                    let label = null;
                    if (el.id) {
                        const labelEl = el.ownerDocument.querySelector('label[for="' + el.id + '"]');
                        if (labelEl) label = labelEl.textContent?.trim();
                    }
                    if (!label && el.closest('label')) {
                        label = el.closest('label').textContent?.trim();
                    }
                    return {
                        tag: el.tagName,
                        type: el.type || null,
                        id: el.id || null,
                        name: el.name || null,
                        class: el.className || null,
                        label: label,
                        placeholder: el.placeholder || null,
                        'aria-label': el.getAttribute('aria-label'),
                        title: el.title || null,
                        value: el.value?.substring(0, 40) || null,
                        visible: el.offsetParent !== null,
                    };
                }""")
                if info.get("visible") and (info.get("id") or info.get("name") or info.get("label")):
                    inputs.append(info)
            except Exception:
                continue
    except Exception:
        pass
    if inputs:
        findings["form_fields"] = inputs

    # Status/header texts
    print("  Scanning status elements...")
    status_elements = []
    try:
        # Look for dropdown/select with status values
        selects = await frame.locator("select").all()
        for sel in selects[:10]:
            try:
                info = await sel.evaluate("""el => ({
                    id: el.id || null,
                    name: el.name || null,
                    label: el.closest('label')?.textContent?.trim() || null,
                    options: Array.from(el.options).map(o => ({value: o.value, text: o.text})),
                    selectedText: el.options[el.selectedIndex]?.text || null,
                })""")
                if info.get("options"):
                    status_elements.append(info)
            except Exception:
                continue
    except Exception:
        pass
    if status_elements:
        findings["selects"] = status_elements

    return findings


async def main():
    print("=" * 70)
    print("UNIT4 UI INSPECTOR")
    print("=" * 70)
    print()
    print("This script inspects Unit4 UI elements and dumps their HTML")
    print("attributes to help find language-independent selectors.")
    print()

    config = load_config_safe()
    if config is None:
        return

    unit4_url = config.get("unit4", {}).get("url")
    if not unit4_url:
        print("[!] Error: unit4.url not configured in config.json")
        return

    output = {
        "url": unit4_url,
        "elements_needed": [e["name"] + " (" + e["hint"] + ")" for e in ELEMENTS_TO_INSPECT],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)

        if os.path.exists(SESSION_FILE):
            print("[*] Loading session...")
            context = await browser.new_context(storage_state=SESSION_FILE)
        else:
            context = await browser.new_context()

        page = await context.new_page()

        # Open Unit4
        print("[*] Opening Unit4...")
        await page.goto(unit4_url)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        if "Login" in await page.title():
            print("[!] Please log in (2FA if needed), then press ENTER...")
            await asyncio.get_event_loop().run_in_executor(None, input)
            await context.storage_state(path=SESSION_FILE)
            await asyncio.sleep(2)

        # Inspect main page (before navigating to Zeiterfassung)
        print("\n[1] Inspecting main page (menu items)...")
        output["main_page"] = {}
        output["main_page"]["frames"] = len(page.frames)
        for i, frame in enumerate(page.frames):
            print(f"  Frame {i}: {frame.url[:80]}")
            findings = await inspect_frame(frame)
            if findings:
                output["main_page"][f"frame_{i}"] = findings

        # Navigate to Zeiterfassung
        print("\n[*] Please navigate to Zeiterfassung / Time Recording now.")
        print("    Then press ENTER...")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

        # Inspect Zeiterfassung page
        print("\n[2] Inspecting Zeiterfassung page...")
        output["zeiterfassung"] = {}
        output["zeiterfassung"]["frames"] = len(page.frames)
        for i, frame in enumerate(page.frames):
            url = frame.url[:80]
            print(f"  Frame {i}: {url}")
            findings = await inspect_frame(frame)
            if findings:
                output["zeiterfassung"][f"frame_{i}"] = findings

        await browser.close()

    # Save results
    outfile = "ui_inspection.json"
    with open(outfile, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 70)
    print(f"DONE - Results saved to {outfile}")
    print("=" * 70)
    print()
    print("Please share this file so we can find stable selectors.")
    print(f"File size: {os.path.getsize(outfile)} bytes")


if __name__ == "__main__":
    asyncio.run(main())
