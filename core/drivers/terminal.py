import subprocess
import os
import platform
import shlex
import re
from typing import List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    returncode: int
    context: str


class TerminalDriver:
    """
    Sandboxed Terminal Driver.
    ЗАУВАЖЕННЯ: Основна валідація — у kernel (capabilities.py).
    Цей driver — defense in depth (останній рубіж).
    """
    
    # Останній рубіж: навіть якщо kernel помилився — block це
    BLOCKED_COMMANDS = {'mkfs', 'fdisk', 'dd', 'format', 'diskpart'}
    
    DANGEROUS_PATTERNS = [
        r'rm\s+-rf\s+/',
        r'>\s*/dev/',
        r':\(\)\{\s*:\|:&\s*\};',  # fork bomb
        r'curl\s+.*\|\s*sh',
        r'wget\s+.*\|\s*sh',
        r'powershell\s+-enc',
        r'Invoke-Expression',
        r'IEX\s*\(',
    ]

    def __init__(self, working_dir: str = "./kernel_workspace", timeout: int = 20):
        self.name = "terminal"
        self.working_dir = os.path.abspath(working_dir)
        self.os_type = platform.system()
        self.timeout = timeout
        os.makedirs(self.working_dir, exist_ok=True)

    def _get_system_context(self) -> str:
        return f"[SYSTEM INFO: OS={self.os_type}, CWD={self.working_dir}]"

    def _last_resort_check(self, command: str) -> Tuple[bool, str]:
        """Останній рубіж. Не заміна capabilities — доповнення."""
        stripped = command.strip()
        if not stripped:
            return False, "Empty command"
        
        # Block dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                return False, "Dangerous pattern detected (last resort)"
        
        # Block permanently blocked commands
        try:
            base_cmd = shlex.split(stripped)[0]
        except ValueError:
            base_cmd = stripped.split()[0] if stripped.split() else ""
        
        if base_cmd.lower() in {cmd.lower() for cmd in self.BLOCKED_COMMANDS}:
            return False, f"Command '{base_cmd}' permanently blocked"
        
        return True, ""

    def execute(self, command: str) -> str:
        print(f"\n[Driver: Terminal] Executing: '{command}'...")
        
        # Останній рубіж
        ok, err = self._last_resort_check(command)
        if not ok:
            return f"{self._get_system_context()}\n[SECURITY BLOCKED] {err}"
        
        # Підготовка
        try:
            args = shlex.split(command)
            use_shell = False
        except ValueError:
            args = [command]
            use_shell = True
        
        # Виконання
        try:
            result = subprocess.run(
                args,
                shell=use_shell,
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
            status = "Success" if result.returncode == 0 else "Error"
            truncated = final_output[:2000]
            if len(final_output) > 2000:
                truncated += "\n... [output truncated]"
            
            return f"{self._get_system_context()}\n[{status}]\n{truncated}"
            
        except subprocess.TimeoutExpired:
            return f"{self._get_system_context()}\n[ERROR] Timeout ({self.timeout}s)."
        except FileNotFoundError:
            return f"{self._get_system_context()}\n[ERROR] Command not found."
        except Exception as e:
            return f"{self._get_system_context()}\n[ERROR] {str(e)}"