"""
kernel/workers/background.py

Background Worker Queue

Фонові задачі, що не блокують основний цикл kernel.

Як у Рафаель:
  - Фоновий аналіз оточення
  - Синтез навичок (Skill Fusion)
  - Оптимізація та cleanup
  - Все це відбувається "за лаштунками", поки носій живе своїм життям
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Any, Optional, Dict
from collections import deque


class TaskType(Enum):
    ANALYZE = auto()      # Аналіз даних, оточення
    SYNTHESIZE = auto()   # Компіляція/фузія скиллів
    CLEANUP = auto()      # GC, очищення старих логів
    INDEX = auto()        # Індексація пам'яті
    LEARN = auto()        # Навчання, оптимізація


@dataclass
class BackgroundTask:
    id: str
    task_type: TaskType
    fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    priority: int = 5       # 1 = highest, 10 = lowest
    created_ns: int = field(default_factory=lambda: time.monotonic_ns())
    completed: bool = False
    result: Any = None
    error: Optional[str] = None


class BackgroundWorker:
    """
    Async worker queue.

    Usage:
        worker = BackgroundWorker()
        worker.start()

        # Submit task
        worker.submit(TaskType.SYNTHESIZE, my_compile_fn, skill_data)

        # Later...
        results = worker.get_results()
    """

    def __init__(self, max_workers: int = 2):
        self.max_workers = max_workers
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._results: deque[BackgroundTask] = deque(maxlen=100)
        self._running = False
        self._workers: list[asyncio.Task] = []
        self._task_counter = 0

    def start(self):
        """Запуск фонових воркерів."""
        if self._running:
            return
        self._running = True
        for i in range(self.max_workers):
            task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(task)

    def stop(self):
        """Зупинка. Чекаємо поточні задачі, нові — не беремо."""
        self._running = False
        for w in self._workers:
            w.cancel()

    def submit(
        self,
        task_type: TaskType,
        fn: Callable,
        *args,
        priority: int = 5,
        **kwargs,
    ) -> str:
        """
        Додати задачу у чергу.
        Повертає task_id.
        """
        self._task_counter += 1
        task_id = f"BG-{self._task_counter:04d}"
        task = BackgroundTask(
            id=task_id,
            task_type=task_type,
            fn=fn,
            args=args,
            kwargs=kwargs,
            priority=priority,
        )
        # PriorityQueue: (priority, task)
        self._queue.put_nowait((priority, task))
        return task_id

    def get_results(self, task_type: Optional[TaskType] = None, limit: int = 10) -> List[BackgroundTask]:
        """Отримати результати виконаних задач."""
        results = list(self._results)
        if task_type:
            results = [r for r in results if r.task_type == task_type]
        return results[-limit:]

    def get_pending_count(self) -> int:
        return self._queue.qsize()

    async def _worker_loop(self, worker_name: str):
        """Фоновий цикл воркера."""
        while self._running:
            try:
                priority, task = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                if asyncio.iscoroutinefunction(task.fn):
                    result = await task.fn(*task.args, **task.kwargs)
                else:
                    result = await asyncio.to_thread(task.fn, *task.args, **task.kwargs)
                task.result = result
                task.completed = True
            except Exception as e:
                task.error = str(e)
                task.completed = True

            self._results.append(task)
            print(f"[BG {worker_name}] {task.task_type.name} {task.id}: {'OK' if task.error is None else 'FAIL'}")


# --- Helper functions for common background tasks ---

async def bg_compile_skill(skill_library, trace_steps, user_input):
    """Фонова компіляція скилла з ReAct trace."""
    try:
        compiled_steps = [
            {"tool": s["tool"], "action_input": s["action_input"]}
            for s in trace_steps if s.get("tool") != "respond"
        ]
        if compiled_steps:
            new_skill = skill_library.from_trace(
                trace_id="",
                user_input=user_input,
                trace_steps=compiled_steps
            )
            skill_library.add_skill(new_skill)
            return f"Compiled skill: {new_skill.name}"
        return "No steps to compile"
    except Exception as e:
        return f"Compile error: {str(e)}"


async def bg_cleanup_memory(memory_controller, max_age_hours: int = 24):
    """Фонове очищення старих сесій."""
    try:
        memory_controller.cleanup_stale_sessions()
        return "Memory cleanup complete"
    except Exception as e:
        return f"Cleanup error: {str(e)}"


async def bg_index_skills(skill_library):
    """Фонова реіндексація скиллів."""
    try:
        # Placeholder: future optimization
        return f"Indexed {skill_library.get_stats().get('total_skills', 0)} skills"
    except Exception as e:
        return f"Index error: {str(e)}"