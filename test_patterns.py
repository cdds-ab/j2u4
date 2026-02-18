"""Tests for regex patterns and locale configuration.

These tests verify the language-independent parts of the sync logic
(patterns, locale dict consistency) without requiring a running Unit4
instance or browser.
"""

import re

import pytest

from patterns import Patterns
from unit4_browser import DAY_ABBREV_PATTERN, LOCALE_STRINGS


# ---------------------------------------------------------------------------
# DAY_DATE pattern — must match both DE and EN day row labels
# ---------------------------------------------------------------------------

class TestDayDatePattern:
    """Patterns.DAY_DATE must parse day labels from Zeitdetails / Time details."""

    @pytest.mark.parametrize(
        "label, expected_day, expected_month, expected_date",
        [
            # German labels
            ("Mo 3/02", "Mo", "3", "02"),
            ("Di 4/02", "Di", "4", "02"),
            ("Mi 5/02", "Mi", "5", "02"),
            ("Do 6/02", "Do", "6", "02"),
            ("Fr 7/02", "Fr", "7", "02"),
            ("Sa 8/02", "Sa", "8", "02"),
            ("So 9/02", "So", "9", "02"),
            # English labels
            ("Mon 3/02", "Mon", "3", "02"),
            ("Tue 4/02", "Tue", "4", "02"),
            ("Wed 5/02", "Wed", "5", "02"),
            ("Thu 6/02", "Thu", "6", "02"),
            ("Fri 7/02", "Fri", "7", "02"),
            ("Sat 8/02", "Sat", "8", "02"),
            ("Sun 9/02", "Sun", "9", "02"),
            # Double-digit day/month
            ("Mo 27/01", "Mo", "27", "01"),
            ("Fri 14/11", "Fri", "14", "11"),
            # Dot separator (locale=de date format)
            ("Mo 3.02", "Mo", "3", "02"),
            ("Fri 14.11", "Fri", "14", "11"),
        ],
    )
    def test_day_date_matches(self, label, expected_day, expected_month, expected_date):
        m = Patterns.DAY_DATE.match(label)
        assert m is not None, f"DAY_DATE should match '{label}'"
        assert m.group(1) == expected_day
        assert m.group(2) == expected_month
        assert m.group(3) == expected_date

    @pytest.mark.parametrize(
        "label",
        [
            "Lun 3/02",   # French
            "3/02 Mo",    # wrong order
            "",
            "Total 40:00",
        ],
    )
    def test_day_date_rejects(self, label):
        assert Patterns.DAY_DATE.match(label) is None


# ---------------------------------------------------------------------------
# DAY_ABBREV_PATTERN — used in Playwright locators
# ---------------------------------------------------------------------------

class TestDayAbbrevPattern:
    """DAY_ABBREV_PATTERN must match all DE and EN day abbreviations."""

    @pytest.mark.parametrize("abbrev", ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"])
    def test_matches_german(self, abbrev):
        assert re.fullmatch(DAY_ABBREV_PATTERN, abbrev)

    @pytest.mark.parametrize("abbrev", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    def test_matches_english(self, abbrev):
        assert re.fullmatch(DAY_ABBREV_PATTERN, abbrev)

    def test_rejects_unknown(self):
        assert re.fullmatch(DAY_ABBREV_PATTERN, "Lun") is None


# ---------------------------------------------------------------------------
# WORKLOG_MARKER — [WL:12345]
# ---------------------------------------------------------------------------

class TestWorklogMarker:

    @pytest.mark.parametrize(
        "text, expected_id",
        [
            ("[WL:1764] working on concept", "1764"),
            ("some prefix [WL:99999] suffix", "99999"),
            ("[WL:1]", "1"),
        ],
    )
    def test_extracts_id(self, text, expected_id):
        m = Patterns.WORKLOG_MARKER.search(text)
        assert m is not None
        assert m.group(1) == expected_id

    def test_no_match(self):
        assert Patterns.WORKLOG_MARKER.search("no marker here") is None


# ---------------------------------------------------------------------------
# TICKET_KEY — ABC-123
# ---------------------------------------------------------------------------

class TestTicketKey:

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("PROJ-42 some text", "PROJ-42"),
            ("working on ACME-1234", "ACME-1234"),
            ("TOOLONG-1 ok", "TOOLONG-1"),
        ],
    )
    def test_extracts_key(self, text, expected):
        m = Patterns.TICKET_KEY.search(text)
        assert m is not None
        assert m.group(1) == expected

    def test_too_short_prefix(self):
        # Min 3 uppercase letters
        assert Patterns.TICKET_KEY.search("AB-1") is None


# ---------------------------------------------------------------------------
# ARBAUFT — 1234-56789-001
# ---------------------------------------------------------------------------

class TestArbauft:

    def test_extracts(self):
        m = Patterns.ARBAUFT.search("order 1234-56789-001 done")
        assert m.group(1) == "1234-56789-001"

    def test_no_match(self):
        assert Patterns.ARBAUFT.search("1234-5678-001") is None  # wrong segment length


# ---------------------------------------------------------------------------
# LOCALE_STRINGS — consistency checks
# ---------------------------------------------------------------------------

class TestLocaleStrings:
    """Both locales must define the same keys with compatible types."""

    def test_both_locales_present(self):
        assert "de" in LOCALE_STRINGS
        assert "en" in LOCALE_STRINGS

    def test_same_keys(self):
        assert set(LOCALE_STRINGS["de"].keys()) == set(LOCALE_STRINGS["en"].keys())

    def test_status_locked_non_empty(self):
        for locale in ("de", "en"):
            statuses = LOCALE_STRINGS[locale]["status_locked"]
            assert len(statuses) >= 3, f"{locale}: need at least 3 status values"
            assert all(isinstance(s, str) and s for s in statuses)

    def test_string_values_non_empty(self):
        for locale in ("de", "en"):
            for key in ("confirm_yes", "cancel", "menu_text", "time_details"):
                val = LOCALE_STRINGS[locale][key]
                assert isinstance(val, str) and val, f"{locale}.{key} must be non-empty string"
