"""
kernel/skills/brancher_fixed.py
Speculative Branching v3 — Fixed Edition.
Changes:
- ProcessPoolExecutor REMOVED (was wrong for IO-bound LLM)
- asyncio.Semaphore for concurrency control
- asyncio.gather for parallel branch execution
- Added structured output parsing (JSON schema)
"""
import asyncio
import time
import json
from dataclasses import dataclass
from typing import List, Dict, Optional

from kernel.cortex.react_engine import ReActTrace
from kernel.security.capabilities import CapabilityManager, CapOp


@dataclass
class HypothesisResult:
    hypothesis: str
    trace: Optional[ReActTrace]
    execution_time_ms: float
    score: float = 0.0


class SpeculativeBrancher:
    """
    Generates alternative hypotheses and evaluates them in parallel.
    CPU-bound work stays in event loop (LLM is IO-bound).
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
        # FIXED: Semaphore instead of ProcessPool for IO-bound LLM
        self._semaphore = asyncio.Semaphore(max_branches)
        self._react_engine = None

    def attach_kernel_runtime(self, kernel_runtime):
        self.kernel_runtime = kernel_runtime

    def attach_capability_manager(self, cap_manager: CapabilityManager):
        self.capability_manager = cap_manager

    async def solve(self, user_input: str, session_id: str = "default") -> Optional[ReActTrace]:
        # Capability check
        if self.capability_manager:
            ok, reason = self.capability_manager.validate("brancher", CapOp.BRANCH, user_input)
            if not ok:
                print(f"[Brancher] Capability denied: {reason}")
                return None

        hypotheses = await self._generate_hypotheses_structured(user_input)
        if not hypotheses:
            return None

        # Evaluate each hypothesis — parallel with semaphore
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
                # Score: success rate * thoroughness / latency
                score = (
                    len(res.trace.steps) * 10.0
                    + (1000.0 / (res.execution_time_ms + 1.0))
                    + (res.trace.reflection_count * -5.0)  # penalty for reflections
                )
                res.score = score
                if score > best_score:
                    best_score = score
                    best_trace = res.trace

        return best_trace

    async def _generate_hypotheses_structured(self, user_input: str) -> List[str]:
        """Ask LLM to generate alternative approaches — structured JSON output."""
        prompt = f"""You are ExArchon Brancher. Generate {self.max_branches} different approaches to solve this request.

User request: {user_input}

Respond ONLY with a JSON array of strings. Example:
["Approach 1: check system resources", "Approach 2: analyze logs", "Approach 3: verify network connectivity"]
"""
        try:
            raw = await asyncio.wait_for(
                self.acl.execute(prompt),
                timeout=self.llm_timeout,
            )
            # Try structured JSON first
            try:
                hypotheses = json.loads(raw)
                if isinstance(hypotheses, list):
                    return [str(h).strip() for h in hypotheses[:self.max_branches] if str(h).strip()]
            except json.JSONDecodeError:
                pass

            # Fallback: line-by-line parsing
            hypotheses = []
            for line in raw.split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith("-") or line.startswith("")):
                    hypotheses.append(line.lstrip("0123456789.-) \"\'").strip())
            return hypotheses[:self.max_branches]
        except asyncio.TimeoutError:
            return [user_input]  # Fallback: just the original

    async def _run_branch(self, hypothesis: str, user_input: str, session_id: str) -> HypothesisResult:
        """Execute a single hypothesis branch with semaphore-controlled concurrency."""
        start = time.time()
        async with self._semaphore:
            prompt = f"""Approach: {hypothesis}
Original request: {user_input}
Solve using available tools. Think step by step.
"""
            try:
                if self._react_engine is not None:
                    trace = await self._react_engine.run(prompt, session_id)
                    elapsed = (time.time() - start) * 1000
                    return HypothesisResult(hypothesis, trace, elapsed)

                raw = await asyncio.wait_for(
                    self.acl.execute(prompt),
                    timeout=self.llm_timeout,
                )
                trace = ReActTrace(user_input=user_input)
                if raw and len(raw.strip()) > 10:
                    trace.success = True
                    trace.final_answer = raw
                else:
                    trace.success = False
                    trace.final_answer = f"[Brancher] Empty response for hypothesis: {hypothesis}"
                trace.steps = []
                elapsed = (time.time() - start) * 1000
                return HypothesisResult(hypothesis, trace, elapsed)
            except asyncio.TimeoutError:
                elapsed = (time.time() - start) * 1000
                return HypothesisResult(hypothesis, None, elapsed)
            except Exception as e:
                elapsed = (time.time() - start) * 1000
                return HypothesisResult(hypothesis, None, elapsed)