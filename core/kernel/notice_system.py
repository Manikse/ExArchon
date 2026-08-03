"""
kernel/notice_system.py

Notice System / Event Abstraction Layer

Замість спаму сирими логами ("Core temp: 25C", "Core temp: 26C", "Core temp: 25C"...)
— агреговані, пріоритизовані сповіщення.

Як у Рафаель:
  ❌ "Temperature sensor reading: 25.3 degrees Celsius"
  ✅ "System nominal. All parameters within safe bounds."

  ❌ "Temperature sensor reading: 35.0 degrees Celsius"
  ✅ "Notice: Thermal anomaly detected. Recommend reducing load."
"""

from __future__ import annotations

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable
from collections import deque


class NoticeSeverity(Enum):
    DEBUG = 0      # Тільки для розробки
    INFO = 1       # Загальна інформація
    NOTICE = 2     # Важлива подія (аналог "Notice:" у Рафаель)
    WARNING = 3    # Потребує уваги
    CRITICAL = 4   # Негайна реакція


@dataclass
class Notice:
    """Одне агреговане сповіщення."""
    id: str
    title: str                    # Коротко, як у Рафаель: "Analysis Complete"
    message: str                  # Деталі
    severity: NoticeSeverity
    source: str                   # Звідки: "sensory", "cortex", "memory"
    timestamp_ns: int = field(default_factory=lambda: time.monotonic_ns())
    acknowledged: bool = False    # Користувач/система прочитала
    auto_dismiss_ms: int = 0      # 0 = вічне, інакше — TTL

    def is_expired(self) -> bool:
        if self.auto_dismiss_ms <= 0:
            return False
        elapsed_ms = (time.monotonic_ns() - self.timestamp_ns) / 1e6
        return elapsed_ms > self.auto_dismiss_ms


class NoticeBoard:
    """Дошка сповіщень. Зберігає останні N нотисів."""

    def __init__(self, max_notices: int = 50):
        self._notices: deque[Notice] = deque(maxlen=max_notices)
        self._counter = 0

    def post(self, notice: Notice) -> str:
        """Додати сповіщення. Повертає ID."""
        self._counter += 1
        notice.id = f"NTC-{self._counter:04d}"
        self._notices.append(notice)
        return notice.id

    def get_active(self, min_severity: NoticeSeverity = NoticeSeverity.DEBUG) -> List[Notice]:
        """Отримати всі активні (не прострочені) нотиси від певного рівня."""
        result = []
        for n in self._notices:
            if n.severity.value >= min_severity.value and not n.is_expired():
                result.append(n)
        return result

    def get_latest(self, count: int = 5) -> List[Notice]:
        """Останні N нотисів."""
        return list(self._notices)[-count:]

    def acknowledge(self, notice_id: str) -> bool:
        for n in self._notices:
            if n.id == notice_id:
                n.acknowledged = True
                return True
        return False

    def clear_acknowledged(self):
        """Прибрати всі acknowledged."""
        self._notices = deque([n for n in self._notices if not n.acknowledged], maxlen=self._notices.maxlen)

    def to_console_string(self, count: int = 10) -> str:
        """Форматування для виводу у консоль."""
        lines = []
        for n in self.get_active()[-count:]:
            icon = {
                NoticeSeverity.DEBUG: "◆",
                NoticeSeverity.INFO: "●",
                NoticeSeverity.NOTICE: "▲",
                NoticeSeverity.WARNING: "⚠",
                NoticeSeverity.CRITICAL: "✖",
            }.get(n.severity, "?")
            status = "[ACK]" if n.acknowledged else "[NEW]"
            lines.append(f"{status} {icon} [{n.source}] {n.title}: {n.message}")
        return "\n".join(lines) if lines else "No active notices."


class NoticeSystem:
    """
    Головний модуль. Отримує сирі події, агрегує їх у нотиси.

    Приклад:
      Raw events: ["temp:25", "temp:26", "temp:25", "temp:35"]
      → Notice("System nominal", "All thermal parameters stable", INFO)
      → Notice("Thermal anomaly", "Core temperature exceeded threshold", WARNING)
    """

    def __init__(self, board: Optional[NoticeBoard] = None):
        self.board = board or NoticeBoard()
        self._aggregators: Dict[str, Callable[[List[str]], Optional[Notice]]] = {}
        self._raw_buffers: Dict[str, List[str]] = {}  # буфер сирих подій по джерелу
        self._last_notices: Dict[str, str] = {}  # щоб не дублювати

    def register_aggregator(self, source: str, fn: Callable[[List[str]], Optional[Notice]]):
        """
        Реєстрація агрегатора для джерела.

        fn приймає список сирих рядків і повертає Notice або None.
        """
        self._aggregators[source] = fn
        self._raw_buffers[source] = []

    def feed_raw(self, source: str, raw_data: str):
        """Надіслати сиру подію на обробку."""
        if source not in self._raw_buffers:
            self._raw_buffers[source] = []
        self._raw_buffers[source].append(raw_data)

        # Якщо є агрегатор — запускаємо
        if source in self._aggregators:
            notice = self._aggregators[source](self._raw_buffers[source])
            if notice:
                # Дедуплікація: якщо такий самий тайтл вже був — не постимо
                key = f"{source}:{notice.title}"
                if self._last_notices.get(key) != notice.message:
                    self.board.post(notice)
                    self._last_notices[key] = notice.message
                # Чистимо буфер після агрегації
                self._raw_buffers[source] = []

    def get_board(self) -> NoticeBoard:
        return self.board


# --- Predefined aggregators (як у Рафаель) ---

def thermal_aggregator(raw_events: List[str]) -> Optional[Notice]:
    """
    Агрегатор температури.
    Замість "temp:25, temp:26, temp:25" → "System nominal"
    """
    if len(raw_events) < 3:
        return None  # Недостатньо даних

    # Парсимо температури
    temps = []
    for ev in raw_events:
        try:
            # Формат: "Core temp: 25C" або просто "25"
            val = float(''.join(c for c in ev if c.isdigit() or c == '.'))
            temps.append(val)
        except ValueError:
            continue

    if not temps:
        return None

    avg = sum(temps) / len(temps)
    max_t = max(temps)

    if max_t > 33:
        return Notice(
            id="",
            title="Thermal Anomaly",
            message=f"Core temperature peaked at {max_t:.1f}°C. Recommend reducing cognitive load.",
            severity=NoticeSeverity.WARNING,
            source="sensory",
            auto_dismiss_ms=30000,
        )
    elif avg < 30:
        return Notice(
            id="",
            title="System Nominal",
            message=f"All thermal parameters stable (avg {avg:.1f}°C).",
            severity=NoticeSeverity.INFO,
            source="sensory",
            auto_dismiss_ms=60000,
        )
    return None


def skill_aggregator(raw_events: List[str]) -> Optional[Notice]:
    """
    Агрегатор подій скиллів.
    """
    if not raw_events:
        return None
    last = raw_events[-1]
    if "compiled" in last.lower():
        return Notice(
            id="",
            title="Skill Synthesis Complete",
            message="New muscle memory module compiled and ready for execution.",
            severity=NoticeSeverity.NOTICE,
            source="cortex",
            auto_dismiss_ms=15000,
        )
    if "error" in last.lower():
        return Notice(
            id="",
            title="Compilation Failed",
            message="Skill synthesis encountered errors. Check logs for details.",
            severity=NoticeSeverity.WARNING,
            source="cortex",
            auto_dismiss_ms=30000,
        )
    return None