"""
kernel/runtime/loop_v2.py
ExArchon Kernel Runtime v2 — integrates Muscle Engine + Context Engine + Parallel Cortex.
Drop-in enhancement for loop.py. Can run alongside old runtime during migration.
"""
import os
import time
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# v1.0 imports
from kernel.skills.vm_v2 import SkillVM
from kernel.skills.compiler_v2 import SkillCompiler
from kernel.memory.hnma_controller import HNMAController
from kernel.memory.atom import ContextChainBuilder, FactExtractor
from kernel.cortex.batcher import BatchingEngine, TaskPriority
from kernel.cortex.aml_loader import AdaptiveModelLoader, ModelTier

# Legacy imports (for compat)
from kernel.notice_system import NoticeSystem


@dataclass
class ExecutionResult:
    output: str
    source: str  # "muscle", "cortex", "reflex", "error"
    latency_ms: float
    skill_hash: Optional[str] = None


class KernelRuntimeV2:
    """
    v2 Runtime with HNMA + VM + Batching + AML.
    Integrates with existing KernelManager via composition.
    """

    def __init__(
        self,
        acl,
        memory,  # UNMSController (legacy, kept for compat)
        drivers: Dict,
        workspace: str = "./kernel_workspace",
        state_journal_path: str = "./kernel_workspace/state.journal",
        skill_db_path: str = "./kernel_workspace/skills.db",
        enable_batching: bool = True,
        enable_aml: bool = True,
    ):
        self.acl = acl
        self.memory_legacy = memory
        self.drivers = drivers
        self.workspace = workspace
        os.makedirs(workspace, exist_ok=True)

        # v1.0 Muscle Engine
        self.hnma = HNMAController(base_path=workspace)
        self.vm = SkillVM(drivers=drivers)
        self.compiler = SkillCompiler()

        # v1.0 Context Engine
        self.chain_builder = ContextChainBuilder()

        # v1.0 Parallel Cortex
        self.batcher: Optional[BatchingEngine] = None
        self.aml: Optional[AdaptiveModelLoader] = None

        if enable_batching:
            self.batcher = BatchingEngine(
                llm_call=self._llm_infer_raw,
                batch_window_ms=50.0,
                max_batch_size=10,
            )

        if enable_aml:
            self.aml = AdaptiveModelLoader(
                cloud_infer=self._cloud_infer_raw,
            )

        # Capability manager (wired externally)
        self.cap_manager = None
        self.notice_system: Optional[NoticeSystem] = None

        # Stats
        self._muscle_hits = 0
        self._cortex_calls = 0
        self._total_tasks = 0

    def attach_capability_manager(self, cap_manager):
        self.cap_manager = cap_manager
        self.vm.attach_capability_manager(cap_manager)

    def attach_notice_system(self, notice_system):
        self.notice_system = notice_system

    # ── LLM wrappers for Batcher/AML ──
    async def _llm_infer_raw(self, prompt: str) -> str:
        """Raw LLM call — routed through AML if available."""
        if self.aml:
            return await self.aml.infer(prompt, complexity=0.5)
        return await self.acl.execute(prompt)

    async def _cloud_infer_raw(self, prompt: str) -> str:
        """Cloud-only fallback."""
        return await self.acl.execute(prompt)

    # ── Main entry: step_v2 ──
    async def step_v2(self, user_input: str, session_id: str = "default") -> str:
        """
        v2 execution pipeline:
        1. Reflex check (hardcoded fast path)
        2. Muscle Memory (HNMA L1/L2 + VM) — <1ms
        3. Cortex (Batched LLM) — 2-5s
        4. Compile successful trace to Muscle Memory
        5. Store ContextAtom in HNMA L3
        """
        start = time.perf_counter()
        self._total_tasks += 1

        # 1. Reflex fast path
        reflex = self._check_reflex(user_input)
        if reflex:
            return reflex

        # 2. Muscle Memory — try HNMA first
        muscle_result = await self._try_muscle_memory(user_input)
        if muscle_result:
            self._muscle_hits += 1
            self._store_atom(user_input, muscle_result.output, source="muscle")
            return muscle_result.output

        # 3. Cortex — batched LLM inference
        self._cortex_calls += 1
        cortex_output = await self._run_cortex(user_input, session_id)

        # 4. Compile to Muscle Memory (background)
        asyncio.create_task(self._compile_to_muscle(user_input, cortex_output))

        # 5. Store context
        self._store_atom(user_input, cortex_output, source="cortex")

        latency_ms = (time.perf_counter() - start) * 1000
        return cortex_output

    def _check_reflex(self, user_input: str) -> Optional[str]:
        """Hardcoded reflex responses — zero latency."""
        triggers = {
            "привіт": "Вітаю, Founder. Ядро працює в штатному режимі. Чим можу допомогти?",
            "хто ти": "Я EXARCHON — Когнітивна Операційна Система.",
            "статус": f"Система онлайн. Muscle hits: {self._muscle_hits}, Cortex calls: {self._cortex_calls}.",
        }
        clean = user_input.lower().strip()
        for key, response in triggers.items():
            if key in clean and len(clean) < 30:
                return response
        return None

    async def _try_muscle_memory(self, user_input: str) -> Optional[ExecutionResult]:
        """
        Try to execute via Muscle Memory (HNMA + VM).
        Returns None if no matching skill found.
        """
        start = time.perf_counter()

        # Search L2/L3 for skill by keyword
        # For now: simple keyword match against L2 cache names
        query_lower = user_input.lower()
        best_hash = None
        best_score = 0.0

        for key_hex, entry in self.hnma.l2_cache.items():
            score = 0.0
            name_lower = entry.name.lower()
            desc_lower = entry.description.lower()

            # Exact match bonus
            if query_lower == name_lower:
                score = 2.0
            elif query_lower in name_lower or query_lower in desc_lower:
                score = 1.0
            else:
                # Word overlap
                query_words = set(query_lower.split())
                name_words = set(name_lower.split())
                overlap = len(query_words & name_words)
                if overlap > 0:
                    score = overlap / len(query_words)

            if score > best_score and score >= 0.5:
                best_score = score
                best_hash = bytes.fromhex(key_hex)

        if not best_hash:
            return None

        # Load and execute
        entry = self.hnma.load_skill(best_hash)
        if not entry:
            return None

        result = self.vm.execute(
            entry.bytecode,
            context={
                "memory": self.hnma,
                "cortex": self,
            }
        )

        latency_ms = (time.perf_counter() - start) * 1000
        return ExecutionResult(
            output=result,
            source="muscle",
            latency_ms=latency_ms,
            skill_hash=best_hash,
        )

    async def _run_cortex(self, user_input: str, session_id: str) -> str:
        """Run through Cortex with batching."""
        if self.batcher:
            return await self.batcher.submit(
                prompt=user_input,
                priority=TaskPriority.NORMAL,
                session_id=session_id,
            )
        # Fallback: direct ACL call
        return await self.acl.execute(user_input)

    async def _compile_to_muscle(self, user_input: str, output: str):
        """Background: compile interaction to Muscle Memory skill."""
        try:
            # Simple heuristic: if output contains tool-like patterns, compile
            # Full impl would parse ReAct trace
            trace_steps = [
                {"tool": "terminal", "action_input": user_input},
            ]
            bytecode = self.compiler.compile_from_trace(trace_steps)

            self.hnma.store_skill(
                name=user_input[:50],
                bytecode=bytecode,
                description=user_input,
            )
        except Exception as e:
            if self.notice_system:
                self.notice_system.post(
                    title="Muscle Compile Failed",
                    message=str(e),
                    severity="warning",
                )

    def _store_atom(self, user_input: str, output: str, source: str):
        """Store interaction as ContextAtom in HNMA L3."""
        try:
            raw = f"User: {user_input}\nExArchon: {output}\nSource: {source}"
            atom = self.chain_builder.add_interaction(
                raw_content=raw,
                importance=0.7 if source == "cortex" else 0.5,
            )
            self.hnma.store_atom(atom.to_dict())
        except Exception:
            pass  # Non-critical

    # ── Stats & Diagnostics ──
    def get_stats_v2(self) -> Dict[str, Any]:
        return {
            "runtime_version": "v2.0",
            "total_tasks": self._total_tasks,
            "muscle_hits": self._muscle_hits,
            "cortex_calls": self._cortex_calls,
            "muscle_hit_rate": round(self._muscle_hits / max(self._total_tasks, 1), 3),
            "hnma": self.hnma.get_stats(),
            "vm": self.vm.get_stats(),
            "batcher": self.batcher.get_stats() if self.batcher else None,
            "aml": self.aml.get_stats() if self.aml else None,
        }

    # ── Legacy compat: step() delegates to step_v2 ──
    async def step(self, user_input: str, session_id: str = "default") -> str:
        """Legacy-compatible entry point."""
        return await self.step_v2(user_input, session_id)

    def get_stats(self) -> Dict[str, Any]:
        """Legacy-compatible stats."""
        return self.get_stats_v2()

    def shutdown(self):
        """Graceful shutdown."""
        if self.hnma:
            self.hnma.close()
        if self.batcher:
            # Batcher has no explicit shutdown, tasks complete naturally
            pass

    # ── Background worker status (legacy compat) ──
    def get_background_status(self) -> str:
        stats = self.get_stats_v2()
        lines = [
            f"Runtime: {stats['runtime_version']}",
            f"Total tasks: {stats['total_tasks']}",
            f"Muscle hits: {stats['muscle_hits']} ({stats['muscle_hit_rate']*100:.1f}%)",
            f"Cortex calls: {stats['cortex_calls']}",
            f"HNMA L2 cached: {stats['hnma']['L2_cached']}",
            f"HNMA L3 atoms: {stats['hnma']['L3_atoms']}",
            f"VM executions: {stats['vm']['executions']}",
            f"VM avg time: {stats['vm']['avg_time_us']:.1f}μs",
        ]
        if stats['batcher']:
            lines.append(f"Batches sent: {stats['batcher']['batches_sent']}")
            lines.append(f"Tasks batched: {stats['batcher']['tasks_batched']}")
        return "\n".join(lines)