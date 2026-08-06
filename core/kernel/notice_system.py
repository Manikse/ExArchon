"""
kernel/notice_system.py
Notice System v2 — Persistence + Rate Limiting + NoticeBoard compatibility.

Зміни: додано SQLite persistence для critical notices, rate limiting, dedup.
Збережено: NoticeBoard, get_board(), register_aggregator, thermal_aggregator, skill_aggregator.
"""
import time
import sqlite3
import os
from collections import deque
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from enum import Enum


class NoticeSeverity(Enum):
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ALERT = "alert"
    CRITICAL = "critical"


@dataclass
class Notice:
    title: str
    message: str
    severity: NoticeSeverity
    source: str = "kernel"
    timestamp: float = field(default_factory=time.time)


class NoticeBoard:
    """
    In-memory notice board for live display.
    Compatible with original ExArchon API.
    """

    def __init__(self, max_notices: int = 100):
        self._notices: deque = deque(maxlen=max_notices)

    def add(self, notice: Notice):
        self._notices.append(notice)

    def get_all(self) -> List[Notice]:
        return list(self._notices)

    def clear(self):
        self._notices.clear()

    def to_console_string(self, count: int = 10) -> str:
        """Format notices for Rich console display."""
        notices = list(self._notices)[-count:]
        if not notices:
            return "[dim]No notices available.[/dim]"
        lines = []
        for n in notices:
            sev_color = {
                "debug": "dim",
                "info": "blue",
                "notice": "cyan",
                "warning": "yellow",
                "alert": "bright_red",
                "critical": "bold red",
            }.get(n.severity.value, "white")
            ts = time.strftime("%H:%M:%S", time.localtime(n.timestamp))
            lines.append(f"[{ts}] [{sev_color}]{n.severity.value.upper()}[/{sev_color}] {n.source}: {n.message}")
        return "\n".join(lines)


def thermal_aggregator(raw_events: List[str]) -> Optional[Notice]:
    """Aggregate raw thermal events into a single notice."""
    if not raw_events:
        return None
    # Parse temperatures
    temps = []
    for ev in raw_events:
        try:
            # Extract number before 'C'
            parts = ev.split("Core temp:")
            if len(parts) > 1:
                temp_str = parts[1].strip().replace("C", "").strip()
                temps.append(int(temp_str))
        except (ValueError, IndexError):
            continue
    if not temps:
        return None
    avg_temp = sum(temps) / len(temps)
    max_temp = max(temps)
    if max_temp > 50:
        return Notice(
            title="Thermal Alert",
            message=f"Core temperature high: {max_temp}°C (avg {avg_temp:.1f}°C)",
            severity=NoticeSeverity.WARNING,
            source="thermal",
        )
    return Notice(
        title="Thermal Status",
        message=f"Core temperature normal: {avg_temp:.1f}°C",
        severity=NoticeSeverity.INFO,
        source="thermal",
    )


def skill_aggregator(raw_events: List[str]) -> Optional[Notice]:
    """Aggregate skill-related events."""
    if not raw_events:
        return None
    return Notice(
        title="Skill Activity",
        message=f"{len(raw_events)} skill events in last window",
        severity=NoticeSeverity.INFO,
        source="cortex",
    )


class NoticeSystem:
    """
    Raphael-style notices — human readable, not log spam.
    v2: SQLite persistence for critical, rate limiting, dedup.
    Compatible with original ExArchon API.
    """

    def __init__(
        self,
        db_path: str = "./kernel_workspace/notices.db",
        max_live_notices: int = 100,
        rate_limit_seconds: float = 5.0,
    ):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._board = NoticeBoard(max_live_notices)
        self._rate_limit = rate_limit_seconds
        self._last_posted: Dict[str, float] = {}  # source -> last timestamp
        self._aggregators: Dict[str, Callable] = {}
        self._raw_buffers: Dict[str, List[str]] = {}
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                severity TEXT NOT NULL,
                source TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_severity ON notices(severity, timestamp)
        """)

    def register_aggregator(self, name: str, aggregator: Callable):
        """Register an aggregator function for a source."""
        self._aggregators[name] = aggregator
        self._raw_buffers[name] = []

    def feed_raw(self, source: str, raw_event: str):
        """Feed a raw event to be aggregated."""
        if source not in self._raw_buffers:
            self._raw_buffers[source] = []
        self._raw_buffers[source].append(raw_event)
        # Trigger aggregation immediately for simplicity
        if source in self._aggregators:
            notice = self._aggregators[source](self._raw_buffers[source])
            if notice:
                self.post(
                    title=notice.title,
                    message=notice.message,
                    severity=notice.severity.value,
                    source=source,
                )
            # Clear buffer after aggregation
            self._raw_buffers[source] = []

    def post(self, title: str, message: str, severity: str = "info", source: str = "kernel"):
        """Post a notice. Critical/Alert/Warning are persisted."""
        try:
            sev = NoticeSeverity(severity.lower())
        except ValueError:
            sev = NoticeSeverity.INFO

        # Rate limiting per source
        now = time.time()
        last = self._last_posted.get(source, 0)
        if now - last < self._rate_limit:
            # Skip if same message recently
            if self._board.get_all() and self._board.get_all()[-1].message == message:
                return

        self._last_posted[source] = now

        notice = Notice(title=title, message=message, severity=sev, source=source)
        self._board.add(notice)

        # Persist critical notices
        if sev in (NoticeSeverity.CRITICAL, NoticeSeverity.ALERT, NoticeSeverity.WARNING):
            with self._conn:
                self._conn.execute("""
                    INSERT INTO notices (title, message, severity, source, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (title, message, sev.value, source, now))

        # Print based on severity
        prefix = f"[{sev.value.upper()}]"
        print(f"\n{prefix} {title}: {message}")

    def get_board(self) -> NoticeBoard:
        """Return the live notice board (compatible with original API)."""
        return self._board

    def get_recent(self, severity_filter: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Get recent notices from live buffer."""
        result = []
        for n in reversed(self._board.get_all()):
            if severity_filter and n.severity.value != severity_filter:
                continue
            result.append({
                "title": n.title,
                "message": n.message,
                "severity": n.severity.value,
                "source": n.source,
                "timestamp": n.timestamp,
            })
            if len(result) >= limit:
                break
        return result

    def get_persistent(self, min_severity: str = "warning", limit: int = 50) -> List[Dict]:
        """Get persisted notices from SQLite."""
        sev_order = {"debug": 0, "info": 1, "notice": 2, "warning": 3, "alert": 4, "critical": 5}
        min_level = sev_order.get(min_severity, 3)

        rows = self._conn.execute("""
            SELECT * FROM notices
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()

        result = []
        for r in rows:
            if sev_order.get(r["severity"], 0) >= min_level:
                result.append(dict(r))
        return result

    def clear(self):
        self._board.clear()

    def close(self):
        if self._conn:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
            self._conn = None