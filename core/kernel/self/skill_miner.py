"""
kernel/self/skill_miner.py
ExArchon Skill Miner — self-improvement via experience.

When the kernel successfully completes a non-trivial task through ReAct,
SkillMiner converts that execution trace into a reusable CASS skill
(compiled bytecode stored in flash memory L1).
Over time the kernel "learns" without being explicitly taught.
"""

import hashlib
import logging
from typing import Optional, List, Dict, Any

from kernel.skills.compiler_v2 import SkillCompiler


class SkillMiner:
    """
    Extracts reusable skills from successful ReAct traces.

    Behaviour:
      - Pass a completed ReActTrace whose steps were all tool calls.
      - SkillMiner compiles the tool steps into CASS bytecode via SkillCompiler.
      - If bytecode is non-empty and unique, store it in CASS L1 via store_cass.
    """

    def __init__(
        self,
        store_cass: callable,
        query_skills: callable,
        compiler: Optional[SkillCompiler] = None,
        logger: Optional[logging.Logger] = None,
        min_steps: int = 2,
    ):
        """
        :param store_cass: callable(name, bytecode, description) -> hash
        :param query_skills: callable(query, limit) -> list[dict] with 'bytecode'
        :param compiler: SkillCompiler instance
        :param min_steps: minimum tool steps to consider a trace worth mining
        """
        self.store_cass = store_cass
        self.query_skills = query_skills
        self.compiler = compiler or SkillCompiler()
        self.logger = logger or logging.getLogger("SkillMiner")
        self.min_steps = min_steps
        self.mined_count = 0
        self.duplicate_count = 0

    @staticmethod
    def _is_tool_step(step) -> bool:
        """A step is tool-usable if it has action and action_input."""
        return bool(getattr(step, "action", None)) and bool(getattr(step, "action_input", None))

    def _dedupe(self, bytecode: bytes, query: str, limit: int = 3) -> bool:
        """Return True if bytecode already exists in CASS (duplicate)."""
        try:
            rows = self.query_skills(query, limit=limit)
            for row in rows:
                existing = row.get("bytecode")
                if existing and existing == bytecode:
                    return True
        except Exception:
            pass
        return False

    def mine(self, trace, description: str = "") -> Optional[str]:
        """
        Attempt to extract and store a skill from a ReAct trace.
        :param trace: object with `.steps` (list) and `.success` (bool)
        :param description: natural-language description of the task/skill
        :return: CASS L1 hash if stored, else None
        """
        if not getattr(trace, "steps", None):
            return None

        # Only mine successful, non-trivial traces
        if not getattr(trace, "success", False):
            return None

        tool_steps = [s for s in trace.steps if self._is_tool_step(s)]
        if len(tool_steps) < self.min_steps:
            return None

        # Build the skill name from the task itself
        user_input = getattr(trace, "user_input", "") or "learned_task"
        skill_name = self._slugify(user_input) or "learned_task"

        # Compile tool steps to bytecode (respond/notice built-ins allowed)
        try:
            trace_steps = [
                {"tool": s.action, "action_input": s.action_input}
                for s in tool_steps
            ]
            bytecode = self.compiler.compile_from_trace(trace_steps)
        except Exception as e:
            self.logger.warning(f"[SkillMiner] compile failed: {e}")
            return None

        if not bytecode:
            return None

        # Deduplicate: don't re-store an identical skill
        if self._dedupe(bytecode, skill_name):
            self.duplicate_count += 1
            return None

        desc = description or f"Auto-mined from task: {user_input[:120]}"
        try:
            skill_hash = self.store_cass(skill_name, bytecode, desc)
            self.mined_count += 1
            # print for visibility; logging may be noisy in long runs
            if self.logger:
                self.logger.info(f"[SkillMiner] Stored skill '{skill_name}' ({skill_hash.hex()[:16]})")
            return skill_hash
        except Exception as e:
            self.logger.warning(f"[SkillMiner] store failed: {e}")
            return None

    @staticmethod
    def _slugify(text: str) -> str:
        """Create a safe, short skill name from free text."""
        clean = "".join(c if c.isalnum() else " " for c in text.strip().lower())
        words = clean.split()
        keep = words[:4]
        return "_".join(keep) if keep else ""