"""Centralized regex patterns for worklog sync."""

import re


class Patterns:
    """Regex patterns used throughout the sync process."""

    # Worklog marker: [WL:12345]
    WORKLOG_MARKER = re.compile(r"\[WL:(\d+)\]")

    # Jira ticket key: ABC-123
    TICKET_KEY = re.compile(r"([A-Z]{3,10}-\d+)")

    # Unit4 ArbAuft: 1234-56789-001
    ARBAUFT = re.compile(r"(\d{4}-\d{5}-\d{3})")

    # Day row label in Zeitdetails: "Mo 1/26" or "Di 27/01" (DE + EN)
    DAY_DATE = re.compile(r"^(Mo|Di|Mi|Do|Fr|Sa|So|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d+)[/.](\d+)")

    # Week format: YYYYWW (e.g., 202605)
    WEEK_FORMAT = re.compile(r"^\d{6}$")

    # Date format: YYYY-MM-DD
    DATE_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    # Numeric cell value (hours): 0.00, 8:00, 7,5
    NUMERIC_CELL = re.compile(r"^[\d:,.]+$")
