"""
kernel/self/metacognition.py
ExArchon Metacognition — self-awareness layer.

Answers: "Where am I?", "What am I doing?", "What can I see/control?",
"What do I remember?", "How healthy am I?"
"""

import os
import time
import platform
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any


@dataclass
class KernelState:
    """Live snapshot of kernel's current situation."""
    status: str = "idle"                      # idle | processing | sleeping | degraded
    current_goal: str = ""                    # active goal description
    current_activity: str = ""                # what kernel is executing right now
    working_dir: str = ""                     # cwd
    platform: str = ""                        # os / machine
    uptime_s: float = 0.0                     # seconds since kernel start
    free_mem_mb: float = 0.0
    cpu_percent: float = 0.0
    capabilities: List[str] = field(default_factory=list)   # enabled cap names
    drivers: Dict[str, str] = field(default_factory=dict)   # tool_id → name
    skills_available: int = 0                  # count in CASS L1
    atoms_in_memory: int = 0                   # count in HNMA L2
    notices_pending: int = 0                   # unread notices
    llm_model: str = ""                        # active LLM model
    last_goal_result: str = ""                 # outcome of last autonomous goal
    environment_hints: List[str] = field(default_factory=list)  # "cpu hot", "mem low"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Metacognition:
    """
    Central self-monitoring service.
    Kernel registers itself; metacognition produces self-reports
    that can be injected into prompts or displayed on a status panel.
    """

    def __init__(self, working_dir: str = "./kernel_workspace"):
        self.start_time = time.time()
        self.working_dir = working_dir
        self.state = KernelState(working_dir=working_dir, platform=f"{platform.system()} {platform.machine()}")
        self._activity_stack: List[str] = []

    def start_activity(self, description: str) -> None:
        """Push a new activity onto the stack. Last one is 'current'."""
        self._activity_stack.append(description)
        self.state.current_activity = description
        self.state.status = "processing"

    def end_activity(self, success: bool = True, result: str = "") -> None:
        """Pop the top activity; if it was the last, return to idle."""
        if self._activity_stack:
            self._activity_stack.pop()
        self.state.current_activity = self._activity_stack[-1] if self._activity_stack else ""
        self.state.status = "idle" if not self._activity_stack else "processing"
        if result:
            self.state.last_goal_result = result
        if success is False:
            self.state.status = "degraded"

    def set_goal(self, goal: str) -> None:
        self.state.current_goal = goal

    def clear_goal(self) -> None:
        self.state.current_goal = ""
        self.state.last_goal_result = self.state.last_goal_result

    def snapshot(
        self,
        capabilities: Optional[List[str]] = None,
        drivers: Optional[Dict[str, str]] = None,
        skills: int = 0,
        atoms: int = 0,
        notices: int = 0,
        llm_model: str = "",
    ) -> KernelState:
        """Refresh environment metrics and return a fresh state dict."""
        self.state.uptime_s = round(time.time() - self.start_time, 1)

        # Capability names from manager (capability ids), not enum objects
        if capabilities:
            self.state.capabilities = [str(c).split(".")[-1] for c in capabilities]
        if drivers:
            self.state.drivers = drivers
        self.state.skills_available = skills
        self.state.atoms_in_memory = atoms
        self.state.notices_pending = notices
        self.state.llm_model = llm_model

        # Lightweight environment hints
        hints = []
        if self.state.cpu_percent > 85:
            hints.append("CPU load high")
        if self.state.free_mem_mb < 500:
            hints.append("Memory low")
        if self.state.skills_available == 0:
            hints.append("No skills in flash yet")
        self.state.environment_hints = hints

        return self.state

    def activity_report(self) -> str:
        """Human-readable self-awareness card."""
        s = self.state
        lines = [
            f"[ExArchon] Status: {s.status}",
            f"  Location : {s.working_dir} @ {s.platform}",
            f"  Uptime   : {s.uptime_s:.0f}s | CPU {s.cpu_percent:.1f}% | Mem {s.free_mem_mb:.0f}MB free",
            f"  Current  : {s.current_activity or '(idle)'}",
            f"  Goal     : {s.current_goal or '(none — awaiting direction)'}",
            f"  LLM      : {s.llm_model or '(unknown)'}",
            f"  Drivers  : {', '.join(s.drivers.values()) if s.drivers else 'none'}",
            f"  Skills   : {s.skills_available} in L1 | Atoms {s.atoms_in_memory} in L2",
            f"  Notices  : {s.notices_pending} pending",
            f"  Caps     : {', '.join(s.capabilities) if s.capabilities else '(none requested)'}",
        ]
        if self.state.environment_hints:
            lines.append("  Hints    : " + "; ".join(self.state.environment_hints))
        if self.state.last_goal_result:
            lines.append(f"  LastGoal : {self.state.last_goal_result}")
        return "\n".join(lines)

    def know_thyself_prompt(self) -> str:
        """Compact prompt fragment for LLM understanding of self."""
        s = self.state
        return (
            "You are ExArchon running on this machine.\n"
            f"Context: OS={s.platform}, cwd={s.working_dir}\n"
            f"Capabilities: {', '.join(s.capabilities) if s.capabilities else 'none'}\n"
            f"Available drivers: {', '.join(s.drivers.values()) if s.drivers else 'none'}\n"
            f"Skills learned: {s.skills_available}\n"
            f"Currently: {s.current_activity or 'idle'}"
        )

    def note_env_metric(self, cpu_percent: float = 0.0, free_mem_mb: float = 0.0) -> None:
        self.state.cpu_percent = round(cpu_percent, 1)
        self.state.free_mem_mb = round(free_mem_mb, 1)