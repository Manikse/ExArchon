"""
kernel/self/goal_loop.py
ExArchon GoalLoop — proactive self-direction.

Kernel periodically picks a sub-goal from its "direction" (high-level mandate),
executes it through the normal runtime, self-evaluates the outcome,
and records progress into memory. This turns the kernel from reactive to proactive.
"""

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Any, Dict


@dataclass
class Goal:
    id: str
    title: str
    description: str
    status: str = "pending"          # pending | active | done | failed
    created_at: float = field(default_factory=time.time)
    result: str = ""
    score: float = 0.0               # 0..1 self-evaluation


class GoalLoop:
    """
    Proactive loop. Given a direction (a List of candidate goal templates),
    it rotates through them, executes each via a provided async executor,
    and logs the outcome.

    The executor is typically `kernel.process_reactive(prompt)` — any async
    callable that takes a string goal prompt and returns (ok, summary).
    """

    def __init__(
        self,
        direction: List[str],
        executor: Callable[[str], Any],
        interval_s: int = 3600,
        logger: Optional[logging.Logger] = None,
    ):
        """
        :param direction: high-level mandates, e.g.
            ["Improve the test suite", "Search the web for new papers on RL",
             "Consolidate today's interactions in memory"]
        :param executor: async callable(goal_prompt) -> (bool ok, str summary)
        :param interval_s: seconds between autonomous goal runs
        """
        self.direction = [g for g in direction if g]
        self.executor = executor
        self.interval_s = interval_s
        self.logger = logger or logging.getLogger("GoalLoop")
        self.goals: List[Goal] = []
        self._cursor = 0
        self._running = False

    def _next_goal_title(self) -> Optional[str]:
        if not self.direction:
            return None
        # rotate through direction
        title = self.direction[self._cursor % len(self.direction)]
        self._cursor += 1
        return title

    async def pick_and_execute(self) -> Optional[Goal]:
        """Pick the next goal from direction, execute via executor, self-evaluate."""
        title = self._next_goal_title()
        if not title:
            return None

        goal = Goal(id=f"g{int(time.time())}", title=title, description=title, status="active")
        self.goals.append(goal)
        self.logger.info(f"[GoalLoop] Executing autonomous goal: {title}")
        try:
            result = await self.executor(title)
            # executor returns (ok, summary)
            if isinstance(result, tuple) and len(result) == 2:
                ok, summary = result
            else:
                ok, summary = bool(result), str(result)

            goal.status = "done" if ok else "failed"
            goal.result = summary
            goal.score = 0.9 if ok else 0.2
            self.logger.info(f"[GoalLoop] Goal '{title}' -> {'OK' if ok else 'FAIL'}: {summary[:120]}")
        except Exception as e:
            goal.status = "failed"
            goal.result = f"GoalLoop exception: {e}"
            goal.score = 0.0
            self.logger.warning(f"[GoalLoop] Goal '{title}' exception: {e}")

        return goal

    async def run_forever(self, sleep_fn=None):
        """
        Infinite proactive loop. Sleep interval between runs.
        :param sleep_fn: async sleep override for tests (e.g. a small value).
        """
        self._running = True
        sleep_fn = sleep_fn or (lambda: asyncio.sleep(self.interval_s))
        while self._running:
            await self.pick_and_execute()
            await sleep_fn()

    def stop(self):
        self._running = False

    def summary(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": g.id,
                "title": g.title,
                "status": g.status,
                "score": g.score,
                "result": g.result[:200] if g.result else "",
            }
            for g in self.goals
        ]