"""
kernel/state_machine.py
Формальна state machine ExArchon.
St = [Mt, Ct, Et] — immutable state vector.
"""
from __future__ import annotations
import time
import hashlib
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict


class State(Enum):
    BOOT = auto()
    IDLE = auto()
    REFLEX = auto()      # System 1 — hardcoded, 0ms
    MUSCLE = auto()      # System 1 — compiled skills, <50ms
    COGNITIVE = auto()   # System 2 — LLM reasoning, 3-10s
    RECOVERY = auto()    # FDIR
    SAFE = auto()        # Safe mode
    SHUTDOWN = auto()


class TransitionError(Exception):
    pass


@dataclass(frozen=True)
class StateVector:
    memory_hash: str
    cognitive_node: str
    env_fingerprint: str
    timestamp_ns: int = field(default_factory=lambda: time.monotonic_ns())
    generation: int = 0

    def __post_init__(self):
        object.__setattr__(
            self, '_hash',
            hashlib.sha256(
                f"{self.memory_hash}:{self.cognitive_node}:{self.env_fingerprint}:{self.generation}".encode()
            ).hexdigest()[:16]
        )

    @property
    def id(self) -> str:
        return getattr(self, '_hash', 'unknown')

    def with_transition(self, **kwargs) -> StateVector:
        return StateVector(
            memory_hash=kwargs.get('memory_hash', self.memory_hash),
            cognitive_node=kwargs.get('cognitive_node', self.cognitive_node),
            env_fingerprint=kwargs.get('env_fingerprint', self.env_fingerprint),
            timestamp_ns=time.monotonic_ns(),
            generation=self.generation + 1,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "memory_hash": self.memory_hash,
            "cognitive_node": self.cognitive_node,
            "env_fingerprint": self.env_fingerprint,
            "timestamp_ns": self.timestamp_ns,
            "generation": self.generation,
        }


@dataclass
class Action:
    op: str  # "READ" | "WRITE" | "EXEC" | "BRANCH" | "WAIT" | "NOOP"
    target: str
    payload: Any = None
    timeout_ms: int = 5000
    required_caps: tuple = field(default_factory=tuple)
    source_agent: str = "kernel"


class StateMachine:
    VALID_TRANSITIONS = {
        State.BOOT: {State.IDLE, State.SAFE, State.SHUTDOWN},
        State.IDLE: {State.REFLEX, State.MUSCLE, State.COGNITIVE, State.RECOVERY, State.SHUTDOWN},
        State.REFLEX: {State.IDLE, State.RECOVERY, State.SAFE},
        State.MUSCLE: {State.IDLE, State.RECOVERY, State.SAFE},
        State.COGNITIVE: {State.IDLE, State.RECOVERY, State.SAFE},
        State.RECOVERY: {State.IDLE, State.SAFE, State.SHUTDOWN},
        State.SAFE: {State.IDLE, State.RECOVERY, State.SHUTDOWN},
        State.SHUTDOWN: set(),
    }

    def __init__(self):
        self._state = State.BOOT
        self._state_vector = StateVector(memory_hash="init", cognitive_node="boot", env_fingerprint="init", generation=0)
        self._history: list[StateVector] = []
        self._validators: list[Callable] = []
        self._transition_hooks: list[Callable] = []

    @property
    def state(self) -> State:
        return self._state

    @property
    def state_vector(self) -> StateVector:
        return self._state_vector

    def register_validator(self, fn: Callable[[State, State, StateVector, Action], bool]):
        self._validators.append(fn)

    def register_transition_hook(self, fn: Callable[[State, State, StateVector], None]):
        self._transition_hooks.append(fn)

    def can_transition(self, from_state: State, to_state: State) -> bool:
        return to_state in self.VALID_TRANSITIONS.get(from_state, set())

    def transition(self, to_state: State, action: Optional[Action] = None, new_vector: Optional[StateVector] = None) -> StateVector:
        if not self.can_transition(self._state, to_state):
            raise TransitionError(f"Invalid transition: {self._state.name} → {to_state.name}")

        for validator in self._validators:
            if not validator(self._state, to_state, self._state_vector, action or Action("NOOP", "")):
                raise TransitionError(f"Validator rejected: {self._state.name} → {to_state.name}")

        self._history.append(self._state_vector)
        if len(self._history) > 1000:
            self._history.pop(0)

        old_state = self._state
        self._state = to_state
        self._state_vector = new_vector or self._state_vector.with_transition()

        for hook in self._transition_hooks:
            try:
                hook(old_state, to_state, self._state_vector)
            except Exception:
                pass

        return self._state_vector

    def rollback(self, steps: int = 1) -> StateVector:
        if steps > len(self._history):
            steps = len(self._history)
        for _ in range(steps):
            self._state_vector = self._history.pop()
        return self._state_vector

    def checkpoint(self) -> str:
        import json
        return json.dumps({
            "state": self._state.name,
            "vector": self._state_vector.to_dict(),
        })