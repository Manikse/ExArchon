import subprocess
import os
import platform
import shlex
import re
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class SandboxLevel(Enum):
    """Рівні пісочниці для виконання команд."""
    STRICT = "strict"      # shell=False, тільки whitelist
    MODERATE = "moderate"  # shell=True, blacklist небезпечних патернів
    DISABLED = "disabled"  # shell=True, без обмежень (НЕ РЕКОМЕНДУЄТЬСЯ)


@dataclass
class ExecutionResult:
    """Структурований результат виконання команди."""
    stdout: str
    stderr: str
    returncode: int
    context: str


class TerminalDriver:
    """
    Sandboxed Terminal Driver для ExArchon.
    Виконує OS-команди з налаштовуваними політиками безпеки.

    Рівні безпеки:
    - STRICT: shell=False, тільки дозволені команди з whitelist
    - MODERATE: shell=True, але блокуються небезпечні патерни (default)
    - DISABLED: без обмежень (тільки для девелопменту!)
    """

    # Базовий whitelist для STRICT mode
    DEFAULT_ALLOWED_COMMANDS = {
        # Unix basics
        'ls', 'dir', 'cat', 'type', 'echo', 'pwd', 'cd', 'date', 'whoami',
        'hostname', 'ps', 'tasklist', 'find', 'grep', 'head', 'tail',
        'wc', 'sort', 'uniq', 'df', 'du', 'free', 'uptime', 'uname',
        'git', 'python', 'python3', 'pip', 'pip3', 'node', 'npm', 'npx',
        'curl', 'wget', 'mkdir', 'touch', 'cp', 'copy', 'mv', 'move',
        'rm', 'del', 'rmdir', 'chmod', 'chown', 'ln', 'tar', 'zip', 'unzip',
        'cat', 'less', 'more', 'nano', 'vim', 'vi', 'code',
        # Windows PowerShell
        'Get-Date', 'Get-ChildItem', 'Get-Content', 'Write-Output',
        'Test-Path', 'Get-Location', 'Select-String', 'Get-Process',
        'Start-Process', 'Stop-Process', 'New-Item', 'Remove-Item',
        'Copy-Item', 'Move-Item', 'Set-Location', 'Get-Help',
    }

    # Blacklist патернів для MODERATE mode
    DANGEROUS_PATTERNS = [
        r'rm\s+-rf\s+/',
        r'>\s*/dev/',
        r':\(\)\{\s*:\|:&\s*\};',  # fork bomb
        r'curl\s+.*\|\s*sh',
        r'wget\s+.*\|\s*sh',
        r'powershell\s+-enc',  # encoded commands
        r'Invoke-Expression',
        r'IEX\s*\(',
        r'\$\(',
        r'`\s*\$',
        r'base64\s+-d\s*\|',
        r'eval\s*\(',
        r'exec\s*\(',
        r'__import__\s*\(',
        r'import\s+os\.system',
        r'subprocess\.call',
        r'subprocess\.run',
        r'os\.system',
    ]

    # Команди, які НІКОЛИ не можна виконувати навіть в MODERATE
    BLOCKED_COMMANDS = {
        'mkfs', 'fdisk', 'dd', 'format', 'diskpart',
    }

    def __init__(
        self, 
        working_dir: str = "./kernel_workspace",
        sandbox_level: SandboxLevel = SandboxLevel.MODERATE,
        allowed_commands: Optional[set] = None,
        timeout: int = 20
    ):
        self.name = "Terminal"
        self.working_dir = os.path.abspath(working_dir)
        self.os_type = platform.system()
        self.sandbox_level = sandbox_level
        self.allowed_commands = allowed_commands or self.DEFAULT_ALLOWED_COMMANDS
        self.timeout = timeout

        os.makedirs(self.working_dir, exist_ok=True)

    def _get_system_context(self) -> str:
        """Повертає короткий опис середовища для Ядра."""
        return f"[SYSTEM INFO: OS={self.os_type}, Shell=Default, CWD={self.working_dir}]"

    def _validate_command(self, command: str) -> Tuple[bool, str]:
        """
        Валідація команди відповідно до політик безпеки.
        Повертає (is_valid, error_message).
        """
        stripped = command.strip()
        if not stripped:
            return False, "Empty command"

        if self.sandbox_level == SandboxLevel.DISABLED:
            return True, ""

        # Перевірка на path traversal
        normalized = os.path.normpath(stripped)
        if '..' in stripped or normalized.startswith('..'):
            return False, "Path traversal attempt detected"

        # Перевірка на абсолютні шляхи, що виходять за working_dir
        # (спрощена перевірка — блокуємо явні /etc, C:\Windows тощо)
        dangerous_paths = [
            '/etc/', '/usr/', '/bin/', '/sbin/', '/lib/', '/sys/',
            '/proc/', '/dev/', '/boot/', '/root/',
            'C:\\Windows', 'C:\\Program Files', 'C:\\System32',
        ]
        for dpath in dangerous_paths:
            if dpath.lower() in stripped.lower():
                return False, f"Access to system path blocked: {dpath}"

        # Перевірка на небезпечні патерни
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                return False, f"Dangerous pattern detected"

        # Витягуємо базову команду
        try:
            parts = shlex.split(stripped)
            if not parts:
                return False, "Empty command after parsing"
            base_cmd = parts[0]
        except ValueError:
            # Не вдалося розпарсити — можливо, складна shell-конструкція
            if self.sandbox_level == SandboxLevel.STRICT:
                return False, "Invalid command syntax in strict mode"
            base_cmd = stripped.split()[0] if stripped.split() else ""

        # Перевірка blocked commands (завжди, незалежно від режиму)
        if base_cmd.lower() in {cmd.lower() for cmd in self.BLOCKED_COMMANDS}:
            return False, f"Command '{base_cmd}' is permanently blocked"

        # STRICT mode: перевірка whitelist
        if self.sandbox_level == SandboxLevel.STRICT:
            if base_cmd.lower() not in {cmd.lower() for cmd in self.allowed_commands}:
                return False, f"Command '{base_cmd}' not in whitelist. Allowed: {sorted(self.allowed_commands)}"

        return True, ""

    def _prepare_execution(self, command: str) -> Tuple[List[str], Optional[str], bool]:
        """
        Підготовка аргументів виконання залежно від ОС і рівня пісочниці.
        Повертає (args, executable, use_shell).
        """
        if self.os_type == "Windows":
            if self.sandbox_level == SandboxLevel.STRICT:
                # Спробуємо розпарсити як пряму команду
                try:
                    args = shlex.split(command)
                    if args:
                        return args, None, False
                except ValueError:
                    pass
                # Якщо не вдалося — PowerShell з обмеженням
                return [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Restricted",
                    "-Command", command
                ], "powershell.exe", False
            else:
                # MODERATE / DISABLED: PowerShell з NoProfile для безпеки
                return [
                    "powershell.exe", "-NoProfile", "-Command", command
                ], "powershell.exe", False
        else:
            # Unix-like (Linux, macOS)
            if self.sandbox_level == SandboxLevel.STRICT:
                try:
                    args = shlex.split(command)
                    return args, None, False
                except ValueError:
                    return [], None, False
            else:
                return [command], None, True

    def execute(self, command: str) -> str:
        """
        Виконує команду з повною валідацією та sandbox.
        """
        print(f"\n[Driver: Terminal]  Executing: '{command}'...")

        # === ВАЛІДАЦІЯ ===
        is_valid, error_msg = self._validate_command(command)
        if not is_valid:
            print(f"[Driver: Terminal]  SECURITY BLOCKED: {error_msg}")
            return f"{self._get_system_context()}\n[SECURITY BLOCKED] {error_msg}"

        # === ПІДГОТОВКА ===
        args, executable, use_shell = self._prepare_execution(command)

        if not args:
            return f"{self._get_system_context()}\n[ERROR] Failed to parse command for execution"

        # === ВИКОНАННЯ ===
        try:
            result = subprocess.run(
                args,
                shell=use_shell,
                executable=executable,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding='utf-8',
                errors='replace'
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                output_parts.append(f"STDERR: {stderr}")

            final_output = "\n".join(output_parts)

            if not final_output:
                status = "Success" if result.returncode == 0 else "Failed"
                return (
                    f"{self._get_system_context()}\n"
                    f"[{status}] Executed with no output (exit code: {result.returncode})."
                )

            status = "Success" if result.returncode == 0 else "Error"
            truncated = final_output[:2000]
            if len(final_output) > 2000:
                truncated += "\n... [output truncated]"

            return f"{self._get_system_context()}\n[{status}]\n{truncated}"

        except subprocess.TimeoutExpired:
            return f"{self._get_system_context()}\n[ERROR] Timeout ({self.timeout}s)."
        except FileNotFoundError:
            return f"{self._get_system_context()}\n[ERROR] Command not found in PATH."
        except PermissionError:
            return f"{self._get_system_context()}\n[ERROR] Permission denied."
        except Exception as e:
            return f"{self._get_system_context()}\n[ERROR] Execution failed: {str(e)}"