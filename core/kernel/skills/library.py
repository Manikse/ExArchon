"""
ExArchon Skill Library — Muscle Memory Layer.
SQLite-based skill storage with keyword retrieval.
Optional: sentence-transformers embeddings.
"""
import sqlite3
import json
import re
import hashlib
import time
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from contextlib import contextmanager


@dataclass
class ExecutionStep:
    tool: str
    action_input: str


@dataclass
class Skill:
    """Скомпільована навичка."""
    skill_id: str
    name: str
    input_pattern: str          # Текст, за яким навчалися
    keywords: List[str]         # Ключові слова для пошуку
    execution_graph: List[ExecutionStep]  # Що виконувати
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
    Muscle Memory для ExArchon.
    Зберігає успішні ReAct traces як швидкі навички.
    """

    def __init__(self, db_path: str = "./kernel_workspace/skills.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    input_pattern TEXT NOT NULL,
                    keywords TEXT NOT NULL,
                    execution_graph TEXT NOT NULL,
                    success_rate REAL DEFAULT 1.0,
                    usage_count INTEGER DEFAULT 0,
                    avg_time_ms REAL DEFAULT 0.0,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_keywords ON skills(keywords)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_success ON skills(success_rate DESC, usage_count DESC)
            """)

    def add_skill(self, skill: Skill) -> bool:
        """Додає нову навичку в бібліотеку."""
        try:
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO skills 
                    (skill_id, name, input_pattern, keywords, execution_graph,
                     success_rate, usage_count, avg_time_ms, created_at, last_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(skill_id) DO UPDATE SET
                        success_rate = excluded.success_rate,
                        usage_count = excluded.usage_count,
                        avg_time_ms = excluded.avg_time_ms,
                        last_used = excluded.last_used
                """, (
                    skill.skill_id,
                    skill.name,
                    skill.input_pattern,
                    json.dumps(skill.keywords),
                    json.dumps([{"tool": s.tool, "action_input": s.action_input} for s in skill.execution_graph]),
                    skill.success_rate,
                    skill.usage_count,
                    skill.avg_time_ms,
                    skill.created_at,
                    skill.last_used
                ))
            return True
        except Exception as e:
            print(f"[SkillLibrary] Error adding skill: {e}")
            return False

    def find_skill(self, user_input: str, min_score: float = 0.6) -> Optional[Skill]:
        """
        Шукає найкращу навичку за keywords.
        Повертає Skill, якщо score >= min_score.
        """
        query_words = set(self._tokenize(user_input))
        if not query_words:
            return None

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM skills ORDER BY success_rate DESC, usage_count DESC
            """).fetchall()

        best_skill = None
        best_score = 0.0

        for row in rows:
            skill_keywords = set(json.loads(row["keywords"]))
            if not skill_keywords:
                continue

            # Jaccard similarity
            intersection = query_words & skill_keywords
            union = query_words | skill_keywords
            score = len(intersection) / len(union) if union else 0.0

            # Бонус за success_rate
            score *= (0.5 + 0.5 * row["success_rate"])

            if score > best_score:
                best_score = score
                best_skill = row

        if best_skill and best_score >= min_score:
            return self._row_to_skill(best_skill)

        return None

    def record_usage(self, skill_id: str, success: bool, execution_time_ms: float):
        """Оновлює статистику після використання навички."""
        with self._connect() as conn:
            row = conn.execute(
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

            conn.execute("""
                UPDATE skills SET
                    usage_count = ?,
                    success_rate = ?,
                    avg_time_ms = ?,
                    last_used = ?
                WHERE skill_id = ?
            """, (new_count, new_rate, new_time, time.time(), skill_id))

    def get_all_skills(self) -> List[Skill]:
        """Повертає всі навички."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM skills ORDER BY usage_count DESC").fetchall()
            return [self._row_to_skill(r) for r in rows]

    def delete_skill(self, skill_id: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM skills WHERE skill_id = ?", (skill_id,))

    def get_stats(self) -> Dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            avg_rate = conn.execute("SELECT AVG(success_rate) FROM skills").fetchone()[0] or 0.0
            total_uses = conn.execute("SELECT SUM(usage_count) FROM skills").fetchone()[0] or 0
            return {
                "total_skills": total,
                "avg_success_rate": round(avg_rate, 3),
                "total_invocations": total_uses,
                "db_path": self.db_path
            }

    # ============ INTERNALS ============

    def _tokenize(self, text: str) -> List[str]:
        """Проста токенізація для keyword search."""
        import re
        text = text.lower()
        words = re.findall(r"\b[a-zа-яіїєґ0-9]{3,}\b", text)
        # Фільтруємо стоп-слова
        stop = {"the", "and", "you", "що", "як", "це", "тут", "для", "але", "not", "can", "use"}
        return [w for w in words if w not in stop]

    def _row_to_skill(self, row: sqlite3.Row) -> Skill:
        graph_raw = json.loads(row["execution_graph"])
        graph = [ExecutionStep(s["tool"], s["action_input"]) for s in graph_raw]
        return Skill(
            skill_id=row["skill_id"],
            name=row["name"],
            input_pattern=row["input_pattern"],
            keywords=json.loads(row["keywords"]),
            execution_graph=graph,
            success_rate=row["success_rate"],
            usage_count=row["usage_count"],
            avg_time_ms=row["avg_time_ms"],
            created_at=row["created_at"],
            last_used=row["last_used"]
        )

    @staticmethod
    def from_trace(trace_id: str, user_input: str, trace_steps: List[Dict]) -> Skill:
        """
        Компілює ReAct trace в Skill.
        trace_steps: [{"tool": ..., "action_input": ...}, ...]
        """
        import hashlib
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
            execution_graph=graph
        )