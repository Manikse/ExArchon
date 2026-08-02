"""
kernel/security/capabilities.py
Capability-Based Security для ExArchon.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Tuple, Optional
import fnmatch


@dataclass(frozen=True)
class Capabilities:
    can_read_paths: Tuple[str, ...] = field(default_factory=tuple)
    can_write_paths: Tuple[str, ...] = field(default_factory=tuple)
    can_exec_commands: Tuple[str, ...] = field(default_factory=tuple)
    can_network: bool = False
    can_spawn_agents: bool = False
    max_cpu_percent: float = 50.0
    max_memory_mb: int = 256
    max_exec_time_ms: int = 5000

    def can_read(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, p) for p in self.can_read_paths)

    def can_write(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, p) for p in self.can_write_paths)

    def can_exec(self, command: str) -> bool:
        cmd = command.strip().split()[0] if command.strip() else ""
        return cmd in self.can_exec_commands


class CapabilityChecker:
    def check(self, action, caps: Capabilities) -> Tuple[bool, Optional[str]]:
        if action.op == "READ":
            if not caps.can_read(action.target):
                return False, f"READ denied: {action.target}"
        elif action.op == "WRITE":
            if not caps.can_write(action.target):
                return False, f"WRITE denied: {action.target}"
        elif action.op == "EXEC":
            if not caps.can_exec(action.target):
                return False, f"EXEC denied: {action.target}"
        elif action.op == "NETWORK":
            if not caps.can_network:
                return False, "NETWORK denied"
        elif action.op == "SPAWN":
            if not caps.can_spawn_agents:
                return False, "SPAWN denied"
        elif action.op in ("BRANCH", "WAIT", "NOOP"):
            return True, None
        else:
            return False, f"Unknown op: {action.op}"
        return True, None


# Predefined capability sets
TERMINAL_READONLY = Capabilities(can_exec_commands=("df", "ls", "ps", "cat", "pwd", "whoami", "uptime", "echo"))
TERMINAL_LIMITED = Capabilities(
    can_exec_commands=("df", "ls", "ps", "cat", "pwd", "whoami", "uptime", "echo", "mkdir", "touch", "cp", "mv"),
    can_write_paths=("./logs/*", "./tmp/*")
)
FS_READONLY = Capabilities(can_read_paths=("./workspace/*", "./data/*"))
FS_SHADOW = Capabilities(
    can_read_paths=("./workspace/*", "./data/*"),
    can_write_paths=("./workspace/*", "./data/*")
)
COGNITIVE_AGENT = Capabilities(
    can_read_paths=("./memory/*", "./skills/*"),
    can_write_paths=("./skills/*", "./memory/short_term/*"),
    can_exec_commands=("python3",)
)
REFLEX_AGENT = Capabilities(
    can_exec_commands=("echo",),
    max_cpu_percent=5.0,
    max_memory_mb=32,
    max_exec_time_ms=100
)