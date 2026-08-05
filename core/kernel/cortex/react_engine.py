"""
ExArchon Cortex — ReAct Engine v2
Circuit Breaker Edition.

Зміни від v1:
1. CIRCUIT BREAKER: max_reflection_depth=3, exponential backoff.
2. Якщо max reflections досягнуто — ескалація до оператора через Notice System.
3. Валідація capability через новий CapabilityManager.
4. Таймаут на кожен LLM call.
5. Graceful handling malformed LLM responses.

Аналогія з Raphael: якщо Рафаєль не може вирішити задачу за 3 спроби,
він не зациклюється — він повідомляє Рімуру: "Потрібна твоя допомога".
"""
import re
import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

from kernel.state_machine import Action
from kernel.security.capabilities import CapabilityManager, CapOp


@dataclass
class CortexStep:
    """Один крок мислення."""
    thought: str
    action: str
    action_input: str
    observation: str = ""
    raw_response: str = ""


@dataclass
class ReActTrace:
    """Повний trace сесії — зберігається для Muscle Memory."""
    user_input: str
    steps: List[CortexStep] = field(default_factory=list)
    success: bool = False
    final_answer: str = ""
    reflection_count: int = 0
    total_llm_time_ms: float = 0.0


class MaxReflectionError(Exception):
    """Досягнуто максимальну кількість reflection спроб."""
    pass


class ReactEngine:
    """
    ReAct Engine для ExArchon v2 — з circuit breaker.
    """

    FEW_SHOT_PROMPT = r"""
You are ExArchon Cortex, the reasoning layer of a Cognitive OS.
You think step by step and use tools to solve tasks.

AVAILABLE TOOLS:
- terminal: execute OS commands (bash/PowerShell)
- file_system: READ, WRITE, APPEND, DELETE, LIST, COPY, MOVE, DIFF, PATCH
- web_search: search the internet
- spawn_agent: delegate to a sub-agent via A2A
- respond: finish the task and answer the user

RULES:
1. Always start with "Thought:" explaining your reasoning.
2. Then "Action:" with the exact tool name.
3. Then "Action Input:" with the exact input.
4. If the task is complete, use Action: respond
5. Do NOT use markdown code blocks. Use plain text only.

EXAMPLE 1:
User: Check disk space.
Thought: I need to check available disk space using df -h.
Action: terminal
Action Input: df -h

EXAMPLE 2:
User: Read config.py.
Thought: The user wants to read a file. I will use the file_system driver.
Action: file_system
Action Input: READ config.py

EXAMPLE 3:
User: What is the weather?
Thought: I need to search the web for current weather information.
Action: web_search
Action Input: current weather

Now solve the user's request.
""".strip()

    def __init__(
        self,
        acl,
        drivers: Dict[str, Callable],
        memory=None,
        max_iterations: int = 5,
        max_reflection_depth: int = 3,
        reflection_backoff_base: float = 1.0,
        llm_timeout: float = 30.0,
    ):
        self.acl = acl
        self.drivers = drivers
        self.memory = memory
        self.max_iterations = max_iterations
        self.max_reflection_depth = max_reflection_depth
        self.reflection_backoff_base = reflection_backoff_base
        self.llm_timeout = llm_timeout
        self.tool_names = set(drivers.keys()) | {"respond"}
        self.kernel_runtime = None
        self.capability_manager: Optional[CapabilityManager] = None

    def attach_kernel_runtime(self, kernel_runtime):
        """Attach to parent KernelRuntime for capability validation."""
        self.kernel_runtime = kernel_runtime

    def attach_capability_manager(self, cap_manager: CapabilityManager):
        """Attach CapabilityManager для перевірки прав."""
        self.capability_manager = cap_manager

    async def run(self, user_input: str, session_id: str = "default") -> ReActTrace:
        """
        Головний entry point. Запускає ReAct-цикл з circuit breaker.
        """
        trace = ReActTrace(user_input=user_input)
        history = []
        reflection_count = 0
        total_llm_time = 0.0

        for i in range(self.max_iterations):
            # === LLM Call з таймаутом ===
            prompt = self._build_prompt(user_input, history)
            llm_start = time.time()
            try:
                raw = await asyncio.wait_for(
                    self.acl.execute(prompt, system_prompt=self.FEW_SHOT_PROMPT),
                    timeout=self.llm_timeout,
                )
            except asyncio.TimeoutError:
                trace.success = False
                trace.final_answer = (
                    f"[Cortex] LLM timeout after {self.llm_timeout}s. "
                    f"Task abandoned. Please retry or check LLM provider."
                )
                trace.reflection_count = reflection_count
                trace.total_llm_time_ms = total_llm_time
                self._store_memory(session_id, trace)
                return trace
            except Exception as e:
                trace.success = False
                trace.final_answer = f"[Cortex] LLM communication error: {str(e)}"
                trace.reflection_count = reflection_count
                self._store_memory(session_id, trace)
                return trace

            total_llm_time += (time.time() - llm_start) * 1000

            step = self._parse_response(raw)
            step.raw_response = raw

            if not step.action:
                # Malformed response — це помилка, яка потребує reflection
                step.action = "respond"
                step.action_input = (
                    f"[Cortex Error] Could not parse action from LLM response. "
                    f"Raw: {raw[:500]}"
                )
                trace.success = False
                trace.steps.append(step)
                trace.reflection_count = reflection_count
                trace.total_llm_time_ms = total_llm_time
                self._store_memory(session_id, trace)
                return trace

            if step.action == "respond":
                trace.success = True
                trace.final_answer = step.action_input
                trace.steps.append(step)
                trace.reflection_count = reflection_count
                trace.total_llm_time_ms = total_llm_time
                self._store_memory(session_id, trace)
                return trace

            # === Виконання дії ===
            observation = await self._execute_action(step)
            step.observation = observation
            trace.steps.append(step)

            # === Перевірка на помилку виконання ===
            if observation.startswith("[Error]") or observation.startswith("[Execution Error]") or observation.startswith("[CAPABILITY DENIED]"):
                reflection_count += 1
                if reflection_count >= self.max_reflection_depth:
                    trace.success = False
                    trace.final_answer = (
                        f"[Cortex] Max reflection depth ({self.max_reflection_depth}) reached. "
                        f"Last error: {observation[:200]}. "
                        f"Manual intervention required."
                    )
                    trace.reflection_count = reflection_count
                    trace.total_llm_time_ms = total_llm_time
                    self._store_memory(session_id, trace)
                    # Ескалація — kernel runtime може відправити notice
                    if self.kernel_runtime and hasattr(self.kernel_runtime, 'notice_system'):
                        try:
                            self.kernel_runtime.notice_system.post(
                                title="Cortex Reflection Limit Reached",
                                message=f"Task '{user_input[:50]}' failed after {reflection_count} attempts. Last error: {observation[:200]}",
                                severity="CRITICAL",
                                source="cortex",
                            )
                        except Exception:
                            pass
                    return trace

                # Exponential backoff перед наступною спробою
                backoff = self.reflection_backoff_base * (2 ** (reflection_count - 1))
                history.append({
                    "thought": step.thought,
                    "action": step.action,
                    "action_input": step.action_input,
                    "observation": f"[REFLECTION #{reflection_count}] {observation}\n[BACKOFF] Waiting {backoff}s before retry...",
                })
                await asyncio.sleep(backoff)
                continue

            history.append({
                "thought": step.thought,
                "action": step.action,
                "action_input": step.action_input,
                "observation": step.observation,
            })

        else:
            # Max iterations reached
            trace.success = False
            trace.final_answer = (
                f"[Cortex] Max iterations ({self.max_iterations}) reached. "
                f"Partial result after {len(trace.steps)} steps. "
                f"Reflections: {reflection_count}."
            )
            trace.reflection_count = reflection_count
            trace.total_llm_time_ms = total_llm_time
            self._store_memory(session_id, trace)
            return trace

    def _store_memory(self, session_id: str, trace: ReActTrace):
        """Зберігає trace у UNMS, якщо доступний."""
        if self.memory:
            try:
                # Синхронний виклик для уникнення nested event loop issues
                if hasattr(self.memory, 'add_interaction'):
                    import asyncio
                    if asyncio.iscoroutinefunction(self.memory.add_interaction):
                        # Запускаємо через create_task, якщо є event loop
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(self.memory.add_interaction(
                                session_id,
                                trace.user_input,
                                trace.final_answer,
                                user_importance=7 if trace.success else 4,
                            ))
                        except RuntimeError:
                            pass  # No event loop running
                    else:
                        self.memory.add_interaction(
                            session_id,
                            trace.user_input,
                            trace.final_answer,
                            user_importance=7 if trace.success else 4,
                        )
            except Exception as e:
                print(f"[Cortex] Memory storage error: {e}")

    def _build_prompt(self, user_input: str, history: List[Dict]) -> str:
        lines = [f"User: {user_input}", ""]
        for h in history:
            lines.append(f"Thought: {h['thought']}")
            lines.append(f"Action: {h['action']}")
            lines.append(f"Action Input: {h['action_input']}")
            lines.append(f"Observation: {h['observation']}")
            lines.append("")
        lines.append("What is your next Thought, Action, and Action Input?")
        return "\n".join(lines)

    def _parse_response(self, raw: str) -> CortexStep:
        thought = ""
        action = ""
        action_input = ""

        m_thought = re.search(r"Thought:\s*(.+?)(?=\nAction:|$)", raw, re.DOTALL | re.IGNORECASE)
        if m_thought:
            thought = m_thought.group(1).strip()

        m_action = re.search(r"Action:\s*(\w+)", raw, re.IGNORECASE)
        if m_action:
            action = m_action.group(1).strip().lower()

        m_input = re.search(r"Action Input:\s*(.+?)(?=\nThought:|\nAction:|$)", raw, re.DOTALL | re.IGNORECASE)
        if m_input:
            action_input = m_input.group(1).strip()

        if action not in self.tool_names:
            action = ""
            action_input = raw[:1000]

        return CortexStep(
            thought=thought or "[No thought parsed]",
            action=action,
            action_input=action_input,
        )

    async def _execute_action(self, step: CortexStep) -> str:
        if step.action not in self.drivers:
            return f"[Error] Unknown tool: {step.action}. Available: {sorted(self.tool_names)}"

        # === Capability validation через новий CapabilityManager ===
        if self.capability_manager:
            ok, reason = self.capability_manager.validate("cortex", CapOp.EXEC, step.action)
            if not ok:
                return f"[CAPABILITY DENIED] {reason}"

        # Legacy fallback через kernel_runtime
        if self.kernel_runtime:
            action = Action(op="EXEC", target=step.action_input, source_agent="cortex")
            if hasattr(self.kernel_runtime, '_check_capability'):
                if not self.kernel_runtime._check_capability(step.action, action):
                    return "[CAPABILITY DENIED] Legacy capability check failed"

        driver = self.drivers[step.action]

        try:
            if asyncio.iscoroutinefunction(driver):
                result = await driver(step.action_input)
            else:
                result = driver(step.action_input)
            return str(result)[:2000]
        except Exception as e:
            return f"[Execution Error] {str(e)}"