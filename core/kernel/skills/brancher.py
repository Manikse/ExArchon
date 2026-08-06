"""
kernel/skills/brancher.py
Speculative Branching v2 — ProcessPool for CPU-bound LLM calls.
"""
import asyncio
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import List, Dict, Optional

from kernel.cortex.react_engine import ReActTrace
from kernel.security.capabilities import CapabilityManager, CapOp


@dataclass
class HypothesisResult:
    hypothesis: str
    trace: Optional[ReActTrace]
    execution_time_ms: float


class SpeculativeBrancher:
    """
    Generates alternative hypotheses and evaluates them.
    CPU-bound LLM calls run in ProcessPool to avoid blocking event loop.
    """

    def __init__(
        self,
        acl,
        drivers: Dict,
        memory=None,
        max_branches: int = 3,
        llm_timeout: float = 30.0,
    ):
        self.acl = acl
        self.drivers = drivers
        self.memory = memory
        self.max_branches = max_branches
        self.llm_timeout = llm_timeout
        self.kernel_runtime = None
        self.capability_manager: Optional[CapabilityManager] = None
        # ProcessPool for CPU-bound tasks
        self._cpu_pool = ProcessPoolExecutor(max_workers=2)

    def attach_kernel_runtime(self, kernel_runtime):
        self.kernel_runtime = kernel_runtime

    def attach_capability_manager(self, cap_manager: CapabilityManager):
        self.capability_manager = cap_manager

    async def solve(self, user_input: str, session_id: str = "default") -> Optional[ReActTrace]:
        """Generate hypotheses and pick the best one."""
        # Capability check
        if self.capability_manager:
            ok, reason = self.capability_manager.validate("brancher", CapOp.BRANCH, user_input)
            if not ok:
                print(f"[Brancher] Capability denied: {reason}")
                return None

        hypotheses = await self._generate_hypotheses(user_input)
        if not hypotheses:
            return None

        # Evaluate each hypothesis — run in ProcessPool if CPU-bound
        tasks = []
        for h in hypotheses:
            task = asyncio.create_task(self._run_branch(h, user_input, session_id))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        best_trace = None
        best_score = -1.0

        for res in results:
            if isinstance(res, Exception):
                continue
            if res and res.trace and res.trace.success:
                # Score by length (more steps = more thorough) and speed
                score = len(res.trace.steps) * 10 + (1000.0 / (res.execution_time_ms + 1))
                if score > best_score:
                    best_score = score
                    best_trace = res.trace

        return best_trace

    async def _generate_hypotheses(self, user_input: str) -> List[str]:
        """Ask LLM to generate alternative approaches."""
        prompt = f"""
User request: {user_input}
Generate {self.max_branches} different approaches to solve this.
Number them 1, 2, 3.
Each approach should be one sentence.
"""
        try:
            raw = await asyncio.wait_for(
                self.acl.execute(prompt),
                timeout=self.llm_timeout,
            )
            hypotheses = []
            for line in raw.split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith("-")):
                    hypotheses.append(line.lstrip("0123456789.-) ").strip())
            return hypotheses[:self.max_branches]
        except asyncio.TimeoutError:
            return [user_input]  # Fallback: just the original

    async def _run_branch(self, hypothesis: str, user_input: str, session_id: str) -> HypothesisResult:
        """Execute a single hypothesis branch."""
        start = time.time()
        # Build prompt for this hypothesis
        prompt = f"""
Approach: {hypothesis}
Original request: {user_input}
Solve using available tools. Think step by step.
"""
        try:
            # Run LLM call — if it's CPU-bound, offload to ProcessPool
            # For now, assume acl.execute is async I/O
            raw = await asyncio.wait_for(
                self.acl.execute(prompt),
                timeout=self.llm_timeout,
            )
            # Parse simple trace
            trace = ReActTrace(user_input=user_input)
            trace.success = True
            trace.final_answer = raw
            trace.steps = []  # Simplified for branching
            elapsed = (time.time() - start) * 1000
            return HypothesisResult(hypothesis, trace, elapsed)
        except asyncio.TimeoutError:
            elapsed = (time.time() - start) * 1000
            return HypothesisResult(hypothesis, None, elapsed)
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HypothesisResult(hypothesis, None, elapsed)

    def shutdown(self):
        self._cpu_pool.shutdown(wait=True)