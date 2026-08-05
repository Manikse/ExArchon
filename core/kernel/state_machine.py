"""
kernel/state_machine.py
Формальна state machine ExArchon з Write-Ahead Log (WAL) та Crash Recovery.

St = [Mt, Ct, Et] — immutable state vector.
Кожна transition записується у WAL ПЕРЕД застосуванням.
При старті kernel — replay WAL для відновлення стану.

Аналогія з Raphael: це як "чорновик дій".
Жодна дія не вважається завершеною, поки не записана у журнал.
"""
from __future__ import annotations
import time
import hashlib
import json
import os
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List


class State(Enum):
    BOOT = auto()
    IDLE = auto()
    REFLEX = auto()       # System 0 — hardcoded, <1ms
    MUSCLE = auto()       # System 1 — compiled skills, <50ms
    COGNITIVE = auto()    # System 2 — LLM reasoning, 3-10s
    RECOVERY = auto()     # FDIR — Fault Detection, Isolation, Recovery
    SAFE = auto()         # Safe mode — мінімальна функціональність
    SHUTDOWN = auto()


class TransitionError(Exception):
    pass


class StateRecoveryError(Exception):
    """Помилка відновлення стану з WAL."""
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

    @classmethod
    def from_dict(cls, d: Dict) -> "StateVector":
        return cls(
            memory_hash=d["memory_hash"],
            cognitive_node=d["cognitive_node"],
            env_fingerprint=d["env_fingerprint"],
            timestamp_ns=d.get("timestamp_ns", time.monotonic_ns()),
            generation=d.get("generation", 0),
        )


@dataclass
class Action:
    op: str  # "READ" | "WRITE" | "EXEC" | "BRANCH" | "WAIT" | "NOOP"
    target: str
    payload: Any = None
    timeout_ms: int = 5000
    required_caps: tuple = field(default_factory=tuple)
    source_agent: str = "kernel"


@dataclass
class JournalEntry:
    """Запис у Write-Ahead Log."""
    entry_type: str  # "BEGIN", "TRANSITION", "CHECKPOINT", "COMMIT"
    prev_state: Optional[str]
    new_state: Optional[str]
    prev_vector_hash: Optional[str]
    new_vector_hash: Optional[str]
    action: Optional[Dict]
    timestamp_ns: int
    checksum: str

    def compute_checksum(self) -> str:
        data = f"{self.entry_type}:{self.prev_state}:{self.new_state}:{self.prev_vector_hash}:{self.new_vector_hash}:{self.timestamp_ns}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def is_valid(self) -> bool:
        return self.checksum == self.compute_checksum()

    def to_dict(self) -> Dict:
        return {
            "entry_type": self.entry_type,
            "prev_state": self.prev_state,
            "new_state": self.new_state,
            "prev_vector_hash": self.prev_vector_hash,
            "new_vector_hash": self.new_vector_hash,
            "action": self.action,
            "timestamp_ns": self.timestamp_ns,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "JournalEntry":
        return cls(
            entry_type=d["entry_type"],
            prev_state=d.get("prev_state"),
            new_state=d.get("new_state"),
            prev_vector_hash=d.get("prev_vector_hash"),
            new_vector_hash=d.get("new_vector_hash"),
            action=d.get("action"),
            timestamp_ns=d["timestamp_ns"],
            checksum=d["checksum"],
        )

    @classmethod
    def begin_transition(
        cls,
        prev_state: str,
        new_state: str,
        prev_vector_hash: str,
        action: Optional[Action] = None,
    ) -> "JournalEntry":
        entry = cls(
            entry_type="BEGIN",
            prev_state=prev_state,
            new_state=new_state,
            prev_vector_hash=prev_vector_hash,
            new_vector_hash=None,
            action=action.to_dict() if action else None,
            timestamp_ns=time.monotonic_ns(),
            checksum="",
        )
        object.__setattr__(entry, 'checksum', entry.compute_checksum())
        return entry

    @classmethod
    def commit_transition(
        cls,
        new_state: str,
        new_vector_hash: str,
    ) -> "JournalEntry":
        entry = cls(
            entry_type="COMMIT",
            prev_state=None,
            new_state=new_state,
            prev_vector_hash=None,
            new_vector_hash=new_vector_hash,
            action=None,
            timestamp_ns=time.monotonic_ns(),
            checksum="",
        )
        object.__setattr__(entry, 'checksum', entry.compute_checksum())
        return entry


class StateJournal:
    """
    Write-Ahead Log для State Machine.
    Append-only файл з fsync після кожного запису.
    """

    def __init__(self, journal_path: str = "./kernel_workspace/state.journal"):
        self.journal_path = journal_path
        os.makedirs(os.path.dirname(journal_path) or ".", exist_ok=True)
        self._file = open(journal_path, "a+b")
        self._ensure_fsync()

    def _ensure_fsync(self):
        """Гарантує, що запис дійсно на диску."""
        self._file.flush()
        os.fsync(self._file.fileno())

    def write(self, entry: JournalEntry):
        """Записує entry у WAL з fsync."""
        line = json.dumps(entry.to_dict()) + "\n"
        self._file.write(line.encode("utf-8"))
        self._ensure_fsync()

    def replay(self) -> List[JournalEntry]:
        """
        Відтворює журнал з диска.
        Повертає лише валідні entries.
        """
        entries = []
        self._file.seek(0)
        for line in self._file:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entry = JournalEntry.from_dict(d)
                if entry.is_valid():
                    entries.append(entry)
                else:
                    print(f"[StateJournal] CORRUPTED ENTRY DETECTED, skipping: {d}")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[StateJournal] UNREADABLE ENTRY: {e}")
                continue
        return entries

    def checkpoint(self, state: State, vector: StateVector):
        """Створює checkpoint — компактний запис поточного стану."""
        entry = JournalEntry(
            entry_type="CHECKPOINT",
            prev_state=None,
            new_state=state.name,
            prev_vector_hash=None,
            new_vector_hash=vector.id,
            action=vector.to_dict(),
            timestamp_ns=time.monotonic_ns(),
            checksum="",
        )
        object.__setattr__(entry, 'checksum', entry.compute_checksum())
        self.write(entry)

    def truncate(self):
        """Очищує журнал після успішного checkpoint (оптимізація)."""
        self._file.close()
        with open(self.journal_path, "w") as f:
            pass
        self._file = open(self.journal_path, "a+b")

    def close(self):
        self._file.close()


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

    def __init__(self, journal_path: str = "./kernel_workspace/state.journal"):
        self._state = State.BOOT
        self._state_vector = StateVector(
            memory_hash="init",
            cognitive_node="boot",
            env_fingerprint="init",
            generation=0,
        )
        self._validators: List[Callable] = []
        self._transition_hooks: List[Callable] = []

        # WAL journal
        self._journal = StateJournal(journal_path)
        self._history: List[StateVector] = []
        self._max_history = 1000

        # Recovery при старті
        self._recover()

    def _recover(self):
        """
        Відновлює стан з WAL при ініціалізації.
        Шукає останній CHECKPOINT або пару BEGIN+COMMIT.
        """
        entries = self._journal.replay()
        if not entries:
            print("[StateMachine] No journal found. Starting fresh from BOOT.")
            return

        # Шукаємо останній валідний стан
        last_checkpoint = None
        last_commit = None
        pending_begin = None

        for entry in entries:
            if entry.entry_type == "CHECKPOINT":
                last_checkpoint = entry
                pending_begin = None
            elif entry.entry_type == "BEGIN":
                pending_begin = entry
            elif entry.entry_type == "COMMIT":
                if pending_begin and pending_begin.new_state == entry.new_state:
                    last_commit = entry
                    pending_begin = None
                else:
                    # Orphaned COMMIT — ignore
                    pass

        # Відновлюємо стан
        if last_checkpoint:
            try:
                vector_data = last_checkpoint.action
                if vector_data:
                    self._state_vector = StateVector.from_dict(vector_data)
                self._state = State[last_checkpoint.new_state]
                print(f"[StateMachine] Recovered from CHECKPOINT: {self._state.name}, gen={self._state_vector.generation}")
            except (KeyError, ValueError) as e:
                print(f"[StateMachine] CHECKPOINT corrupted: {e}. Starting fresh.")
                self._journal.truncate()
                return

        if last_commit:
            try:
                self._state = State[last_commit.new_state]
                print(f"[StateMachine] Recovered from COMMIT: {self._state.name}")
            except KeyError:
                print(f"[StateMachine] COMMIT corrupted. Rolling back to checkpoint.")

        # Якщо є pending BEGIN без COMMIT — це crash посередині transition
        if pending_begin:
            print(f"[StateMachine] INCOMPLETE TRANSITION detected (BEGIN without COMMIT). Rolling back to {pending_begin.prev_state}.")
            try:
                self._state = State[pending_begin.prev_state]
            except KeyError:
                pass

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

        # === WAL: записуємо BEGIN ПЕРЕД зміною стану ===
        begin_entry = JournalEntry.begin_transition(
            prev_state=self._state.name,
            new_state=to_state.name,
            prev_vector_hash=self._state_vector.id,
            action=action,
        )
        self._journal.write(begin_entry)

        # Зберігаємо у in-memory history
        self._history.append(self._state_vector)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        old_state = self._state
        self._state = to_state
        self._state_vector = new_vector or self._state_vector.with_transition()

        # === WAL: записуємо COMMIT ПІСЛЯ зміни стану ===
        commit_entry = JournalEntry.commit_transition(
            new_state=to_state.name,
            new_vector_hash=self._state_vector.id,
        )
        self._journal.write(commit_entry)

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
        # Після rollback робимо checkpoint
        self._journal.checkpoint(self._state, self._state_vector)
        return self._state_vector

    def checkpoint(self) -> str:
        """Створює checkpoint та очищує старий журнал."""
        self._journal.checkpoint(self._state, self._state_vector)
        self._journal.truncate()
        return json.dumps({
            "state": self._state.name,
            "vector": self._state_vector.to_dict(),
        })

    def get_journal_stats(self) -> Dict:
        entries = self._journal.replay()
        return {
            "total_entries": len(entries),
            "checkpoints": len([e for e in entries if e.entry_type == "CHECKPOINT"]),
            "transitions": len([e for e in entries if e.entry_type == "BEGIN"]),
            "commits": len([e for e in entries if e.entry_type == "COMMIT"]),
            "current_state": self._state.name,
            "generation": self._state_vector.generation,
        }

    def shutdown(self):
        """Graceful shutdown — final checkpoint."""
        self._journal.checkpoint(self._state, self._state_vector)
        self._journal.close()