"""
ExArchon Cortex — ReAct Engine
Thought → Action → Observation loop.
"""
import re
import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

from kernel.state_machine import Action


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


class ReactEngine:
    """
    ReAct Engine для ExArchon.
    Не генерує JSON-план. Думає крок за кроком.
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

    def __init__(self, acl, drivers: Dict[str, Callable], memory=None, max_iterations: int = 5):
        self.acl = acl
        self.drivers = drivers
        self.memory = memory
        self.max_iterations = max_iterations
        self.tool_names = set(drivers.keys()) | {"respond"}
        self.kernel_runtime = None

    def attach_kernel_runtime(self, kernel_runtime):
        """Attach to parent KernelRuntime for capability validation."""
        self.kernel_runtime = kernel_runtime

    async def run(self, user_input: str, session_id: str = "default") -> ReActTrace:
        """
        Головний entry point. Запускає ReAct-цикл.
        Повертає ReActTrace — може бути збережений як Muscle Memory.
        """
        trace = ReActTrace(user_input=user_input)
        history = []

        for i in range(self.max_iterations):
            prompt = self._build_prompt(user_input, history)
            raw = await self.acl.execute(prompt, system_prompt=self.FEW_SHOT_PROMPT)

            step = self._parse_response(raw)
            step.raw_response = raw

            if not step.action:
                step.action = "respond"
                step.action_input = (
                    f"[Cortex Error] Could not parse action from LLM response."
                    f"Raw: {raw[:500]}"
                )

            if step.action == "respond":
                trace.success = True
                trace.final_answer = step.action_input
                trace.steps.append(step)
                break

            observation = await self._execute_action(step)
            step.observation = observation
            trace.steps.append(step)

            history.append({
                "thought": step.thought,
                "action": step.action,
                "action_input": step.action_input,
                "observation": step.observation
            })

        else:
            trace.success = False
            trace.final_answer = (
                f"[Cortex] Max iterations ({self.max_iterations}) reached. "
                f"Partial result after {len(trace.steps)} steps."
            )

        if self.memory:
            self.memory.add_interaction(
                session_id,
                user_input,
                trace.final_answer,
                user_importance=7 if trace.success else 4
            )

        return trace

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
            action_input=action_input
        )

    async def _execute_action(self, step: CortexStep) -> str:
        if step.action not in self.drivers:
            return f"[Error] Unknown tool: {step.action}. Available: {sorted(self.tool_names)}"

        if self.kernel_runtime:
            action = Action(op="EXEC", target=step.action_input, source_agent="cortex")
            if not self.kernel_runtime._check_capability(step.action, action):
                return "[Error] Capability denied by kernel"

        driver = self.drivers[step.action]

        try:
            if asyncio.iscoroutinefunction(driver):
                result = await driver(step.action_input)
            else:
                result = driver(step.action_input)
            return str(result)[:2000]
        except Exception as e:
            return f"[Execution Error] {str(e)}"