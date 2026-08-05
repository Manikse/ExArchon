import subprocess
import os
import platform
import shlex
from typing import List, Optional, Tuple
from dataclasses import dataclass

from kernel.security.capabilities import CapabilityManager, CapOp, CapabilityToken


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    returncode: int
    context: str


class TerminalDriver:
    """
    Sandboxed Terminal Driver — Capability-Based Edition.

    Зміни від попередньої версії:
    1. ВИДАЛЕНО blacklist/whitelist. Тільки CapabilityManager.
    2. ВИДАЛЕНО DANGEROUS_PATTERNS та BLOCKED_COMMANDS.
    3. shell=True ЗАБОРОНЕНО — якщо shlex.split падає, повертаємо помилку.
    4. Кожна команда перевіряється через CapabilityManager ПЕРЕД виконанням.
    5. Додано execution limits (timeout з capability token).

    Філософія: driver НЕ вирішує, що безпечно. Kernel вирішує через capabilities.
    Driver тільки виконує те, що йому дозволено.
    """

    def __init__(
        self,
        working_dir: str = "./kernel_workspace",
        capability_manager: Optional[CapabilityManager] = None,
        default_timeout: int = 20,
    ):
        self.name = "terminal"
        self.working_dir = os.path.abspath(working_dir)
        self.os_type = platform.system()
        self.default_timeout = default_timeout
        self.cap_manager = capability_manager
        os.makedirs(self.working_dir, exist_ok=True)

    def _get_system_context(self) -> str:
        return f"[SYSTEM INFO: OS={self.os_type}, CWD={self.working_dir}]"

    def _resolve_command_target(self, command: str) -> str:
        """Витягує базову команду для перевірки capability."""
        stripped = command.strip()
        if not stripped:
            return ""
        try:
            args = shlex.split(stripped)
            return args[0] if args else ""
        except ValueError:
            # Якщо не можемо розпарсити — це shell-конструкція, яка потребує shell=True
            # Але shell=True ЗАБОРОНЕНО в kernel mode
            return "<shell_construct>"

    def execute(self, command: str, capability_token: Optional[CapabilityToken] = None) -> str:
        """
        Виконує команду з перевіркою capability.

        Args:
            command: команда для виконання
            capability_token: явний токен (опціонально, якщо cap_manager налаштований)
        """
        print(f"\n[Driver: Terminal] Executing: '{command[:100]}'...")

        # === 1. Перевірка capability ===
        base_cmd = self._resolve_command_target(command)
        if not base_cmd:
            return f"{self._get_system_context()}\n[ERROR] Empty command"

        if self.cap_manager:
            ok, reason = self.cap_manager.validate("terminal", CapOp.EXEC, base_cmd)
            if not ok:
                return f"{self._get_system_context()}\n[CAPABILITY DENIED] {reason}"

        # === 2. Парсинг аргументів — БЕЗ shell=True fallback ===
        try:
            args = shlex.split(command)
            use_shell = False
        except ValueError as e:
            return (
                f"{self._get_system_context()}\n"
                f"[SECURITY ERROR] Command parsing failed: {e}.\n"
                f"Shell constructs are prohibited in kernel mode. Use explicit arguments."
            )

        # === 3. Додаткова перевірка: якщо token вимагає no_shell, а ми тут — значить все ок,
        # бо shlex.split спрацював. Але перевіримо явно. ===
        if capability_token:
            no_shell = capability_token.condition("no_shell")
            if no_shell == "True" and use_shell:
                return (
                    f"{self._get_system_context()}\n"
                    f"[CAPABILITY DENIED] Token requires no_shell=True"
                )
            # Timeout з токена
            max_timeout = capability_token.condition("max_timeout")
            if max_timeout:
                try:
                    timeout = min(self.default_timeout, int(max_timeout))
                except ValueError:
                    timeout = self.default_timeout
            else:
                timeout = self.default_timeout
        else:
            timeout = self.default_timeout

        # === 4. Виконання ===
        try:
            result = subprocess.run(
                args,
                shell=use_shell,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                output_parts.append(f"STDERR: {stderr}")

            final_output = "\n".join(output_parts)
            status = "Success" if result.returncode == 0 else "Error"
            truncated = final_output[:2000]
            if len(final_output) > 2000:
                truncated += "\n... [output truncated]"

            return f"{self._get_system_context()}\n[{status}]\n{truncated}"

        except subprocess.TimeoutExpired:
            return f"{self._get_system_context()}\n[ERROR] Timeout ({timeout}s)."
        except FileNotFoundError:
            return f"{self._get_system_context()}\n[ERROR] Command not found: {base_cmd}"
        except PermissionError:
            return f"{self._get_system_context()}\n[ERROR] Permission denied: {base_cmd}"
        except Exception as e:
            return f"{self._get_system_context()}\n[ERROR] {str(e)}"

    def execute_with_result(self, command: str, capability_token: Optional[CapabilityToken] = None) -> ExecutionResult:
        """Повертає структурований результат замість рядка."""
        raw = self.execute(command, capability_token)
        lines = raw.split("\n")

        # Парсимо статус з відповіді
        returncode = 0 if "[Success]" in raw else 1
        stdout = ""
        stderr = ""

        in_stdout = False
        for line in lines:
            if line.startswith("[SYSTEM INFO:"):
                continue
            if line == "[Success]" or line == "[Error]":
                in_stdout = True
                continue
            if line.startswith("STDERR: "):
                stderr += line[8:] + "\n"
            elif in_stdout:
                stdout += line + "\n"

        return ExecutionResult(
            stdout=stdout.strip(),
            stderr=stderr.strip(),
            returncode=returncode,
            context=self._get_system_context(),
        )