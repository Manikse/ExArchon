"""
ExArchon Speculative Brancher.
Для нових задач запускає кілька паралельних гілок (гіпотез) і вибирає найкращу.
"""
import asyncio
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

from kernel.cortex.react_engine import ReactEngine, ReActTrace


@dataclass
class BranchResult:
    hypothesis: str
    trace: ReActTrace
    score: float
    execution_time_ms: float


class SpeculativeBrancher:
    """
    Запускає кілька паралельних ReAct-гілок для нової задачі.
    Вибирає найуспішнішу.
    """

    def __init__(self, acl, drivers: Dict, memory=None, max_branches: int = 3):
        self.acl = acl
        self.drivers = drivers
        self.memory = memory
        self.max_branches = max_branches
        # NEW: Link to KernelRuntime
        self.kernel_runtime = None

    def attach_kernel_runtime(self, kernel_runtime):
        """Attach to parent KernelRuntime for capability validation."""
        self.kernel_runtime = kernel_runtime

    async def solve(self, user_input: str, session_id: str = "default") -> Optional[ReActTrace]:
        """
        1. Генерує гіпотези через LLM
        2. Запускає паралельно
        3. Повертає найкращий trace
        """
        hypotheses = await self._generate_hypotheses(user_input)
        if not hypotheses:
            hypotheses = [user_input]

        tasks = []
        for h in hypotheses:
            task = asyncio.create_task(self._run_branch(h, user_input, session_id))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        best = self._select_best(results)
        return best.trace if best else None

    async def _generate_hypotheses(self, user_input: str) -> List[str]:
        """LLM генерує 2-3 альтернативні підходи."""
        prompt = (
            f"Task: {user_input}\n\n"
            "Generate 2-3 different approaches to solve this task. "
            "Each approach should be a single sentence describing a different strategy. "
            "Format: one approach per line, no numbering, no markdown."
        )
        raw = await self.acl.execute(prompt, system_prompt="You are a strategy generator.")
        lines = [l.strip() for l in raw.split("\n") if l.strip() and len(l.strip()) > 10]
        return lines[:self.max_branches]

    async def _run_branch(self, hypothesis: str, original_input: str, session_id: str) -> BranchResult:
        """Виконує одну гілку."""
        start = time.time()
        adapted_input = f"Original task: {original_input}\nApproach: {hypothesis}"

        engine = ReactEngine(self.acl, self.drivers, memory=self.memory)
        # NEW: Pass kernel runtime to internal ReactEngine
        if self.kernel_runtime:
            engine.attach_kernel_runtime(self.kernel_runtime)

        trace = await engine.run(adapted_input, session_id=f"{session_id}_branch")

        elapsed_ms = (time.time() - start) * 1000
        score = self._score_trace(trace)

        return BranchResult(
            hypothesis=hypothesis,
            trace=trace,
            score=score,
            execution_time_ms=elapsed_ms
        )

    def _score_trace(self, trace: ReActTrace) -> float:
        """Оцінює якість trace."""
        if not trace.success:
            return 0.0

        score = 0.5
        successful_steps = sum(1 for s in trace.steps if not s.observation.startswith("[Error]"))
        score += 0.1 * successful_steps
        score -= 0.02 * len(trace.steps)
        if trace.final_answer and len(trace.final_answer) > 20:
            score += 0.2

        return max(0.0, min(1.0, score))

    def _select_best(self, results: List) -> Optional[BranchResult]:
        """Вибирає найкращий результат."""
        valid = [r for r in results if isinstance(r, BranchResult)]
        if not valid:
            return None
        return max(valid, key=lambda x: x.score)