"""
kernel/runtime/loop_v2.py
ExArchon Kernel Runtime v2.2 — unified cognitive loop.
Now integrates: HNMA + CASS L1 + VM + Batching + AML + ContextInjector
              + ReactEngine (full ReAct) + SpeculativeBrancher + Reflection.
Fixed: journal_path, State.COGNITIVE, reflex dedup, muscle compilation, CAG.
"""
import os
import time
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from kernel.skills.vm_v2 import SkillVM
from kernel.skills.compiler_v2 import SkillCompiler
from kernel.memory.hnma_controller import HNMAController
from kernel.memory.atom import ContextChainBuilder, FactExtractor
from kernel.memory.context_injector import ContextInjector
from kernel.cortex.batcher import BatchingEngine, TaskPriority
from kernel.cortex.aml_loader import AdaptiveModelLoader, ModelTier
from kernel.cortex.react_engine import ReactEngine, ReActTrace
from kernel.skills.brancher import SpeculativeBrancher
from kernel.self.metacognition import Metacognition, KernelState
from kernel.self.skill_miner import SkillMiner, TraceRecord

from kernel.state_machine import StateMachine, State, TransitionError

@dataclass
class ExecutionResult:
    output: str
    source: str
    latency_ms: float
    skill_hash: Optional[str] = None

class KernelRuntimeV2:
    def __init__(
        self,
        acl,
        memory,
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

        self.state_machine = StateMachine()
        self.skill_library = _SkillLibraryStub()

        self.hnma = HNMAController(base_path=workspace)
        self.vm = SkillVM(drivers=drivers)
        self.compiler = SkillCompiler()
        self.injector = ContextInjector(self.hnma)

        self.chain_builder = ContextChainBuilder()

        # === Full cognitive stack v2.2 ===
        self.react_engine = ReactEngine(
            acl=acl,
            drivers=drivers,
            memory=memory,
            max_iterations=5,
            max_reflection_depth=3,
        )
        self.brancher = SpeculativeBrancher(
            acl=acl,
            drivers=drivers,
            memory=memory,
            max_branches=3,
        )
        # Wire brancher to use full ReAct for real tool execution
        self.brancher._react_engine = self.react_engine

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

        self.cap_manager = None
        self.notice_system = None

        # === Self-Awareness Layer ===
        self.metacognition = Metacognition(working_dir=workspace)
        self.skill_miner = SkillMiner(
            hnma_controller=self.hnma,
            skill_compiler=self.compiler,
            min_success_score=0.75,
            max_skills_per_input=3,
        )

        self._muscle_hits = 0
        self._cortex_calls = 0
        self._branch_calls = 0
        self._total_tasks = 0

    def attach_capability_manager(self, cap_manager):
        self.cap_manager = cap_manager
        self.vm.attach_capability_manager(cap_manager)
        self.react_engine.attach_capability_manager(cap_manager)
        self.brancher.attach_capability_manager(cap_manager)

    def attach_notice_system(self, notice_system):
        self.notice_system = notice_system
        self.react_engine.attach_kernel_runtime(self)
        self.brancher.attach_kernel_runtime(self)

    async def _llm_infer_raw(self, prompt: str) -> str:
        if self.aml:
            return await self.aml.infer(prompt, complexity=0.5)
        return await self.acl.execute(prompt)

    async def _cloud_infer_raw(self, prompt: str) -> str:
        return await self.acl.execute(prompt)

    async def step_v2(self, user_input: str, session_id: str = "default") -> str:
        start = time.perf_counter()
        self._total_tasks += 1

        # Update metacognition: start activity
        self.metacognition.start_activity(f"processing: {user_input[:50]}")

        # === TIER 1: Reflex (0 ms) ===
        reflex = self._check_reflex(user_input)
        if reflex:
            self._safe_transition(State.IDLE)
            self.metacognition.end_activity(success=True, result="reflex")
            return reflex

        # === TIER 2: Muscle Memory (CASS L1 + HNMA L2) ===
        muscle_result = await self._try_muscle_memory(user_input)
        if muscle_result:
            self._muscle_hits += 1
            self._store_atom(user_input, muscle_result.output, source="muscle")
            self._safe_transition(State.IDLE)
            self.metacognition.end_activity(success=True, result="muscle_hit")
            return muscle_result.output

        # === TIER 3: Live Cortex (ReAct + Reflection + Speculative Branch) ===
        self._cortex_calls += 1
        self._safe_transition(State.COGNITIVE)

        # Update metacognition with current capabilities
        caps = [str(c).split(".")[-1] for c in (self.cap_manager.capabilities if self.cap_manager else [])]
        self.metacognition.snapshot(
            capabilities=caps,
            drivers=self.drivers,
            skills=self.hnma.get_stats().get("L1_cached", 0),
            atoms=self.hnma.get_stats().get("L3_atoms", 0),
            notices=0,  # TODO: get from notice_system
            llm_model=getattr(self.acl, 'model', 'unknown'),
        )

        # CAG: inject verified facts from HNMA before reasoning
        augmented_prompt = self.injector.inject(user_input)

        cortex_output = ""
        react_trace = None
        try:
            # First try full ReAct — let the agent actually use tools.
            react_trace = await asyncio.wait_for(
                self.react_engine.run(augmented_prompt, session_id),
                timeout=300.0,
            )
            if react_trace.success and react_trace.final_answer:
                cortex_output = react_trace.final_answer
            else:
                # ReAct failed or was cut short — escalate to speculative branching.
                self._branch_calls += 1
                branch_trace = await asyncio.wait_for(
                    self.brancher.solve(augmented_prompt, session_id),
                    timeout=120.0,
                )
                if branch_trace and branch_trace.success and branch_trace.final_answer:
                    cortex_output = branch_trace.final_answer
                else:
                    # Final fallback: single batched LLM call (no tools).
                    if self.batcher:
                        cortex_output = await asyncio.wait_for(
                                self.batcher.submit(
                                    prompt=augmented_prompt,
                                    priority=TaskPriority.NORMAL,
                                    session_id=session_id,
                                ),
                                timeout=60.0,
                            )
                    else:
                        cortex_output = await asyncio.wait_for(
                                self.acl.execute(augmented_prompt),
                                timeout=60.0,
                            )
        except asyncio.TimeoutError:
            cortex_output = "[Kernel] Cortex timeout after 5 minutes. Task abandoned."

        if not cortex_output:
            cortex_output = "[Kernel] No output generated. Task failed silently."

        # Compile successful trace into Muscle Memory (CASS/VM bytecode)
        asyncio.create_task(self._compile_to_muscle(user_input, cortex_output))
        self._store_atom(user_input, cortex_output, source="cortex")
        self._safe_transition(State.IDLE)

        # === Self-Improvement: Mine skill from successful ReAct trace ===
        if react_trace and react_trace.success:
            # Convert ReActTrace to TraceRecord for SkillMiner
            trace_record = TraceRecord(
                user_input=user_input,
                steps=[{"action": s.action, "action_input": s.action_input} for s in react_trace.steps],
                success=react_trace.success,
                final_answer=react_trace.final_answer,
                reflection_count=react_trace.reflection_count,
                total_llm_time_ms=react_trace.total_llm_time_ms,
            )
            asyncio.create_task(self.skill_miner.try_mine(trace_record))

        self.metacognition.end_activity(success=bool(cortex_output and not cortex_output.startswith("[Kernel]")), result=cortex_output[:100])

        latency_ms = (time.perf_counter() - start) * 1000
        return cortex_output

    def _safe_transition(self, to_state: State):
        try:
            self.state_machine.transition(to_state)
        except TransitionError:
            pass

    def _check_reflex(self, user_input: str) -> Optional[str]:
        lower = user_input.lower().strip()
        if lower in ("shutdown now", "kernel panic", "emergency stop"):
            return "[REFLEX] Emergency shutdown triggered."
        if lower in ("status", "kernel status", "what is your state"):
            stats = self.get_stats()
            return f"[REFLEX] State: {stats['current_state']}, Gen: {stats['state_generation']}, Skills: {stats['total_skills']}"
        return None

    async def _try_muscle_memory(self, user_input: str) -> Optional[ExecutionResult]:
        """Search CASS L1 (flash skills) first, then HNMA L2 working cache."""
        start = time.perf_counter()
        query_lower = user_input.lower()

        # === 1. CASS L1 — deterministic flash skills ===
        try:
            cass_rows = self.hnma.query_skills(query_lower, limit=3) if hasattr(self.hnma, 'query_skills') else []
            if cass_rows:
                for row in cass_rows:
                    name = (row.get("name") or "").lower()
                    desc = (row.get("description") or "").lower()
                    score = self._skill_score(query_lower, name, desc)
                    if score >= 0.5:
                        bytecode = row.get("bytecode")
                        if bytecode:
                            result = self.vm.execute(
                                bytecode,
                                context={"memory": self.hnma, "cortex": self}
                            )
                            latency_ms = (time.perf_counter() - start) * 1000
                            return ExecutionResult(
                                output=result,
                                source="cass_l1",
                                latency_ms=latency_ms,
                                skill_hash=row.get("hash"),
                            )
        except Exception:
            pass

        # === 2. HNMA L2 — working RAM LRU cache ===
        best_hash = None
        best_score = 0.0

        try:
            for key_hex, entry in self.hnma.l2_cache.items():
                score = self._skill_score(
                    query_lower,
                    (entry.name or "").lower(),
                    (entry.description or "").lower(),
                )
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_hash = bytes.fromhex(key_hex)

            if not best_hash:
                return None

            entry = self.hnma.load_skill(best_hash)
            if not entry:
                return None

            result = self.vm.execute(
                entry.bytecode,
                context={"memory": self.hnma, "cortex": self}
            )

            latency_ms = (time.perf_counter() - start) * 1000
            return ExecutionResult(
                output=result,
                source="hnma_l2",
                latency_ms=latency_ms,
                skill_hash=best_hash,
            )
        except Exception as e:
            if self.notice_system:
                self.notice_system.post(
                    title="Muscle Memory Error",
                    message=str(e),
                    severity="warning",
                    source="muscle",
                )
            return None

    def _skill_score(self, query_lower: str, name_lower: str, desc_lower: str) -> float:
        """Shared scoring for both CASS L1 and HNMA L2 skill lookup."""
        if query_lower == name_lower:
            return 2.0
        if query_lower in name_lower or query_lower in desc_lower:
            return 1.0
        query_words = set(query_lower.split())
        name_words = set(name_lower.split())
        overlap = len(query_words & name_words)
        if overlap > 0:
            return overlap / len(query_words)
        return 0.0

    async def _compile_to_muscle(self, user_input: str, output: str):
        try:
            trace_steps = [{"tool": "respond", "action_input": output}]
            bytecode = self.compiler.compile_from_trace(trace_steps)

            # Store in HNMA (L2 + L3 persistence)
            skill_hash = self.hnma.store_skill(
                name=user_input[:50],
                bytecode=bytecode,
                description=f"Auto-compiled fallback for: {user_input[:100]}",
            )
            # Also record in CASS flash if supported
            try:
                if hasattr(self.hnma, 'store_cass'):
                    self.hnma.store_cass(
                        name=user_input[:50],
                        bytecode=bytecode,
                        description=f"Auto-compiled fallback for: {user_input[:100]}",
                    )
            except Exception:
                pass

            if self.notice_system:
                self.notice_system.post(
                    title="Muscle Memory Compiled",
                    message=f"Cached response for '{user_input[:50]}' as skill {skill_hash.hex()[:16]}",
                    severity="info",
                    source="muscle",
                )
        except Exception as e:
            if self.notice_system:
                self.notice_system.post(
                    title="Muscle Compile Failed",
                    message=str(e),
                    severity="warning",
                    source="muscle",
                )

    def _store_atom(self, user_input: str, output: str, source: str):
        try:
            raw = f"User: {user_input}\nExArchon: {output}\nSource: {source}"
            atom = self.chain_builder.add_interaction(
                raw_content=raw,
                importance=0.7 if source == "cortex" else 0.5,
            )
            self.hnma.store_atom(atom.to_dict())
        except Exception:
            pass

    def get_stats_v2(self) -> Dict[str, Any]:
        return {
            "runtime_version": "v2.2",
            "total_tasks": self._total_tasks,
            "muscle_hits": self._muscle_hits,
            "cortex_calls": self._cortex_calls,
            "branch_calls": self._branch_calls,
            "muscle_hit_rate": round(self._muscle_hits / max(self._total_tasks, 1), 3),
            "hnma": self.hnma.get_stats(),
            "vm": self.vm.get_stats(),
            "batcher": self.batcher.get_stats() if self.batcher else None,
            "aml": self.aml.get_stats() if self.aml else None,
            "metacognition": {
                "status": self.metacognition.state.status,
                "uptime_s": self.metacognition.state.uptime_s,
                "current_activity": self.metacognition.state.current_activity,
                "current_goal": self.metacognition.state.current_goal,
                "skills_available": self.metacognition.state.skills_available,
                "atoms_in_memory": self.metacognition.state.atoms_in_memory,
                "llm_model": self.metacognition.state.llm_model,
            },
            "skill_miner": self.skill_miner.stats(),
        }

    async def step(self, user_input: str, session_id: str = "default") -> str:
        return await self.step_v2(user_input, session_id)

    def get_stats(self) -> Dict[str, Any]:
        base = self.get_stats_v2()
        base["current_state"] = self.state_machine.state.name
        base["state_generation"] = getattr(self.state_machine, '_generation', 0)
        base["total_skills"] = base["hnma"].get("L2_cached", 0)
        return base

    def shutdown(self):
        if self.hnma:
            self.hnma.close()
        try:
            self.brancher.shutdown()
        except Exception:
            pass

    def self_report(self) -> str:
        """Human-readable self-awareness card — 'who am I, what am I doing'."""
        return self.metacognition.activity_report()

    def get_self_state(self) -> KernelState:
        """Programmatic access to current self-state."""
        return self.metacognition.state

    def get_skill_miner_stats(self) -> Dict[str, int]:
        return self.skill_miner.stats()

    def get_background_status(self) -> str:
        stats = self.get_stats_v2()
        lines = [
            f"Runtime: {stats['runtime_version']}",
            f"Total tasks: {stats['total_tasks']}",
            f"Muscle hits: {stats['muscle_hits']} ({stats['muscle_hit_rate']*100:.1f}%)",
            f"Cortex calls: {stats['cortex_calls']}",
            f"Branch calls: {stats['branch_calls']}",
            f"HNMA L2 cached: {stats['hnma']['L2_cached']}",
            f"HNMA L3 atoms: {stats['hnma']['L3_atoms']}",
            f"VM executions: {stats['vm']['executions']}",
            f"VM avg time: {stats['vm']['avg_time_us']:.1f}μs",
        ]
        if stats['batcher']:
            lines.append(f"Batches sent: {stats['batcher']['batches_sent']}")
            lines.append(f"Tasks batched: {stats['batcher']['tasks_batched']}")
        return "\n".join(lines)

class _SkillLibraryStub:
    def close(self):
        pass