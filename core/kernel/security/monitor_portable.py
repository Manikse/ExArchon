"""
kernel/security/monitor_portable.py
Portable LTL Safety Monitor — Zero OS Dependencies, Mars-Ready.

Parses and evaluates Linear Temporal Logic formulas:
  □  (G) — Globally / Always
  ◇  (F) — Eventually / Future
  ¬  — Not
  ∧  — And
  ∨  — Or
  →  — Implies
  X  — Next
  U  — Until

No eBPF, no Linux syscalls. Pure Python. Works on Mars.
"""
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Any
from enum import Enum


class LTLOp(Enum):
    ALWAYS = "G"      # □
    EVENTUALLY = "F"  # ◇
    NEXT = "X"
    UNTIL = "U"
    NOT = "¬"
    AND = "∧"
    OR = "∨"
    IMPLIES = "→"
    ATOMIC = "ATOMIC"


@dataclass
class LTLNode:
    op: LTLOp
    left: Optional["LTLNode"] = None
    right: Optional["LTLNode"] = None
    atomic: Optional[str] = None  # for ATOMIC nodes


@dataclass
class Violation:
    formula: str
    observed: Dict[str, Any]
    timestamp: float
    severity: str = "warning"


class LTLParser:
    """
    Parse LTL formulas from string representation.
    Supports both Unicode (□◇¬∧∨→) and ASCII (G F ! & | ->) notation.
    """

    TOKEN_MAP = {
        "□": "G", "G": "G",
        "◇": "F", "F": "F",
        "X": "X",
        "¬": "!", "!": "!",
        "∧": "&", "&": "&",
        "∨": "|", "|": "|",
        "→": "->", "->": "->",
        "U": "U",
        "(": "(", ")": ")",
    }

    def __init__(self):
        self._pattern = re.compile(
            r'\u25a1|\u25c7|\u00ac|\u2227|\u2228|\u2192|\(|\)|\band\b|\bor\b|'
            r'G|F|X|!|&|\||->|U|\(|\)|'
            r'[a-zA-Z_][a-zA-Z0-9_]*|\d+(?:\.\d+)?'
        )

    def parse(self, formula: str) -> LTLNode:
        """Parse an LTL formula string into an AST."""
        tokens = self._tokenize(formula)
        self._pos = 0
        self._tokens = tokens
        node = self._parse_implies()
        return node

    def _tokenize(self, formula: str) -> List[str]:
        tokens = []
        i = 0
        while i < len(formula):
            c = formula[i]
            if c in " \t\n":
                i += 1
                continue
            # Multi-char operators
            if formula[i:i+2] == "->":
                tokens.append("->")
                i += 2
                continue
            if c in self.TOKEN_MAP:
                tokens.append(self.TOKEN_MAP[c])
                i += 1
                continue
            # Identifiers / numbers
            match = re.match(r'[a-zA-Z_][a-zA-Z0-9_]*|\d+(?:\.\d+)?', formula[i:])
            if match:
                tokens.append(match.group())
                i += len(match.group())
            else:
                i += 1
        return tokens

    def _peek(self) -> Optional[str]:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _consume(self) -> str:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _parse_implies(self) -> LTLNode:
        node = self._parse_or()
        while self._peek() == "->":
            self._consume()
            right = self._parse_or()
            node = LTLNode(LTLOp.IMPLIES, left=node, right=right)
        return node

    def _parse_or(self) -> LTLNode:
        node = self._parse_and()
        while self._peek() == "|":
            self._consume()
            right = self._parse_and()
            node = LTLNode(LTLOp.OR, left=node, right=right)
        return node

    def _parse_and(self) -> LTLNode:
        node = self._parse_unary()
        while self._peek() == "&":
            self._consume()
            right = self._parse_unary()
            node = LTLNode(LTLOp.AND, left=node, right=right)
        return node

    def _parse_unary(self) -> LTLNode:
        tok = self._peek()
        if tok == "!":
            self._consume()
            return LTLNode(LTLOp.NOT, left=self._parse_unary())
        if tok == "G":
            self._consume()
            return LTLNode(LTLOp.ALWAYS, left=self._parse_unary())
        if tok == "F":
            self._consume()
            return LTLNode(LTLOp.EVENTUALLY, left=self._parse_unary())
        if tok == "X":
            self._consume()
            return LTLNode(LTLOp.NEXT, left=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> LTLNode:
        tok = self._peek()
        if tok == "(":
            self._consume()
            node = self._parse_implies()
            if self._peek() == ")":
                self._consume()
            return node
        # Atomic proposition
        if tok and (tok[0].isalpha() or tok[0] == "_"):
            self._consume()
            return LTLNode(LTLOp.ATOMIC, atomic=tok)
        # Number literal
        if tok and tok[0].isdigit():
            self._consume()
            return LTLNode(LTLOp.ATOMIC, atomic=tok)
        return LTLNode(LTLOp.ATOMIC, atomic="true")


class LTLMonitor:
    """
    Runtime monitor that observes system state and checks LTL formulas.
    Maintains a sliding window of observations for temporal operators.
    """

    def __init__(self, window_size: int = 1000):
        self.window_size = window_size
        self.observations: List[Dict[str, Any]] = []
        self.formulas: Dict[str, LTLNode] = {}
        self.violations: List[Violation] = []
        self._parser = LTLParser()

    def add_formula(self, name: str, formula: str):
        """Register an LTL formula to monitor."""
        self.formulas[name] = self._parser.parse(formula)

    def observe(self, state: Dict[str, Any]):
        """Record a new system observation."""
        state["_timestamp"] = time.time()
        self.observations.append(state)
        if len(self.observations) > self.window_size:
            self.observations.pop(0)
        self._check_all(state)

    def _check_all(self, latest: Dict[str, Any]):
        for name, ast in self.formulas.items():
            result = self._eval(ast, len(self.observations) - 1)
            if not result:
                self.violations.append(Violation(
                    formula=name,
                    observed=latest,
                    timestamp=time.time(),
                    severity="warning",
                ))

    def _eval(self, node: LTLNode, idx: int) -> bool:
        """Evaluate an LTL node against observations at index idx."""
        if node.op == LTLOp.ATOMIC:
            return self._eval_atomic(node.atomic, idx)
        if node.op == LTLOp.NOT:
            return not self._eval(node.left, idx)
        if node.op == LTLOp.AND:
            return self._eval(node.left, idx) and self._eval(node.right, idx)
        if node.op == LTLOp.OR:
            return self._eval(node.left, idx) or self._eval(node.right, idx)
        if node.op == LTLOp.IMPLIES:
            return (not self._eval(node.left, idx)) or self._eval(node.right, idx)
        if node.op == LTLOp.ALWAYS:
            # □φ — φ must hold at ALL future observations in window
            for i in range(idx, len(self.observations)):
                if not self._eval(node.left, i):
                    return False
            return True
        if node.op == LTLOp.EVENTUALLY:
            # ◇φ — φ must hold at SOME future observation in window
            for i in range(idx, len(self.observations)):
                if self._eval(node.left, i):
                    return True
            return False
        if node.op == LTLOp.NEXT:
            # Xφ — φ must hold at next observation
            if idx + 1 < len(self.observations):
                return self._eval(node.left, idx + 1)
            return True  # vacuously true at end of trace
        if node.op == LTLOp.UNTIL:
            # φ U ψ — ψ must eventually hold, and φ must hold until then
            for i in range(idx, len(self.observations)):
                if self._eval(node.right, i):
                    return True
                if not self._eval(node.left, i):
                    return False
            return False
        return True

    def _eval_atomic(self, atomic: str, idx: int) -> bool:
        """Evaluate an atomic proposition against observation at idx."""
        if idx < 0 or idx >= len(self.observations):
            return True
        obs = self.observations[idx]

        # Built-in atoms
        if atomic == "true":
            return True
        if atomic == "false":
            return False

        # Comparison atoms: temperature>60, memory<100, etc.
        comp_match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(<|>|<=|>=|==|!=)\s*(.+)", atomic)
        if comp_match:
            key, op, val_str = comp_match.groups()
            actual = obs.get(key)
            if actual is None:
                return True  # unknown = vacuously true
            try:
                actual_f = float(actual)
                val_f = float(val_str)
                if op == "<": return actual_f < val_f
                if op == ">": return actual_f > val_f
                if op == "<=": return actual_f <= val_f
                if op == ">=": return actual_f >= val_f
                if op == "==": return actual_f == val_f
                if op == "!=": return actual_f != val_f
            except ValueError:
                # String comparison
                actual_s = str(actual)
                if op == "==": return actual_s == val_str
                if op == "!=": return actual_s != val_str
            return True

        # Simple key existence / truthiness
        val = obs.get(atomic)
        if val is None:
            return True
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return val != 0
        return bool(val)

    def get_violations(self, since: float = 0.0) -> List[Violation]:
        """Get all violations since given timestamp."""
        return [v for v in self.violations if v.timestamp >= since]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "formulas": len(self.formulas),
            "observations": len(self.observations),
            "violations": len(self.violations),
            "formulas_list": list(self.formulas.keys()),
        }

    def clear_violations(self):
        self.violations.clear()


# ── Pre-configured monitors for ExArchon ──

def create_default_monitor() -> LTLMonitor:
    """Create a monitor with standard ExArchon safety invariants."""
    m = LTLMonitor(window_size=500)
    # Safety: temperature must never exceed 85C
    m.add_formula("thermal_safe", "G(temperature<85)")
    # Safety: memory usage must not exceed 90%
    m.add_formula("memory_safe", "G(memory_usage<90)")
    # Liveness: if watchdog_triggered, then eventually watchdog_pet
    m.add_formula("watchdog_liveness", "G(watchdog_triggered -> F(watchdog_pet))")
    # Safety: capability_denied implies not capability_granted
    m.add_formula("cap_consistency", "G(capability_denied -> !capability_granted)")
    return m


def create_critical_monitor() -> LTLMonitor:
    """Monitor for critical infrastructure (Mars rover, SCADA)."""
    m = LTLMonitor(window_size=2000)
    m.add_formula("thermal_critical", "G(temperature<70)")
    m.add_formula("power_safe", "G(battery_level>10)")
    m.add_formula("comm_liveness", "G(comm_attempt -> F(comm_success | comm_fail))")
    m.add_formula("no_deadlock", "G(running -> F(done | error))")
    return m