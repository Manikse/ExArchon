"""
kernel/skills/vm_v2.py
ExArchon Skill VM v2 — zero-parse bytecode interpreter.
Pure Python baseline. Replace hot paths with Cython later.
"""
import struct
import time
from enum import IntEnum
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field


class OpCode(IntEnum):
    EXEC = 0x00
    COND = 0x01
    LOOP = 0x02
    PARALLEL = 0x03
    FALLBACK = 0x04
    MEMQ = 0x05
    LLM = 0x06
    CAP = 0x07
    RETURN = 0x08
    NOOP = 0x09


@dataclass
class VMConfig:
    max_steps: int = 1000
    max_time_ms: int = 5000
    stack_size: int = 256


@dataclass
class VMStats:
    executions: int = 0
    total_steps: int = 0
    total_time_us: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0


class SkillVM:
    """
    Zero-parse bytecode interpreter for Muscle Memory.
    Drivers mapped by stable ABI tool IDs.
    """

    TOOL_IDS = {
        0: "terminal",
        1: "file_system",
        2: "web_search",
        3: "spawn_agent",
        4: "respond",
        5: "notice",
    }
    TOOL_TO_ID = {v: k for k, v in TOOL_IDS.items()}

    def __init__(self, drivers: Dict[str, Callable], config: Optional[VMConfig] = None):
        self.drivers = drivers
        self.config = config or VMConfig()
        self.stats = VMStats()
        self._cap_manager = None

    def attach_capability_manager(self, cap_manager):
        self._cap_manager = cap_manager

    def execute(self, bytecode: bytes, context: Optional[Dict[str, Any]] = None) -> str:
        """Execute skill bytecode. Returns result string."""
        start = time.perf_counter_ns()
        pc = 0
        stack: List[str] = []
        steps = 0
        result = ""
        context = context or {}

        try:
            while pc < len(bytecode) and steps < self.config.max_steps:
                steps += 1
                op = bytecode[pc]
                pc += 1

                if op == OpCode.EXEC:
                    tool_id, arg_len = struct.unpack_from("<BH", bytecode, pc)
                    pc += 3
                    arg = bytecode[pc:pc + arg_len].decode("utf-8", errors="replace")
                    pc += arg_len

                    tool_name = self.TOOL_IDS.get(tool_id, "unknown")
                    driver = self.drivers.get(tool_name)

                    if driver is None:
                        stack.append(f"[VM ERROR] Unknown driver: {tool_name}")
                        continue

                    # Capability check inline
                    if self._cap_manager:
                        ok, reason = self._cap_manager.validate(tool_name, "exec", arg)
                        if not ok:
                            stack.append(f"[CAPABILITY DENIED] {reason}")
                            continue

                    # Execute driver
                    if hasattr(driver, "execute"):
                        res = driver.execute(arg)
                    else:
                        res = driver(arg)
                    stack.append(str(res))

                elif op == OpCode.COND:
                    reg_idx, cmp_op, true_off, false_off = struct.unpack_from("<BBHH", bytecode, pc)
                    pc += 6
                    val_len = struct.unpack_from("<H", bytecode, pc)[0]
                    pc += 2
                    val = bytecode[pc:pc + val_len].decode("utf-8", errors="replace")
                    pc += val_len

                    check_val = stack[reg_idx] if reg_idx < len(stack) else ""
                    cond_met = False

                    if cmp_op == 0:   # ==
                        cond_met = check_val == val
                    elif cmp_op == 1: # !=
                        cond_met = check_val != val
                    elif cmp_op == 2: # contains
                        cond_met = val in check_val
                    elif cmp_op == 3: # not_contains
                        cond_met = val not in check_val
                    elif cmp_op == 4: # starts_with
                        cond_met = check_val.startswith(val)
                    elif cmp_op == 5: # gt (numeric)
                        try:
                            cond_met = float(check_val) > float(val)
                        except ValueError:
                            cond_met = False

                    pc = true_off if cond_met else false_off

                elif op == OpCode.LOOP:
                    count, body_off = struct.unpack_from("<HH", bytecode, pc)
                    pc += 4
                    # Simplified: jump back body_off if count > 0
                    # Real implementation would use loop stack
                    # For now: treat as NOOP placeholder
                    pass

                elif op == OpCode.MEMQ:
                    query_len = struct.unpack_from("<H", bytecode, pc)[0]
                    pc += 2
                    query = bytecode[pc:pc + query_len].decode("utf-8", errors="replace")
                    pc += query_len
                    dest_reg = bytecode[pc]
                    pc += 1

                    # Query HNMA via context
                    memory = context.get("memory")
                    if memory:
                        mem_result = memory.query_working(query)
                        stack.append(str(mem_result))
                    else:
                        stack.append("")

                elif op == OpCode.LLM:
                    prompt_len = struct.unpack_from("<H", bytecode, pc)[0]
                    pc += 2
                    prompt = bytecode[pc:pc + prompt_len].decode("utf-8", errors="replace")
                    pc += prompt_len
                    dest_reg = bytecode[pc]
                    pc += 1

                    # Escalate to Cortex via context
                    cortex = context.get("cortex")
                    if cortex:
                        llm_result = cortex.quick_infer(prompt)
                        stack.append(str(llm_result))
                    else:
                        stack.append("[VM] Cortex unavailable")

                elif op == OpCode.CAP:
                    cap_len = struct.unpack_from("<H", bytecode, pc)[0]
                    pc += 2
                    cap_str = bytecode[pc:pc + cap_len].decode("utf-8", errors="replace")
                    pc += cap_len

                    if self._cap_manager:
                        ok, _ = self._cap_manager.validate("vm", "exec", cap_str)
                        if not ok:
                            stack.append(f"[CAPABILITY DENIED] {cap_str}")
                            break

                elif op == OpCode.RETURN:
                    reg_idx = bytecode[pc]
                    pc += 1
                    if reg_idx < len(stack):
                        result = stack[reg_idx]
                    break

                elif op == OpCode.FALLBACK:
                    try_len, catch_len, catch_off = struct.unpack_from("<HHH", bytecode, pc)
                    pc += 6
                    # Simplified: try block executes normally, if error jump to catch
                    # Full impl needs exception stack
                    pass

                elif op == OpCode.PARALLEL:
                    count = bytecode[pc]
                    pc += 1
                    offsets = struct.unpack_from(f"<{count}H", bytecode, pc)
                    pc += count * 2
                    # Placeholder: sequential for now, subprocess later
                    pass

                elif op == OpCode.NOOP:
                    pass

                else:
                    result = f"[VM ERROR] Unknown opcode: {op:#x} at pc={pc-1}"
                    break

        except Exception as e:
            result = f"[VM ERROR] {type(e).__name__}: {e}"

        elapsed_us = (time.perf_counter_ns() - start) / 1000.0
        self.stats.executions += 1
        self.stats.total_steps += steps
        self.stats.total_time_us += elapsed_us

        return result if result else (stack[-1] if stack else "[VM] No result")

    @staticmethod
    def compile_skill(steps: List[Dict[str, str]]) -> bytes:
        """Compile JSON-like steps to VM bytecode."""
        bytecode = bytearray()

        for step in steps:
            tool = step.get("tool", "")
            action = step.get("action_input", "")

            tool_id = SkillVM.TOOL_TO_ID.get(tool, 255)
            if tool_id == 255:
                continue

            arg = action.encode("utf-8")
            bytecode.append(OpCode.EXEC)
            bytecode.append(tool_id)
            bytecode.extend(struct.pack("<H", len(arg)))
            bytecode.extend(arg)

        bytecode.append(OpCode.RETURN)
        bytecode.append(0)
        return bytes(bytecode)

    def get_stats(self) -> Dict[str, Any]:
        avg = (self.stats.total_time_us / self.stats.executions) if self.stats.executions else 0.0
        return {
            "executions": self.stats.executions,
            "total_steps": self.stats.total_steps,
            "avg_time_us": round(avg, 2),
            "cache_hits": self.stats.cache_hits,
            "cache_misses": self.stats.cache_misses,
        }