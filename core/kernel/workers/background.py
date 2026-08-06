"""
kernel/workers/background.py
Background Worker v2 — ProcessPool for CPU-bound tasks.
"""
import asyncio
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Any, List, Dict
from enum import Enum, auto


class TaskType(Enum):
    SYNTHESIZE = auto()
    INDEX = auto()
    CLEANUP = auto()
    COMPILE = auto()


@dataclass
class Task:
    id: str
    task_type: TaskType
    fn: Callable
    args: tuple
    kwargs: dict
    priority: int
    submitted_at: float


@dataclass
class TaskResult:
    id: str
    task_type: TaskType
    result: Any = None
    error: str = None
    duration_ms: float = 0.0


class BackgroundWorker:
    """
    Background processing with ProcessPool for CPU-bound work.
    """

    def __init__(self, max_workers: int = 2):
        self.max_workers = max_workers
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._results: List[TaskResult] = []
        self._task_counter = 0
        self._running = False
        self._worker_task = None
        # ProcessPool for CPU-bound (skill compilation, indexing)
        self._cpu_pool = ProcessPoolExecutor(max_workers=max_workers)
        # ThreadPool for I/O-bound (fallback)
        self._loop = asyncio.get_event_loop()

    def start(self):
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._process_loop())

    def stop(self):
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
        self._cpu_pool.shutdown(wait=True)

    def submit(self, task_type: TaskType, fn: Callable, *args, priority: int = 5, **kwargs) -> str:
        self._task_counter += 1
        task_id = f"bg-{self._task_counter}"
        task = Task(
            id=task_id,
            task_type=task_type,
            fn=fn,
            args=args,
            kwargs=kwargs,
            priority=priority,
            submitted_at=time.time(),
        )
        # Lower priority number = higher priority
        asyncio.create_task(self._queue.put((priority, task)))
        return task_id

    async def _process_loop(self):
        while self._running:
            try:
                priority, task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            start = time.time()
            try:
                # Determine if CPU-bound
                if task.task_type in (TaskType.SYNTHESIZE, TaskType.COMPILE, TaskType.INDEX):
                    # Run in ProcessPool
                    result = await self._loop.run_in_executor(
                        self._cpu_pool,
                        self._run_in_process,
                        task.fn,
                        task.args,
                        task.kwargs,
                    )
                else:
                    # I/O-bound — thread or async
                    if asyncio.iscoroutinefunction(task.fn):
                        result = await task.fn(*task.args, **task.kwargs)
                    else:
                        result = await asyncio.to_thread(task.fn, *task.args, **task.kwargs)

                duration = (time.time() - start) * 1000
                self._results.append(TaskResult(
                    id=task.id,
                    task_type=task.task_type,
                    result=result,
                    duration_ms=duration,
                ))
                print(f"[Background] ✓ {task.id} {task.task_type.name} ({duration:.0f}ms)")

            except Exception as e:
                duration = (time.time() - start) * 1000
                self._results.append(TaskResult(
                    id=task.id,
                    task_type=task.task_type,
                    error=str(e),
                    duration_ms=duration,
                ))
                print(f"[Background] ✗ {task.id} {task.task_type.name}: {e}")

    @staticmethod
    def _run_in_process(fn, args, kwargs):
        """Static method for ProcessPool pickling."""
        return fn(*args, **kwargs)

    def get_pending_count(self) -> int:
        return self._queue.qsize()

    def get_results(self, limit: int = 10) -> List[TaskResult]:
        return self._results[-limit:]


# Convenience functions for background tasks
def bg_compile_skill(skill_library, trace_steps, user_input):
    """Compile ReAct trace into skill (CPU-bound)."""
    from kernel.skills.library import SkillLibrary
    skill = SkillLibrary.from_trace("bg", user_input, trace_steps)
    skill_library.add_skill(skill)
    return f"Compiled skill {skill.skill_id}"

def bg_cleanup_memory(memory_controller, threshold: int = 3):
    """Cleanup low-importance memory (I/O-bound)."""
    # This would be async in real usage
    return "Memory cleanup scheduled"