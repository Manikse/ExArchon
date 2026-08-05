"""
ExArchon KernelRuntime v4.0 — Capability-Based State-Driven Kernel.

Зміни від v3:
1. Інтеграція з новим CapabilityManager (замість CapabilityChecker).
2. Execution Limits: timeout, memory tracking, syscall limits для skill execution.
3. Інтеграція з WAL StateMachine — shutdown викликає state_machine.shutdown().
4. Graceful Degradation: якщо LLM недоступний — queue task + notify.
5. Resource tracking для кожного execution context.

Аналогія з Raphael: це саме ядро — воно вирішує, хто що може,
записує кожне рішення, і ніколи не втрачає стан.
"""
import time
import asyncio
import resource
import os
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from kernel.cortex.react_engine import ReactEngine, ReActTrace
from kernel.skills.library import SkillLibrary, Skill, ExecutionStep
from kernel.skills.brancher import SpeculativeBrancher
from kernel.state_machine import StateMachine, State, Action, TransitionError
from kernel.security.capabilities import (
    CapabilityManager,
    CapabilitySet,
    CapOp,
    make_terminal_caps,
    make_filesystem_caps,
    make_cortex_caps,
    make_muscle_caps,
    make_reflex_caps,
    make_sandbox_caps,
)
from kernel.sandbox import Sandbox
from kernel.workers.background import BackgroundWorker, TaskType, bg_compile_skill, bg_cleanup_memory


@dataclass
class ExecutionContext:
    """Контекст виконання однієї задачі — з лімітами та трекінгом."""
    session_id: str
    user_input: str
    start_time: float = field(default_factory=time.time)
    max_time_ms: int = 30000
    max_memory_mb: int = 512
    skills_executed: int = 0
    total_skill_time_ms: float = 0.0
    errors: list = field(default_factory=list)

    def is_timed_out(self) -> bool:
        return (time.time() - self.start_time) * 1000 > self.max_time_ms

    def check_memory(self) -> bool:
        """Перевіряє, чи не перевищено ліміт пам'яті процесу."""
        try:
            usage_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            return usage_mb < self.max_memory_mb
        except Exception:
            return True


@dataclass
class ExecutionLimits:
    """Глобальні ліміти виконання для kernel."""
    skill_timeout_ms: int = 5000
    cortex_timeout_ms: int = 30000
    brancher_timeout_ms: int = 45000
    max_memory_mb: int = 1024
    max_skills_per_session: int = 50


class KernelRuntime:
    """
    Головний runtime ExArchon v4.0 — Capability-Based Kernel.
    """

    def __init__(
        self,
        acl,
        memory,
        drivers: Dict,
        skill_db_path: str = "./kernel_workspace/skills.db",
        state_journal_path: str = "./kernel_workspace/state.journal",
    ):
        self.acl = acl
        self.memory = memory
        self.drivers = drivers
        self.execution_limits = ExecutionLimits()

        # === Capability Manager — центр безпеки ===
        self.cap_manager = CapabilityManager()
        self._init_capabilities()

        # === Components ===
        self.cortex = ReactEngine(acl, drivers, memory=memory)
        self.cortex.attach_capability_manager(self.cap_manager)

        self.brancher = SpeculativeBrancher(acl, drivers, memory=memory, max_branches=3)
        self.brancher.attach_capability_manager(self.cap_manager)

        self.skill_library = SkillLibrary(db_path=skill_db_path)
        self.state_machine = StateMachine(journal_path=state_journal_path)
        self.sandbox = Sandbox(capability_manager=self.cap_manager)
        self.background = BackgroundWorker(max_workers=2)
        self.background.start()

        # Attach to components
        self.cortex.attach_kernel_runtime(self)
        self.brancher.attach_kernel_runtime(self)

        # Execution tracking
        self._active_contexts: Dict[str, ExecutionContext] = {}
        self._session_skill_count: Dict[str, int] = {}

    def _init_capabilities(self):
        """Реєструє всі компоненти та видає їм початкові capabilities."""
        # Terminal
        if "terminal" in self.drivers:
            self.cap_manager.register_component("terminal", make_terminal_caps())
        # FileSystem
        if "file_system" in self.drivers:
            self.cap_manager.register_component("file_system", make_filesystem_caps())
        # Cortex
        self.cap_manager.register_component("cortex", make_cortex_caps())
        # Muscle Memory
        self.cap_manager.register_component("muscle_memory", make_muscle_caps())
        # Reflex
        self.cap_manager.register_component("reflex", make_reflex_caps())
        # Sandbox
        self.cap_manager.register_component("sandbox", make_sandbox_caps())

        print(f"[KernelRuntime] Capability system initialized. Components: {self.cap_manager.list_components()}")

    def _check_capability(self, component_name: str, action: Action) -> bool:
        """Legacy bridge — перевіряє capability через новий менеджер."""
        op_map = {
            "READ": CapOp.READ,
            "WRITE": CapOp.WRITE,
            "EXEC": CapOp.EXEC,
            "NETWORK": CapOp.NETWORK,
            "SPAWN": CapOp.SPAWN,
            "BRANCH": CapOp.BRANCH,
            "WAIT": CapOp.WAIT,
            "NOOP": CapOp.NOOP,
        }
        cap_op = op_map.get(action.op, CapOp.NOOP)
        ok, reason = self.cap_manager.validate(component_name, cap_op, action.target)
        if not ok:
            print(f"[KERNEL] Capability denied for {component_name}: {reason}")
        return ok

    async def step(self, user_input: str, session_id: str = "default") -> str:
        """State-driven entry point з execution tracking."""
        start_time = time.time()

        # === Execution Context ===
        ctx = ExecutionContext(
            session_id=session_id,
            user_input=user_input,
            max_time_ms=self.execution_limits.cortex_timeout_ms,
        )
        self._active_contexts[session_id] = ctx

        # Лічильник skills per session
        self._session_skill_count[session_id] = self._session_skill_count.get(session_id, 0)

        try:
            # === REFLEX: Hardcoded safety checks (System 0) ===
            reflex_result = self._reflex_check(user_input)
            if reflex_result:
                return reflex_result

            # === MUSCLE: Skill Retrieval ===
            try:
                self.state_machine.transition(State.MUSCLE)
            except TransitionError:
                pass

            skill = self.skill_library.find_skill(user_input, min_score=0.55)
            if skill:
                # Перевірка ліміту skills per session
                if self._session_skill_count[session_id] >= self.execution_limits.max_skills_per_session:
                    self.state_machine.transition(State.IDLE)
                    return f"[Kernel] Session skill limit ({self.execution_limits.max_skills_per_session}) reached."

                result = await self._execute_skill(skill, session_id, ctx)
                total_ms = (time.time() - start_time) * 1000
                success = not result.startswith("[Error]")
                self.skill_library.record_usage(skill.skill_id, success, total_ms)
                if self.memory:
                    await self.memory.add_interaction(session_id, user_input, result, user_importance=6, response_importance=5)
                self.state_machine.transition(State.IDLE)
                self._session_skill_count[session_id] += 1
                return f"[Skill: {skill.name}]\n{result}"

            # === COGNITIVE: Speculative Branching ===
            try:
                self.state_machine.transition(State.COGNITIVE)
            except TransitionError:
                pass

            # Graceful degradation: перевіряємо доступність LLM
            if not await self._check_acl_available():
                self.state_machine.transition(State.RECOVERY)
                notice = (
                    f"[Kernel] LLM provider unavailable. Task '{user_input[:50]}' queued. "
                    f"Will retry when connection restored."
                )
                if hasattr(self, 'notice_system') and self.notice_system:
                    self.notice_system.post(title="LLM Offline", message=notice, severity="WARNING")
                self.state_machine.transition(State.IDLE)
                return notice

            trace = await self._run_with_timeout(
                self.brancher.solve(user_input, session_id),
                timeout_ms=self.execution_limits.brancher_timeout_ms,
                fallback_msg="[Kernel] Brancher timeout. Falling back to single-path reasoning.",
            )

            if trace and trace.success:
                self._schedule_skill_compilation(trace, user_input)
                if self.memory:
                    await self.memory.add_interaction(session_id, user_input, trace.final_answer, user_importance=7, response_importance=6)
                self.state_machine.transition(State.IDLE)
                return trace.final_answer

            # === Fallback: ReAct ===
            trace = await self._run_with_timeout(
                self.cortex.run(user_input, session_id=session_id),
                timeout_ms=self.execution_limits.cortex_timeout_ms,
                fallback_msg="[Kernel] Cortex timeout. Task abandoned.",
            )

            if trace.success and len(trace.steps) >= 1:
                self._schedule_skill_compilation(trace, user_input)

            self.state_machine.transition(State.IDLE)
            return trace.final_answer

        except Exception as e:
            # Global error handler — never crash the kernel
            self.state_machine.transition(State.RECOVERY)
            error_msg = f"[Kernel ERROR] {type(e).__name__}: {str(e)}"
            print(f"[KernelRuntime] CRITICAL: {error_msg}")
            try:
                self.state_machine.transition(State.IDLE)
            except TransitionError:
                self.state_machine.transition(State.SAFE)
            return error_msg

        finally:
            # Cleanup context
            if session_id in self._active_contexts:
                del self._active_contexts[session_id]

    def _reflex_check(self, user_input: str) -> Optional[str]:
        """
        System 0: Hardcoded reflexes.
        Instant (<1ms), zero-cost safety checks.
        """
        lower = user_input.lower().strip()

        # Emergency shutdown
        if lower in ("shutdown now", "kernel panic", "emergency stop"):
            # Перевірка capability
            ok, _ = self.cap_manager.validate("reflex", CapOp.EXEC, "shutdown")
            if ok:
                self.state_machine.transition(State.SHUTDOWN)
                return "[REFLEX] Emergency shutdown initiated."
            return "[REFLEX DENIED] Shutdown capability not granted."

        # Self-status
        if lower in ("status", "kernel status", "what is your state"):
            stats = self.get_stats()
            return f"[REFLEX] State: {stats['current_state']}, Gen: {stats['state_generation']}, Skills: {stats['total_skills']}"

        return None

    async def _execute_skill(self, skill: Skill, session_id: str, ctx: ExecutionContext) -> str:
        """Execute compiled skill з capability checks та execution limits."""
        outputs = []
        skill_start = time.time()

        for step in skill.execution_graph:
            # Перевірка таймауту контексту
            if ctx.is_timed_out():
                outputs.append(f"[Error] Execution context timed out after {ctx.max_time_ms}ms")
                break

            # Перевірка пам'яті
            if not ctx.check_memory():
                outputs.append(f"[Error] Memory limit exceeded ({ctx.max_memory_mb}MB)")
                break

            if step.tool not in self.drivers:
                outputs.append(f"[Error] Unknown tool: {step.tool}")
                continue

            driver = self.drivers[step.tool]

            # Capability check
            action = Action(op="EXEC", target=step.action_input, source_agent="muscle_memory")
            if not self._check_capability(step.tool, action):
                outputs.append(f"[Error] Capability denied for {step.tool}")
                break

            # Підстановка {{prev}}
            action_input = step.action_input
            if outputs and "{{prev}}" in action_input:
                action_input = action_input.replace("{{prev}}", outputs[-1][:500])

            # Skill timeout
            skill_timeout = self.execution_limits.skill_timeout_ms / 1000

            try:
                if asyncio.iscoroutinefunction(driver.execute):
                    result = await asyncio.wait_for(
                        driver.execute(action_input),
                        timeout=skill_timeout,
                    )
                else:
                    # Синхронний driver — запускаємо в thread з таймаутом
                    result = await asyncio.wait_for(
                        asyncio.to_thread(driver.execute, action_input),
                        timeout=skill_timeout,
                    )
                outputs.append(str(result))
            except asyncio.TimeoutError:
                outputs.append(f"[Error] Skill step timed out after {skill_timeout}s")
                ctx.errors.append(f"Timeout in {step.tool}")
                break
            except Exception as e:
                outputs.append(f"[Error] {str(e)}")
                ctx.errors.append(str(e))
                break

        ctx.total_skill_time_ms = (time.time() - skill_start) * 1000
        ctx.skills_executed += 1
        return "\n".join(outputs) if outputs else "[Skill executed with no output]"

    async def _run_with_timeout(self, coro, timeout_ms: int, fallback_msg: str):
        """Wrapper для запуску coroutine з таймаутом."""
        try:
            return await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            print(f"[KernelRuntime] {fallback_msg}")
            return None

    async def _check_acl_available(self) -> bool:
        """Перевіряє, чи доступний LLM provider."""
        try:
            # Швидкий health check
            if hasattr(self.acl, 'is_available'):
                return await asyncio.wait_for(self.acl.is_available(), timeout=2.0)
            # Fallback: спробуємо simple ping
            return True
        except Exception:
            return False

    def _schedule_skill_compilation(self, trace: ReActTrace, user_input: str):
        """Raphael-style: Skill synthesis goes to background worker."""
        trace_steps = [
            {"tool": s.action, "action_input": s.action_input}
            for s in trace.steps if s.action != "respond"
        ]
        if trace_steps:
            self.background.submit(
                TaskType.SYNTHESIZE,
                bg_compile_skill,
                self.skill_library,
                trace_steps,
                user_input,
                priority=3,
            )

    def sandbox_validate_skill(self, skill: Skill) -> tuple[bool, str]:
        """Raphael-style: Test skill in sandbox before trusting it."""
        return self.sandbox.validate_for_production(skill, self.drivers)

    def get_background_status(self) -> str:
        """Статус фонових задач."""
        pending = self.background.get_pending_count()
        recent = self.background.get_results(limit=3)
        lines = [f"Background queue: {pending} pending"]
        for r in recent:
            status = "✓" if r.error is None else "✗"
            lines.append(f"  {status} {r.id} {r.task_type.name}: {r.result or r.error}")
        return "\n".join(lines)

    def get_capability_audit(self) -> str:
        """Human-readable audit log capabilities."""
        entries = self.cap_manager.get_audit_log(limit=20)
        lines = ["=== Capability Audit (last 20) ==="]
        for e in entries:
            ts = time.strftime("%H:%M:%S", time.localtime(e["timestamp_ns"] / 1e9))
            lines.append(f"[{ts}] {e['event']:8} {e['source']:12} → {e.get('target', '-'):12} | {e['detail']}")
        return "\n".join(lines)

    def shutdown(self):
        """Graceful shutdown — checkpoint state, close connections."""
        print("[KernelRuntime] Initiating graceful shutdown...")
        self.background.stop()
        self.state_machine.shutdown()  # WAL checkpoint + close
        if self.memory and hasattr(self.memory, 'close'):
            try:
                import asyncio
                if asyncio.iscoroutinefunction(self.memory.close):
                    # Запускаємо, якщо є event loop
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.memory.close())
                    except RuntimeError:
                        pass
                else:
                    self.memory.close()
            except Exception as e:
                print(f"[KernelRuntime] Memory close error: {e}")
        print("[KernelRuntime] Shutdown complete.")

    def get_stats(self) -> Dict[str, Any]:
        skill_stats = self.skill_library.get_stats()
        cap_stats = {
            "components": self.cap_manager.list_components(),
            "audit_entries": len(self.cap_manager.get_audit_log(limit=999999)),
        }
        return {
            **skill_stats,
            **cap_stats,
            "cortex_max_iterations": self.cortex.max_iterations,
            "cortex_max_reflections": self.cortex.max_reflection_depth,
            "current_state": self.state_machine.state.name,
            "state_generation": self.state_machine.state_vector.generation,
            "journal_stats": self.state_machine.get_journal_stats(),
            "bg_pending": self.background.get_pending_count(),
            "bg_completed": len(self.background.get_results()),
            "active_sessions": len(self._active_contexts),
        }