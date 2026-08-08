"""
kernel/cortex/batcher.py
Adaptive Batching Engine (ABE).
Coalesces multiple tasks into single LLM calls for throughput.
"""
import asyncio
import time
from typing import List, Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum


class TaskPriority(Enum):
    REALTIME = 0   # Reflex escalations
    HIGH = 1       # User-facing
    NORMAL = 2     # Background
    LOW = 3        # Cleanup, summaries


@dataclass
class PendingTask:
    task_id: str
    prompt: str
    priority: TaskPriority
    future: asyncio.Future
    submitted_at: float
    session_id: str = "default"
    max_tokens: int = 512


class BatchingEngine:
    """
    Collects tasks during batch_window_ms, then flushes as single LLM call.
    Reduces per-request overhead 5-10x.
    """

    def __init__(
        self,
        llm_call: Callable[[str], Any],
        batch_window_ms: float = 50.0,
        max_batch_size: int = 10,
        max_wait_ms: float = 200.0,
    ):
        self.llm_call = llm_call
        self.batch_window_ms = batch_window_ms
        self.max_batch_size = max_batch_size
        self.max_wait_ms = max_wait_ms

        self._pending: List[PendingTask] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._task_counter = 0

        # Stats
        self.batches_sent = 0
        self.tasks_batched = 0
        self.tasks_solo = 0

    async def submit(self, prompt: str, priority: TaskPriority = TaskPriority.NORMAL,
                     session_id: str = "default", max_tokens: int = 512) -> str:
        """Submit task. Returns result via future."""
        self._task_counter += 1
        task_id = f"batch-{self._task_counter}"
        future = asyncio.get_event_loop().create_future()

        task = PendingTask(
            task_id=task_id,
            prompt=prompt,
            priority=priority,
            future=future,
            submitted_at=time.time(),
            session_id=session_id,
            max_tokens=max_tokens,
        )

        async with self._lock:
            self._pending.append(task)
            should_flush = len(self._pending) >= self.max_batch_size

        if should_flush:
            await self._flush()
        elif not self._flush_task or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush())

        return await future

    async def _delayed_flush(self):
        """Wait for batch window, then flush."""
        await asyncio.sleep(self.batch_window_ms / 1000.0)
        await self._flush()

    async def _flush(self):
        """Send batched request to LLM."""
        async with self._lock:
            if not self._pending:
                return
            batch = self._pending[:self.max_batch_size]
            self._pending = self._pending[self.max_batch_size:]

        if len(batch) == 1:
            # Solo task — no batching overhead
            self.tasks_solo += 1
            task = batch[0]
            try:
                result = await self._call_llm(task.prompt, task.max_tokens)
                task.future.set_result(result)
            except Exception as e:
                task.future.set_exception(e)
            return

        self.batches_sent += 1
        self.tasks_batched += len(batch)

        # Build combined prompt
        combined = self._build_batch_prompt(batch)

        try:
            raw_result = await self._call_llm(combined, max_tokens=1024)
            results = self._split_batch_response(raw_result, len(batch))

            for task, result in zip(batch, results):
                task.future.set_result(result)
        except Exception as e:
            # If batch fails, retry solo
            for task in batch:
                try:
                    result = await self._call_llm(task.prompt, task.max_tokens)
                    task.future.set_result(result)
                except Exception as e2:
                    task.future.set_exception(e2)

    async def _call_llm(self, prompt: str, max_tokens: int) -> str:
        """Wrapper around llm_call with timeout."""
        if asyncio.iscoroutinefunction(self.llm_call):
            return await asyncio.wait_for(
                self.llm_call(prompt),
                timeout=120.0,
            )
        else:
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, self.llm_call, prompt),
                timeout=120.0,
            )

    def _build_batch_prompt(self, tasks: List[PendingTask]) -> str:
        """Combine multiple prompts into single batch prompt."""
        parts = [
            "You are ExArchon, a Cognitive Operating System kernel.",
            "Solve the following independent tasks. Return results numbered exactly as shown.",
            "",
        ]
        for i, task in enumerate(tasks, 1):
            parts.append(f"--- TASK {i} ---")
            parts.append(task.prompt)
            parts.append("")
        parts.append("Return results in this exact format:")
        for i in range(1, len(tasks) + 1):
            parts.append(f"RESULT {i}: <your answer>")
        return "\n".join(parts)

    def _split_batch_response(self, raw: str, count: int) -> List[str]:
        """Parse batched response back into individual results."""
        results = []
        for i in range(1, count + 1):
            marker = f"RESULT {i}:"
            start = raw.find(marker)
            if start == -1:
                results.append(raw)  # Fallback: give full response
                continue
            start += len(marker)
            end = raw.find(f"RESULT {i+1}:", start)
            if end == -1:
                end = len(raw)
            results.append(raw[start:end].strip())

        # Pad if missing
        while len(results) < count:
            results.append("[BATCH ERROR] Missing result")
        return results[:count]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "batches_sent": self.batches_sent,
            "tasks_batched": self.tasks_batched,
            "tasks_solo": self.tasks_solo,
            "pending_now": len(self._pending),
            "avg_batch_size": round(self.tasks_batched / max(self.batches_sent, 1), 2),
        }