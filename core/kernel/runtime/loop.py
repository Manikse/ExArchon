"""
ExArchon KernelRuntime v2.1 — Muscle Memory + Speculative Branching.
Reflex -> Skill Retrieval -> Speculative Branching -> Skill Compilation.
"""
import time
import asyncio
from typing import Dict, Optional

from kernel.cortex.react_engine import ReactEngine, ReActTrace
from kernel.skills.library import SkillLibrary, Skill, ExecutionStep
from kernel.skills.brancher import SpeculativeBrancher


class KernelRuntime:
    """
    Головний runtime ExArchon v2.1.
    3 рівні обробки:
    1. Reflex (0 мс) — хардкод відповіді (в main.py)
    2. Skill Retrieval (50 мс) — Muscle Memory
    3. Speculative Branching (3-10 сек) — 3 паралельні гілки для нового
    """

    def __init__(self, acl, memory, drivers: Dict, skill_db_path: str = "./kernel_workspace/skills.db"):
        self.acl = acl
        self.memory = memory
        self.drivers = drivers
        self.cortex = ReactEngine(acl, drivers, memory=memory)
        self.brancher = SpeculativeBrancher(acl, drivers, memory=memory, max_branches=3)
        self.skill_library = SkillLibrary(db_path=skill_db_path)

    async def step(self, user_input: str, session_id: str = "default") -> str:
        """
        Головний entry point. Один запит користувача -> одна відповідь.
        """
        start_time = time.time()

        # === РІВЕНЬ 2: Skill Retrieval ===
        skill = self.skill_library.find_skill(user_input, min_score=0.55)
        if skill:
            elapsed_ms = (time.time() - start_time) * 1000
            result = await self._execute_skill(skill, session_id)
            total_ms = (time.time() - start_time) * 1000

            # Оновлюємо статистику
            success = not result.startswith("[Error]")
            self.skill_library.record_usage(skill.skill_id, success, total_ms)

            # Зберігаємо в UNMS
            if self.memory:
                self.memory.add_interaction(
                    session_id, user_input, result,
                    user_importance=6, response_importance=5
                )

            return f"[Skill: {skill.name}]\n{result}"

        # === РІВЕНЬ 3: Speculative Branching (для нових задач) ===
        trace = await self.brancher.solve(user_input, session_id)

        if trace and trace.success:
            # === Muscle Memory: компілюємо успішний trace ===
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

            # Зберігаємо в UNMS
            if self.memory:
                self.memory.add_interaction(
                    session_id, user_input, trace.final_answer,
                    user_importance=7, response_importance=6
                )

            return trace.final_answer

        # === Fallback: простий ReAct (якщо Brancher не спрацював) ===
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

        return trace.final_answer

    async def _execute_skill(self, skill: Skill, session_id: str) -> str:
        """Виконує скомпільований Execution Graph навички."""
        outputs = []

        for step in skill.execution_graph:
            if step.tool not in self.drivers:
                outputs.append(f"[Error] Unknown tool: {step.tool}")
                continue

            driver = self.drivers[step.tool]

            # Заміна змінних (проста: якщо action_input містить {{prev}}, підставляємо)
            action_input = step.action_input
            if outputs and "{{prev}}" in action_input:
                action_input = action_input.replace("{{prev}}", outputs[-1][:500])

            try:
                if asyncio.iscoroutinefunction(driver):
                    result = await driver(action_input)
                else:
                    result = driver(action_input)
                outputs.append(str(result))
            except Exception as e:
                outputs.append(f"[Error] {str(e)}")
                break

        return "\n".join(outputs) if outputs else "[Skill executed with no output]"

    def get_stats(self) -> Dict:
        """Статистика runtime."""
        skill_stats = self.skill_library.get_stats()
        return {
            **skill_stats,
            "cortex_max_iterations": self.cortex.max_iterations,
        }