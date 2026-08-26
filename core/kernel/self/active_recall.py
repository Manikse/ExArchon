"""
kernel/self/active_recall.py
ExArchon Active Recall & Sleep Cycle.

SleepCycle is a periodic background task — the "dream/sleep" of the kernel:
  - consolidates working memory (HNMA L2) into long-term facts
  - reduces over-recent events into a compact daily digest
  - posts reminders through the kernel's notice system (proactive recall)

This turns memory from a passive store into an active participant in cognition.
"""

import time
import asyncio
import logging
from typing import Optional, Callable, List, Dict, Any


class SleepCycle:
    """
    Periodic consolidation task.

    Callback style is dependency-injected so it works
    with any memory implementation (HNMA, UNMS, etc.):
      - consolidate_fn: async callable() -> str summary of what was consolidated
      - notice_post:    async callable(title, message, severity) or injection
      - generate_fact_fn: async callable() -> Optional[str] new derived fact
    """

    def __init__(
        self,
        consolidate_fn: Callable[[], Any],
        notice_post: Callable[[str, str, str], Any],
        generate_fact_fn: Optional[Callable[[], Any]] = None,
        interval_s: int = 43200,          # default: every 12h
        logger: Optional[logging.Logger] = None,
    ):
        self.consolidate_fn = consolidate_fn
        self.notice_post = notice_post
        self.generate_fact_fn = generate_fact_fn
        self.interval_s = interval_s
        self.logger = logger or logging.getLogger("SleepCycle")
        self._running = False
        self.last_consolidation_ts: float = 0.0
        self.cycles_run: int = 0

    async def run_once(self) -> Optional[str]:
        """Perform a single consolidation cycle. Returns summary string."""
        summary = ""
        try:
            # 1) Consolidate memory
            cons = self.consolidate_fn()
            if asyncio.iscoroutine(cons):
                cons = await cons
            if cons:
                summary += f"Memory consolidated: {cons}\n"

            # 2) Derive a new fact if a generator is available
            if self.generate_fact_fn:
                fact = self.generate_fact_fn()
                if asyncio.iscoroutine(fact):
                    fact = await fact
                if fact:
                    summary += f"New derived fact: {fact}\n"

            # 3) Post a proactive "wake" notice so the cortex knows it slept
            title = "ExArchon Sleep Cycle Complete"
            message = summary.strip() or "Consolidation finished with no new facts."
            res = self.notice_post(title, message, "low")
            if asyncio.iscoroutine(res):
                await res

            self.last_consolidation_ts = time.time()
            self.cycles_run += 1
            self.logger.info(f"[SleepCycle] cycle #{self.cycles_run}: {summary[:200]}")
            return summary
        except Exception as e:
            self.logger.warning(f"[SleepCycle] cycle failed: {e}")
            return None

    async def run_forever(self, sleep_fn=None):
        """Run consolidation periodically until stopped."""
        self._running = True
        sleep_fn = sleep_fn or (lambda: asyncio.sleep(self.interval_s))
        while self._running:
            await self.run_once()
            await sleep_fn()

    def stop(self):
        self._running = False