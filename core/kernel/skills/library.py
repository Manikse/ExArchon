"""
kernel/skills/library.py
Skill Library v3 — Preconditions + Semantic Retrieval.
"""
import sqlite3
import json
import re
import hashlib
import time
import os
import asyncio
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict, field
from contextlib import contextmanager


@dataclass
class ExecutionStep:
    tool: str
    action_input: str


@dataclass
class SkillPrecondition:
    """Condition that must be met before skill execution."""
    check_type: str  # "capability", "env_var", "disk_space", "memory"
    target: str
    operator: str  # "==", "!=", ">", "<", "exists"
    value: str = ""


@dataclass
class Skill:
    skill_id: str
    name: str
    input_pattern: str
    keywords: List[str]
    execution_graph: List[ExecutionStep]
    preconditions: List[SkillPrecondition] = field(default_factory=list)
    success_rate: float = 1.0
    usage_count: int = 0
    avg_time_ms: float = 0.0
    created_at: float = 0.0
    last_used: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()


class SkillLibrary:
    """
    Muscle Memory v3 — with preconditions and semantic retrieval.
    """

    def __init__(self, db_path: str = "./kernel_workspace/skills.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_connection()
        self._init_schema()

    def _init_connection(self):
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def _init_schema(self):
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    input_pattern TEXT NOT NULL,
                    keywords TEXT NOT NULL,
                    execution_graph TEXT NOT NULL,
                    preconditions TEXT DEFAULT '[]',
                    success_rate REAL DEFAULT 1.0,
                    usage_count INTEGER DEFAULT 0,
                    avg_time_ms REAL DEFAULT 0.0,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_keywords ON skills(keywords)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_success ON skills(success_rate DESC, usage_count DESC)
            """)

    def _expand_keywords(self, words: List[str]) -> List[str]:
        """Semantic expansion via simple synonyms. Replace with WordNet/embedding later."""
        synonyms = {
            "disk": ["storage", "drive", "space", "hdd", "ssd"],
            "space": ["storage", "capacity", "free", "available"],
            "check": ["verify", "inspect", "monitor", "test"],
            "read": ["open", "view", "get", "fetch"],
            "write": ["save", "store", "create", "update"],
            "delete": ["remove", "clean", "clear", "erase"],
            "list": ["show", "display", "ls", "dir"],
            "cpu": ["processor", "load", "usage", "top"],
            "memory": ["ram", "mem", "usage"],
        }
        expanded = set(words)
        for w in words:
            for key, syns in synonyms.items():
                if w == key or w in syns:
                    expanded.add(key)
                    expanded.update(syns)
        return list(expanded)

    def add_skill(self, skill: Skill) -> bool:
        try:
            with self._conn:
                self._conn.execute("""
                    INSERT INTO skills
                    (skill_id, name, input_pattern, keywords, execution_graph, preconditions,
                     success_rate, usage_count, avg_time_ms, created_at, last_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(skill_id) DO UPDATE SET
                    success_rate = excluded.success_rate,
                    usage_count = excluded.usage_count,
                    avg_time_ms = excluded.avg_time_ms,
                    last_used = excluded.last_used
                """, (
                    skill.skill_id, skill.name, skill.input_pattern,
                    json.dumps(skill.keywords),
                    json.dumps([{"tool": s.tool, "action_input": s.action_input} for s in skill.execution_graph]),
                    json.dumps([{"check_type": p.check_type, "target": p.target, "operator": p.operator, "value": p.value} for p in skill.preconditions]),
                    skill.success_rate, skill.usage_count, skill.avg_time_ms,
                    skill.created_at, skill.last_used
                ))
            return True
        except Exception as e:
            print(f"[SkillLibrary] Error adding skill: {e}")
            return False

    def find_skill(self, user_input: str, min_score: float = 0.55) -> Optional[Skill]:
        query_words = set(self._tokenize(user_input))
        if not query_words:
            return None
        query_words = set(self._expand_keywords(list(query_words)))

        rows = self._conn.execute("""
            SELECT * FROM skills ORDER BY success_rate DESC, usage_count DESC
        """).fetchall()

        best_skill = None
        best_score = 0.0

        for row in rows:
            skill_keywords = set(json.loads(row["keywords"]))
            if not skill_keywords:
                continue

            # Jaccard + semantic overlap
            intersection = query_words & skill_keywords
            union = query_words | skill_keywords
            score = len(intersection) / len(union) if union else 0.0

            # Bonus for success rate
            score *= (0.5 + 0.5 * row["success_rate"])

            if score > best_score:
                best_score = score
                best_skill = row

        if best_skill and best_score >= min_score:
            return self._row_to_skill(best_skill)
        return None

    def check_preconditions(self, skill: Skill, context: Dict) -> Tuple[bool, str]:
        """
        Check if skill preconditions are met.
        context: {"disk_usage": 85, "capabilities": [...], "env": {...}}
        """
        for pre in skill.preconditions:
            if pre.check_type == "capability":
                caps = context.get("capabilities", [])
                if pre.target not in caps:
                    return False, f"Missing capability: {pre.target}"
            elif pre.check_type == "disk_space":
                usage = context.get("disk_usage", 0)
                try:
                    threshold = int(pre.value)
                    if pre.operator == ">" and usage > threshold:
                        pass  # Condition met (disk usage IS high)
                    elif pre.operator == "<" and usage < threshold:
                        pass
                    else:
                        return False, f"Disk precondition not met: {usage} {pre.operator} {threshold}"
                except ValueError:
                    pass
            # Add more check_types as needed
        return True, ""

    def record_usage(self, skill_id: str, success: bool, execution_time_ms: float):
        row = self._conn.execute(
            "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
        ).fetchone()
        if not row:
            return

        old_count = row["usage_count"]
        old_rate = row["success_rate"]
        old_time = row["avg_time_ms"]

        new_count = old_count + 1
        new_rate = (old_rate * old_count + (1.0 if success else 0.0)) / new_count
        new_time = (old_time * old_count + execution_time_ms) / new_count

        with self._conn:
            self._conn.execute("""
                UPDATE skills SET
                usage_count = ?, success_rate = ?, avg_time_ms = ?, last_used = ?
                WHERE skill_id = ?
            """, (new_count, new_rate, new_time, time.time(), skill_id))

    def get_all_skills(self) -> List[Skill]:
        rows = self._conn.execute("SELECT * FROM skills ORDER BY usage_count DESC").fetchall()
        return [self._row_to_skill(r) for r in rows]

    def delete_skill(self, skill_id: str):
        with self._conn:
            self._conn.execute("DELETE FROM skills WHERE skill_id = ?", (skill_id,))

    def get_stats(self) -> Dict:
        total = self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        avg_rate = self._conn.execute("SELECT AVG(success_rate) FROM skills").fetchone()[0] or 0.0
        total_uses = self._conn.execute("SELECT SUM(usage_count) FROM skills").fetchone()[0] or 0
        return {
            "total_skills": total,
            "avg_success_rate": round(avg_rate, 3),
            "total_invocations": total_uses,
            "db_path": self.db_path,
        }

    def _tokenize(self, text: str) -> List[str]:
        text = text.lower()
        words = re.findall(r"\b[a-zа-яіїєґ0-9]{3,}\b", text)
        stop = {"the", "and", "you", "що", "як", "це", "тут", "для", "але", "not", "can", "use"}
        return [w for w in words if w not in stop]

    def _row_to_skill(self, row: sqlite3.Row) -> Skill:
        graph_raw = json.loads(row["execution_graph"])
        graph = [ExecutionStep(s["tool"], s["action_input"]) for s in graph_raw]
        pre_raw = json.loads(row.get("preconditions", "[]"))
        preconditions = [SkillPrecondition(p["check_type"], p["target"], p["operator"], p.get("value", "")) for p in pre_raw]
        return Skill(
            skill_id=row["skill_id"],
            name=row["name"],
            input_pattern=row["input_pattern"],
            keywords=json.loads(row["keywords"]),
            execution_graph=graph,
            preconditions=preconditions,
            success_rate=row["success_rate"],
            usage_count=row["usage_count"],
            avg_time_ms=row["avg_time_ms"],
            created_at=row["created_at"],
            last_used=row["last_used"],
        )

    @staticmethod
    def from_trace(trace_id: str, user_input: str, trace_steps: List[Dict], preconditions: List[SkillPrecondition] = None) -> Skill:
        skill_id = hashlib.sha256(f"{user_input}:{time.time()}".encode()).hexdigest()[:16]
        keywords = set()
        for word in re.findall(r"\b[a-zа-яіїєґ0-9]{3,}\b", user_input.lower()):
            if len(word) > 3:
                keywords.add(word)
        graph = [ExecutionStep(s["tool"], s["action_input"]) for s in trace_steps]
        return Skill(
            skill_id=skill_id,
            name=user_input[:50],
            input_pattern=user_input,
            keywords=list(keywords),
            execution_graph=graph,
            preconditions=preconditions or [],
        )

    def close(self):
        if self._conn:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.close()
            self._conn = None