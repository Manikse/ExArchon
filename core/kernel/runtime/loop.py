"""
ExArchon KernelRuntime v3.0 — State-Driven Kernel (Raphael Edition).

+ Sandbox Engine: dry-run skills before production
+ Background Workers: compile skills, cleanup, indexing — non-blocking
+ Notice System integration
"""
import time
import asyncio
from typing import Dict, Optional

from kernel.cortex.react_engine import ReactEngine, ReActTrace
from kernel.skills.library import SkillLibrary, Skill, ExecutionStep
from kernel.skills.brancher import SpeculativeBrancher
from kernel.state_machine import StateMachine, State, Action, TransitionError
from kernel.security.capabilities import CapabilityChecker, TERMINAL_READONLY, FS_READONLY
from kernel.sandbox import Sandbox
from kernel.workers.background import BackgroundWorker, TaskType, bg_compile_skill, bg_cleanup_memory


class KernelRuntime:
    """
    Головний runtime ExArchon v3.0 — Raphael Edition.
    """

    def __init__(self, acl, memory, drivers: Dict, skill_db_path: str = "./kernel_workspace/skills.db"):
        self.acl = acl
        self.memory = memory
        self.drivers = drivers
        self.cortex = ReactEngine(acl, drivers, memory=memory)
        self.brancher = SpeculativeBrancher(acl, drivers, memory=memory, max_branches=3)
        self.skill_library = SkillLibrary(db_path=skill_db_path)

        self.state_machine = StateMachine()
        self.cap_checker = CapabilityChecker()
        self.component_caps: Dict[str, any] = {}
        self._register_driver_caps()

        self.sandbox = Sandbox(capability_checker=self.cap_checker)

        self.background = BackgroundWorker(max_workers=2)
        self.background.start()

        self.cortex.attach_kernel_runtime(self)
        self.brancher.attach_kernel_runtime(self)

    def _register_driver_caps(self):
        if "terminal" in self.drivers:
            self.component_caps["terminal"] = TERMINAL_READONLY
        if "file_system" in self.drivers:
            self.component_caps["file_system"] = FS_READONLY

    def _check_capability(self, component_name: str, action: Action) -> bool:
        caps = self.component_caps.get(component_name)
        if caps is None:
            return False
        ok, reason = self.cap_checker.check(action, caps)
        if not ok:
            print(f"[KERNEL] Capability denied for {component_name}: {reason}")
        return ok

    async def step(self, user_input: str, session_id: str = "default") -> str:
        """State-driven entry point."""
        start_time = time.time()

        # === MUSCLE: Skill Retrieval ===
        try:
            self.state_machine.transition(State.MUSCLE)
        except TransitionError:
            pass

        skill = self.skill_library.find_skill(user_input, min_score=0.55)
        if skill:
            result = await self._execute_skill(skill, session_id)
            total_ms = (time.time() - start_time) * 1000
            success = not result.startswith("[Error]")
            self.skill_library.record_usage(skill.skill_id, success, total_ms)
            if self.memory:
                self.memory.add_interaction(session_id, user_input, result, user_importance=6, response_importance=5)
            self.state_machine.transition(State.IDLE)
            return f"[Skill: {skill.name}]\n{result}"

        # === COGNITIVE: Speculative Branching ===
        try:
            self.state_machine.transition(State.COGNITIVE)
        except TransitionError:
            pass

        trace = await self.brancher.solve(user_input, session_id)

        if trace and trace.success:
            self._schedule_skill_compilation(trace, user_input)
            if self.memory:
                self.memory.add_interaction(session_id, user_input, trace.final_answer, user_importance=7, response_importance=6)
            self.state_machine.transition(State.IDLE)
            return trace.final_answer

        # === Fallback: ReAct ===
        trace = await self.cortex.run(user_input, session_id=session_id)

        if trace.success and len(trace.steps) >= 1:
            self._schedule_skill_compilation(trace, user_input)

        self.state_machine.transition(State.IDLE)
        return trace.final_answer

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

    async def _execute_skill(self, skill: Skill, session_id: str) -> str:
        """Execute compiled skill with capability checks."""
        outputs = []
        for step in skill.execution_graph:
            if step.tool not in self.drivers:
                outputs.append(f"[Error] Unknown tool: {step.tool}")
                continue
            driver = self.drivers[step.tool]
            action = Action(op="EXEC", target=step.action_input, source_agent="muscle_memory")
            if not self._check_capability(step.tool, action):
                outputs.append(f"[Error] Capability denied for {step.tool}")
                break
            action_input = step.action_input
            if outputs and "{{prev}}" in action_input:
                action_input = action_input.replace("{{prev}}", outputs[-1][:500])
            try:
                if asyncio.iscoroutinefunction(driver.execute):
                    result = await driver.execute(action_input)
                else:
                    result = driver.execute(action_input)
                outputs.append(str(result))
            except Exception as e:
                outputs.append(f"[Error] {str(e)}")
                break
        return "\n".join(outputs) if outputs else "[Skill executed with no output]"

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

    def shutdown(self):
        """Graceful shutdown."""
        self.background.stop()

    def get_stats(self) -> Dict:
        skill_stats = self.skill_library.get_stats()
        return {
            **skill_stats,
            "cortex_max_iterations": self.cortex.max_iterations,
            "current_state": self.state_machine.state.name,
            "state_generation": self.state_machine.state_vector.generation,
            "bg_pending": self.background.get_pending_count(),
            "bg_completed": len(self.background.get_results()),
        }