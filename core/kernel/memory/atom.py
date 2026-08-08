"""
kernel/memory/atom.py
ContextAtom model + FactExtractor for Deterministic Context Chain (DCC).
"""
import hashlib
import time
import json
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class Fact:
    """Structured fact triple: subject — predicate — object."""
    subject: str
    predicate: str
    object: str
    certainty: float = 1.0
    source: str = "extracted"

    def to_dict(self) -> Dict:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "certainty": self.certainty,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Fact":
        return cls(**d)


@dataclass
class ContextAtom:
    """
    Atomic unit of context. Linked list via prev_hash.
    Immutable after creation.
    """
    atom_id: bytes = field(default_factory=lambda: b"")
    prev_hash: Optional[bytes] = None
    timestamp_ns: int = 0
    intent_embedding: bytes = b""
    facts: List[Fact] = field(default_factory=list)
    summary: str = ""
    raw_content: str = ""
    importance: float = 0.5
    access_count: int = 0
    session_id: str = "default"

    def __post_init__(self):
        if self.timestamp_ns == 0:
            self.timestamp_ns = time.time_ns()
        if not self.atom_id:
            self._compute_hash()

    def _compute_hash(self):
        h = hashlib.blake2b(digest_size=32)
        if self.prev_hash:
            h.update(self.prev_hash)
        h.update(str(self.timestamp_ns).encode())
        h.update(self.raw_content.encode("utf-8"))
        h.update(json.dumps([f.to_dict() for f in self.facts]).encode())
        self.atom_id = h.digest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atom_id": self.atom_id.hex(),
            "prev_hash": self.prev_hash.hex() if self.prev_hash else None,
            "timestamp_ns": self.timestamp_ns,
            "intent_embedding": self.intent_embedding.hex(),
            "facts_json": json.dumps([f.to_dict() for f in self.facts]),
            "summary": self.summary,
            "raw_content": self.raw_content,
            "importance": self.importance,
            "access_count": self.access_count,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ContextAtom":
        facts_raw = json.loads(d.get("facts_json", "[]"))
        return cls(
            atom_id=bytes.fromhex(d["atom_id"]) if isinstance(d["atom_id"], str) else d["atom_id"],
            prev_hash=bytes.fromhex(d["prev_hash"]) if d.get("prev_hash") else None,
            timestamp_ns=d.get("timestamp_ns", 0),
            intent_embedding=bytes.fromhex(d["intent_embedding"]) if d.get("intent_embedding") else b"",
            facts=[Fact.from_dict(f) for f in facts_raw],
            summary=d.get("summary", ""),
            raw_content=d.get("raw_content", ""),
            importance=d.get("importance", 0.5),
            access_count=d.get("access_count", 0),
            session_id=d.get("session_id", "default"),
        )


class FactExtractor:
    """
    Extract structured facts from ReAct traces and raw text.
    Rule-based baseline. Later: tiny NER model.
    """

    PATTERNS = [
        (r"port\s*(?:is|:|=)\s*(\d+)", "server", "has_port", 1),
        (r"server\s+(?:on\s+)?port\s+(\d+)", "server", "has_port", 1),
        (r"user(?:name)?\s*(?:is|:|=)\s*(\S+)", "account", "has_username", 1),
        (r"password\s*(?:is|:|=)\s*(\S+)", "account", "has_password", 1),
        (r"host(?:name)?\s*(?:is|:|=)\s*(\S+)", "server", "has_hostname", 1),
        (r"file\s+(\S+)\s+(?:created|written|saved)", "file", "was_created", 1),
        (r"deleted\s+file\s+(\S+)", "file", "was_deleted", 1),
        (r"directory\s+(\S+)\s+created", "directory", "was_created", 1),
        (r"disk\s+usage\s+(?:is|:|=)\s*(\d+)%", "system", "disk_usage_percent", 1),
        (r"cpu\s+usage\s+(?:is|:|=)\s*(\d+)%", "system", "cpu_usage_percent", 1),
        (r"memory\s+usage\s+(?:is|:|=)\s*(\d+)%", "system", "memory_usage_percent", 1),
        (r"temperature\s+(?:is|:|=)\s*(\d+)", "system", "temperature_c", 1),
        (r"decided\s+to\s+(\S.+)", "decision", "action", 1),
        (r"conclusion:\s*(.+)", "decision", "conclusion", 1),
    ]

    @classmethod
    def extract(cls, raw_text: str) -> List[Fact]:
        facts = []
        for pattern, subject_template, predicate, group in cls.PATTERNS:
            for match in re.finditer(pattern, raw_text, re.IGNORECASE):
                obj = match.group(group).strip()
                subject = subject_template
                if subject_template == "server" and "host" in pattern:
                    subject = "server:" + obj
                facts.append(Fact(subject, predicate, obj, certainty=0.9))
        return facts

    @classmethod
    def extract_from_trace(cls, trace_steps: List[Dict]) -> List[Fact]:
        parts = []
        for s in trace_steps:
            parts.append(s.get("thought", ""))
            parts.append(s.get("action_input", ""))
            parts.append(s.get("observation", ""))
        all_text = " ".join(parts)
        return cls.extract(all_text)

    @classmethod
    def summarize(cls, raw_text: str, max_len: int = 200) -> str:
        sentences = re.split(r"[.!?\n]+", raw_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if not sentences:
            return raw_text[:max_len]

        def score(s):
            keywords = ["port", "password", "error", "failed", "success", "created", "deleted"]
            return sum(1 for w in keywords if w in s.lower())

        sentences.sort(key=score, reverse=True)
        summary = ". ".join(sentences[:3])
        return summary[:max_len]


class ContextChainBuilder:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self._last_hash: Optional[bytes] = None
        self._atom_count = 0

    def add_interaction(self, raw_content: str, trace_steps: List[Dict] = None, importance: float = 0.5) -> ContextAtom:
        facts = FactExtractor.extract_from_trace(trace_steps) if trace_steps else FactExtractor.extract(raw_content)
        summary = FactExtractor.summarize(raw_content)

        atom = ContextAtom(
            prev_hash=self._last_hash,
            facts=facts,
            summary=summary,
            raw_content=raw_content,
            importance=importance,
            session_id=self.session_id,
        )
        self._last_hash = atom.atom_id
        self._atom_count += 1
        return atom

    def get_head(self) -> Optional[bytes]:
        return self._last_hash