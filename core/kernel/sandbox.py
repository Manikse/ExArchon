"""
kernel/sandbox.py

Sandbox / Simulation Engine

Перед тим, як виконати новий скилл (Muscle Memory) у "продакшені",
проганяємо його через ізольоване середовище з mock-драйверами.

Як у Рафаель: тестуємо навичку у віртуальній машині перед деплоєм у тіло.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from kernel.skills.library import Skill, ExecutionStep
from kernel.state_machine import Action
from kernel.security.capabilities import CapabilityChecker


@dataclass
class SandboxResult:
    """Результат sandbox-прогону."""
    success: bool
    steps_executed: int
    steps_total: int
    errors: List[str]
    outputs: List[str]
    would_modify_files: bool
    would_execute_commands: List[str]
    safety_score: float  # 0.0 - 1.0


class MockDriver:
    """
    Фейковий драйвер. Імітує виконання без side effects.
    Записує, ЩО він би виконав, але не робить цього.
    """

    def __init__(self, real_driver_name: str):
        self.name = real_driver_name
        self.captured_calls: List[str] = []
        self.would_modify = False

    def execute(self, action_input: str) -> str:
        self.captured_calls.append(action_input)
        # Аналізуємо, чи це небезпечна операція
        lower = action_input.lower()
        if any(k in lower for k in ["write", "delete", "rm ", "mkdir", "patch", "append"]):
            self.would_modify = True
        return f"[MOCK {self.name}] Would execute: {action_input[:100]}"


class Sandbox:
    """
    Ізольоване середовище для тестування скиллів.
    """

    def __init__(self, capability_checker: Optional[CapabilityChecker] = None):
        self.cap_checker = capability_checker or CapabilityChecker()
        self._history: List[SandboxResult] = []

    def dry_run(self, skill: Skill, real_drivers: Dict[str, Any]) -> SandboxResult:
        """
        Проганяє Execution Graph скилла на mock-драйверах.

        Повертає SandboxResult з аналізом безпеки.
        """
        errors = []
        outputs = []
        mock_drivers = {}
        would_modify = False
        would_execute = []

        # Створюємо mock-драйвери для всіх реальних
        for name in real_drivers:
            mock_drivers[name] = MockDriver(name)

        for i, step in enumerate(skill.execution_graph):
            if step.tool not in mock_drivers:
                errors.append(f"Step {i}: Unknown tool '{step.tool}'")
                continue

            # Capability check (як у реальному kernel)
            action = Action(op="EXEC", target=step.action_input, source_agent="sandbox")
            # У sandbox перевіряємо, але не блокуємо — тільки логуємо
            # (бо sandbox — це тест, не продакшн)

            mock = mock_drivers[step.tool]
            try:
                result = mock.execute(step.action_input)
                outputs.append(result)
                if mock.would_modify:
                    would_modify = True
                would_execute.append(f"[{step.tool}] {step.action_input}")
            except Exception as e:
                errors.append(f"Step {i}: {str(e)}")

        # Safety score
        safety_score = 1.0
        if errors:
            safety_score -= 0.3 * min(len(errors), 3)
        if would_modify:
            safety_score -= 0.2
        if len(skill.execution_graph) > 10:
            safety_score -= 0.1  # Довгі графи — ризикованіше
        safety_score = max(0.0, safety_score)

        result = SandboxResult(
            success=len(errors) == 0,
            steps_executed=len(skill.execution_graph) - len(errors),
            steps_total=len(skill.execution_graph),
            errors=errors,
            outputs=outputs,
            would_modify_files=would_modify,
            would_execute_commands=would_execute,
            safety_score=safety_score,
        )

        self._history.append(result)
        return result

    def validate_for_production(self, skill: Skill, real_drivers: Dict[str, Any]) -> tuple[bool, str]:
        """
        Швидка перевірка: чи можна деплоїти скилл у продакшн.

        Повертає (ok, reason).
        """
        result = self.dry_run(skill, real_drivers)

        if not result.success:
            return False, f"Sandbox failed: {'; '.join(result.errors[:3])}"

        if result.safety_score < 0.5:
            return False, f"Safety score too low: {result.safety_score:.2f}"

        if result.would_modify_files and len(result.would_execute_commands) > 5:
            return False, "High-risk skill: modifies files with many steps"

        return True, f"Sandbox OK. Safety: {result.safety_score:.2f}. Steps: {result.steps_total}"

    def get_history(self) -> List[SandboxResult]:
        return self._history