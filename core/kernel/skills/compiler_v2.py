"""
kernel/skills/compiler_v2.py
Skill Compiler v2 — converts JSON/declarative skills to VM bytecode.
"""
import struct
from typing import List, Dict, Any
from kernel.skills.vm_v2 import SkillVM, OpCode


class SkillCompiler:
    """
    Compiles high-level skill definitions to VM bytecode.
    Supports: linear execution, conditions, loops, memory queries.
    """

    def __init__(self):
        self.tool_to_id = SkillVM.TOOL_TO_ID

    def compile_linear(self, steps: List[Dict[str, str]]) -> bytes:
        """Compile simple linear skill (tool → action)."""
        return SkillVM.compile_skill(steps)

    def compile_with_conditions(self, nodes: List[Dict[str, Any]]) -> bytes:
        """
        Compile skill with conditions.
        nodes: [
            {"type": "exec", "tool": "terminal", "input": "df -h"},
            {"type": "cond", "reg": 0, "cmp": ">", "val": "90", "true": 2, "false": 3},
        ]
        The `true`/`false` fields can be node indices (which we resolve to offsets),
        or explicit bytecode offsets. If absent, defaults to the next node (fall-through).
        """
        bytecode = bytearray()
        jump_fixups = []  # (true_off_pos, false_off_pos, true_target, false_target)

        # First pass: emit opcodes, record jump fixup positions and node start offsets.
        # Node index -> bytecode offset map
        node_offsets = {}
        current_offset = 0

        for i, node in enumerate(nodes):
            node_offsets[i] = current_offset
            node_type = node.get("type", "exec")

            if node_type == "exec":
                tool = node.get("tool", "")
                action = node.get("input", "")
                tid = self.tool_to_id.get(tool, 255)
                if tid == 255:
                    continue
                arg = action.encode("utf-8")
                bytecode.append(OpCode.EXEC)
                bytecode.append(tid)
                bytecode.extend(struct.pack("<H", len(arg)))
                bytecode.extend(arg)
                current_offset = len(bytecode)

            elif node_type == "cond":
                reg = node.get("reg", 0)
                cmp_map = {"==": 0, "!=": 1, "contains": 2, "not_contains": 3, "starts_with": 4, ">": 5}
                cmp_op = cmp_map.get(node.get("cmp", "=="), 0)
                val = str(node.get("val", "")).encode("utf-8")

                # Reserve space for offsets (fixed later via fixups)
                bytecode.append(OpCode.COND)
                bytecode.append(reg)
                bytecode.append(cmp_op)
                # true_off placeholder
                true_off_pos = len(bytecode)
                bytecode.extend(struct.pack("<H", 0))
                # false_off placeholder
                false_off_pos = len(bytecode)
                bytecode.extend(struct.pack("<H", 0))
                bytecode.extend(struct.pack("<H", len(val)))
                bytecode.extend(val)
                current_offset = len(bytecode)

                true_target = node.get("true", i + 1)
                false_target = node.get("false", i + 1)
                jump_fixups.append((true_off_pos, false_off_pos, true_target, false_target))

            elif node_type == "memq":
                query = str(node.get("query", "")).encode("utf-8")
                dest = node.get("dest", 0)
                bytecode.append(OpCode.MEMQ)
                bytecode.extend(struct.pack("<H", len(query)))
                bytecode.extend(query)
                bytecode.append(dest)
                current_offset = len(bytecode)

            elif node_type == "llm":
                prompt = str(node.get("prompt", "")).encode("utf-8")
                dest = node.get("dest", 0)
                bytecode.append(OpCode.LLM)
                bytecode.extend(struct.pack("<H", len(prompt)))
                bytecode.extend(prompt)
                bytecode.append(dest)
                current_offset = len(bytecode)

            elif node_type == "cap":
                cap = str(node.get("cap", "")).encode("utf-8")
                bytecode.append(OpCode.CAP)
                bytecode.extend(struct.pack("<H", len(cap)))
                bytecode.extend(cap)
                current_offset = len(bytecode)

        # Record the final offset (RETURN instruction position)
        final_offset = len(bytecode)
        bytecode.append(OpCode.RETURN)
        bytecode.append(0)

        # Second pass: apply jump fixups now that all offsets are known.
        for true_off_pos, false_off_pos, true_target, false_target in jump_fixups:
            # Resolve targets: if int and it's a node index, use node_offsets; else treat as absolute offset.
            def resolve(target, default_offset):
                if isinstance(target, int):
                    if target in node_offsets:
                        return node_offsets[target]
                    if 0 <= target < final_offset:
                        return target  # Already an absolute bytecode offset
                return default_offset

            true_off = resolve(true_target, node_offsets.get(true_target, final_offset))
            false_off = resolve(false_target, node_offsets.get(false_target, final_offset))

            struct.pack_into("<H", bytecode, true_off_pos, true_off)
            struct.pack_into("<H", bytecode, false_off_pos, false_off)

        return bytes(bytecode)

    def compile_from_trace(self, trace_steps: List[Dict]) -> bytes:
        """Compile ReAct trace to bytecode."""
        steps = []
        for s in trace_steps:
            if "tool" in s and "action_input" in s:
                steps.append({
                    "tool": s["tool"],
                    "action_input": s["action_input"],
                })
        return self.compile_linear(steps)

    @staticmethod
    def disassemble(bytecode: bytes) -> List[str]:
        """Human-readable disassembly for debugging."""
        pc = 0
        lines = []
        while pc < len(bytecode):
            op = bytecode[pc]
            pc += 1
            name = OpCode(op).name if op <= max(OpCode) else f"UNKNOWN({op})"

            if op == OpCode.EXEC:
                tid = bytecode[pc]
                alen = struct.unpack_from("<H", bytecode, pc+1)[0]
                arg = bytecode[pc+3:pc+3+alen].decode("utf-8", errors="replace")
                lines.append(f"{len(lines):04d}: EXEC tool={tid} arg='{arg[:40]}'")
                pc += 3 + alen

            elif op == OpCode.COND:
                reg, cmp_op = bytecode[pc], bytecode[pc+1]
                true_off, false_off = struct.unpack_from("<HH", bytecode, pc+2)
                vlen = struct.unpack_from("<H", bytecode, pc+6)[0]
                val = bytecode[pc+8:pc+8+vlen].decode("utf-8", errors="replace")
                lines.append(f"{len(lines):04d}: COND reg={reg} cmp={cmp_op} val='{val}' true@{true_off} false@{false_off}")
                pc += 8 + vlen

            elif op == OpCode.MEMQ:
                qlen = struct.unpack_from("<H", bytecode, pc)[0]
                q = bytecode[pc+2:pc+2+qlen].decode("utf-8", errors="replace")
                dest = bytecode[pc+2+qlen]
                lines.append(f"{len(lines):04d}: MEMQ '{q[:40]}' -> reg{dest}")
                pc += 3 + qlen

            elif op == OpCode.LLM:
                plen = struct.unpack_from("<H", bytecode, pc)[0]
                p = bytecode[pc+2:pc+2+plen].decode("utf-8", errors="replace")
                dest = bytecode[pc+2+plen]
                lines.append(f"{len(lines):04d}: LLM '{p[:40]}...' -> reg{dest}")
                pc += 3 + plen

            elif op == OpCode.CAP:
                clen = struct.unpack_from("<H", bytecode, pc)[0]
                c = bytecode[pc+2:pc+2+clen].decode("utf-8", errors="replace")
                lines.append(f"{len(lines):04d}: CAP '{c}'")
                pc += 2 + clen

            elif op == OpCode.RETURN:
                reg = bytecode[pc]
                lines.append(f"{len(lines):04d}: RETURN reg{reg}")
                pc += 1

            elif op == OpCode.NOOP:
                lines.append(f"{len(lines):04d}: NOOP")

            else:
                lines.append(f"{len(lines):04d}: {name}")

        return lines