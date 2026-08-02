"""
ExArchon KernelRuntime v3.0 — State-Driven Kernel.
Reflex -> Skill Retrieval -> Speculative Branching -> Skill Compilation.
"""
import time
import asyncio
from typing import Dict, Optional

from kernel.cortex.react_engine import ReactEngine, ReActTrace
from kernel.skills.library import SkillLibrary, Skill, ExecutionStep
from kernel.skills.brancher import SpeculativeBrancher
from kernel.state_machine import StateMachine, State, Action, TransitionError
from kernel.security.capabilities import CapabilityChecker, TERMINAL_READONLY, FS_READONLY


class KernelRuntime:
    """
    Головний runtime ExArchon v3.0.
    State-Driven: кожен крок — це state transition.
    """

    def __init__(self, acl, memory, drivers: Dict, skill_db_path: str = "./kernel_workspace/skills.db"):
        self.acl = acl
        self.memory = memory
        self.drivers = drivers
        self.cortex = ReactEngine(acl, drivers, memory=memory)
        self.brancher = SpeculativeBrancher(acl, drivers, memory=memory, max_branches=3)
        self.skill_library = SkillLibrary(db_path=skill_db_path)
        
        # --- NEW: State Machine ---
        self.state_machine = StateMachine()
        
        # --- NEW: Capability System ---
        self.cap_checker = CapabilityChecker()
        self.component_caps: Dict[str, any] = {}
        
        # Реєструємо drivers з capabilities
        self._register_driver_caps()

    def _register_driver_caps(self):
        """Реєстрація capabilities для кожного driver."""
        if "terminal" in self.drivers:
            self.component_caps["terminal"] = TERMINAL_READONLY
        if "file_system" in self.drivers:
            self.component_caps["file_system"] = FS_READONLY
        # web_search — опціонально, якщо є

    def _check_capability(self, component_name: str, action: Action) -> bool:
        """Kernel перевіряє, чи компонент має право виконати Action."""
        caps = self.component_caps.get(component_name)
        if caps is None:
            print(f"[KERNEL] Unknown component: {component_name}")
            return False
        ok, reason = self.cap_checker.check(action, caps)
        if not ok:
            print(f"[KERNEL] Capability denied for {component_name}: {reason}")
        return ok

    async def step(self, user_input: str, session_id: str = "default") -> str:
        """
        State-driven entry point.
        Кожен рівень — це state transition.
        """
        start_time = time.time()

        # === РІВЕНЬ 2: Skill Retrieval (MUSCLE state) ===
        try:
            self.state_machine.transition(State.MUSCLE)
        except TransitionError:
            pass  # Already in MUSCLE or can't transition
        
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

        # === РІВЕНЬ 3: Speculative Branching (COGNITIVE state) ===
        try:
            self.state_machine.transition(State.COGNITIVE)
        except TransitionError:
            pass
        
        trace = await self.brancher.solve(user_input, session_id)

        if trace and trace.success:
            # Muscle Memory: компілюємо успішний trace
            try:
                compiled_steps = [
                    {"tool": s.action, "action_input": s.action_input}
                    for s in trace.steps if s.action != "respond"
                ]
                if compiled_steps:
                    new_skill = SkillLibrary.from_trace(
                        trace_id="",
                        user_input=user_input,
                        trace_steps=compiled_steps
                    )
                    self.skill_library.add_skill(new_skill)
            except Exception:
                pass

            if self.memory:
                self.memory.add_interaction(session_id, user_input, trace.final_answer, user_importance=7, response_importance=6)
            
            self.state_machine.transition(State.IDLE)
            return trace.final_answer

        # === Fallback: простий ReAct ===
        trace = await self.cortex.run(user_input, session_id=session_id)

        if trace.success and len(trace.steps) >= 1:
            try:
                compiled_steps = [
                    {"tool": s.action, "action_input": s.action_input}
                    for s in trace.steps if s.action != "respond"
                ]
                if compiled_steps:
                    new_skill = SkillLibrary.from_trace(
                        trace_id="",
                        user_input=user_input,
                        trace_steps=compiled_steps
                    )
                    self.skill_library.add_skill(new_skill)
            except Exception:
                pass

        self.state_machine.transition(State.IDLE)
        return trace.final_answer

    async def _execute_skill(self, skill: Skill, session_id: str) -> str:
        """Виконує скомпільований Execution Graph через kernel (з capability checks)."""
        outputs = []

        for step in skill.execution_graph:
            if step.tool not in self.drivers:
                outputs.append(f"[Error] Unknown tool: {step.tool}")
                continue

            driver = self.drivers[step.tool]
            
            # --- NEW: Capability Check ---
            action = Action(op="EXEC", target=step.action_input, source_agent="muscle_memory")
            if not self._check_capability(step.tool, action):
                outputs.append(f"[Error] Capability denied for {step.tool}")
                break

            # Заміна змінних
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

    def get_stats(self) -> Dict:
        skill_stats = self.skill_library.get_stats()
        return {
            **skill_stats,
            "cortex_max_iterations": self.cortex.max_iterations,
            "current_state": self.state_machine.state.name,
            "state_generation": self.state_machine.state_vector.generation,
        }