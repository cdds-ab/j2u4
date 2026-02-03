"""Data models for Tempo to Unit4 sync."""

from dataclasses import dataclass, field


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


@dataclass
class SyncConfig:
    """Configuration for sync behavior."""

    timeout_ms: int = 10000
    max_retries: int = 3
    retry_delay_s: float = 1.0
    slow_mo: int = 100


@dataclass
class SyncState:
    """State tracking for a sync operation."""

    worklogs: list[TempoWorklog] = field(default_factory=list)
    existing_entries: list[Unit4Entry] = field(default_factory=list)
    created: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
