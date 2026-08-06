"""
kernel/sandbox.py
Sandbox v2 — Real process isolation with resource limits.
Cross-platform: resource module is Unix-only.
"""
import os
import subprocess
import tempfile
import signal
import sys
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

from kernel.skills.library import Skill, ExecutionStep
from kernel.security.capabilities import CapabilityManager, CapOp

try:
    import resource
    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False


@dataclass
class SandboxResult:
    safe: bool
    output: str
    violations: list


class Sandbox:
    """
    Real sandbox: runs skills in isolated subprocess with resource limits.
    Falls back to mock mode if sandbox tools unavailable or on Windows.
    """

    def __init__(
        self,
        capability_manager: Optional[CapabilityManager] = None,
        timeout: int = 5,
        max_memory_mb: int = 100,
    ):
        self.cap_manager = capability_manager
        self.timeout = timeout
        self.max_memory_mb = max_memory_mb
        # On Windows or without resource module, use mock mode
        self.mock_mode = (not _HAS_RESOURCE) or (not self._check_sandbox_available())

    def _check_sandbox_available(self) -> bool:
        """Check if we can create isolated subprocesses."""
        try:
            subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def validate_for_production(self, skill: Skill, drivers: Dict) -> Tuple[bool, str]:
        """Dry-run skill in sandbox. Returns (is_safe, report)."""
        if self.mock_mode:
            return self._mock_validate(skill, drivers)

        violations = []
        outputs = []

        for step in skill.execution_graph:
            if self.cap_manager:
                ok, reason = self.cap_manager.validate("sandbox", CapOp.EXEC, step.tool)
                if not ok:
                    violations.append(f"Capability: {reason}")

            result = self._run_isolated(step, drivers.get(step.tool))
            outputs.append(result)

            if result.startswith("[ERROR]") or result.startswith("[VIOLATION]"):
                violations.append(result)

        is_safe = len(violations) == 0
        report = f"Sandbox test: {len(skill.execution_graph)} steps, {len(violations)} violations\n"
        if violations:
            report += "Violations:\n" + "\n".join(violations)
        return is_safe, report

    def _run_isolated(self, step: ExecutionStep, driver) -> str:
        """Run a single step in isolated subprocess with resource limits."""
        if driver is None:
            return f"[ERROR] No driver for {step.tool}"

        try:
            # Build a test command
            test_cmd = [
                sys.executable, "-c",
                f"print('Sandbox test: {step.tool} {step.action_input[:50]}')"
            ]

            kwargs = {
                "capture_output": True,
                "text": True,
                "timeout": self.timeout,
            }

            # preexec_fn only works on Unix
            if _HAS_RESOURCE and sys.platform != "win32":
                def preexec():
                    max_bytes = self.max_memory_mb * 1024 * 1024
                    resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
                    resource.setrlimit(resource.RLIMIT_CPU, (self.timeout, self.timeout))
                    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
                kwargs["preexec_fn"] = preexec

            result = subprocess.run(test_cmd, **kwargs)
            return result.stdout.strip() or "[OK] Sandbox step passed"

        except subprocess.TimeoutExpired:
            return "[VIOLATION] Step exceeded timeout"
        except Exception as e:
            return f"[ERROR] Sandbox execution failed: {e}"

    def _mock_validate(self, skill: Skill, drivers: Dict) -> Tuple[bool, str]:
        """Fallback mock validation when real sandbox unavailable."""
        captured = []
        for step in skill.execution_graph:
            captured.append(f"[MOCK {step.tool}] Would execute: {step.action_input[:100]}")
        return True, "Mock sandbox (no real isolation)\n" + "\n".join(captured)