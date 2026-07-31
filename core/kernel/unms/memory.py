"""
UNMS — Unified Neural Memory System v2.0
Персистентна гібридна пам'ять із SQLite + FTS + Importance-based retention.
"""
import sqlite3
import logging
import json
import hashlib
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from contextlib import contextmanager


@dataclass
class MemoryEntry:
    """Структурований запис пам'яті."""
    session_id: str
    role: str
    content: str
    importance: int = 5
    timestamp: float = 0.0
    metadata: Optional[Dict] = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class UNMSController:
    """
    Unified Neural Memory System (UNMS) v2.

    Архітектура:
    - SQLite backend (персистентність)
    - Full-Text Search (FTS5) для релевантного retrieval
    - Hybrid context: short-term (останні повідомлення) + long-term (ретривлені)
    - Importance-based retention
    - Automatic summarization trigger
    """

    def __init__(
        self,
        db_path: str = "./kernel_workspace/unms.db",
        max_short_term: int = 10,
        max_long_term_retrieval: int = 5,
        importance_threshold: int = 7,
        session_ttl_hours: int = 168,  # 7 днів
        enable_fts: bool = True
    ):
        self.db_path = db_path
        self.max_short_term = max_short_term
        self.max_long_term_retrieval = max_long_term_retrieval
        self.importance_threshold = importance_threshold
        self.session_ttl_hours = session_ttl_hours
        self.enable_fts = enable_fts

        # Забезпечуємо директорію
        import os
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

        self._init_db()

    # ============ DATABASE LAYER ============

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
            # Основна таблиця взаємодій
            conn.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER DEFAULT 5,
                    timestamp REAL NOT NULL,
                    metadata TEXT,
                    summary_trigger INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_time 
                ON interactions(session_id, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_importance 
                ON interactions(importance DESC, timestamp DESC)
            """)

            # Таблиця summary для сесій
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    last_updated REAL NOT NULL
                )
            """)

            # FTS5 для семантичного пошуку
            if self.enable_fts:
                try:
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS interactions_fts USING fts5(
                            content,
                            session_id UNINDEXED
                        )
                    """)
                    # Тригер для синхронізації FTS
                    conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS interactions_fts_insert
                        AFTER INSERT ON interactions
                        BEGIN
                            INSERT INTO interactions_fts(rowid, content, session_id)
                            VALUES (new.id, new.content, new.session_id);
                        END
                    """)
                    conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS interactions_fts_delete
                        AFTER DELETE ON interactions
                        BEGIN
                            INSERT INTO interactions_fts(interactions_fts, rowid, content, session_id)
                            VALUES ('delete', old.id, old.content, old.session_id);
                        END
                    """)
                except sqlite3.OperationalError as e:
                    # FTS5 може бути недоступний у деяких збірках Python
                    self.enable_fts = False
                    self.logger = logging.getLogger("UNMS")
                    self.logger.warning(f"FTS5 not available ({e}), falling back to standard search.")

    # ============ CORE OPERATIONS ============

    def add_interaction(
        self,
        session_id: str,
        user_query: str,
        kernel_response: str,
        user_importance: int = 5,
        response_importance: int = 5,
        metadata: Optional[Dict] = None
    ):
        """
        Записує взаємодію в персистентну пам'ять.
        Автоматично тригерить summary, якщо сесія стала занадто довгою.
        """
        meta_json = json.dumps(metadata) if metadata else None
        ts = time.time()

        with self._connect() as conn:
            # User message
            conn.execute("""
                INSERT INTO interactions (session_id, role, content, importance, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, "user", user_query, user_importance, ts, meta_json))

            # Assistant message
            conn.execute("""
                INSERT INTO interactions (session_id, role, content, importance, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, "assistant", kernel_response, response_importance, ts, meta_json))

        # Перевіряємо, чи треба summary
        self._maybe_summarize(session_id)

    def get_context_string(self, session_id: str, query: Optional[str] = None) -> str:
        """
        Формує контекст для LLM із:
        1. Summary сесії (якщо є)
        2. Релевантних long-term повідомлень (через FTS або importance)
        3. Останніх short-term повідомлень
        """
        parts = []

        # 1. Summary
        summary = self._get_summary(session_id)
        if summary:
            parts.append(f"[SESSION SUMMARY]\n{summary}\n")

        # 2. Long-term retrieval (релевантні повідомлення)
        if query and self.enable_fts:
            relevant = self._search_relevant(session_id, query, self.max_long_term_retrieval)
            if relevant:
                parts.append("[RELEVANT MEMORY]\n" + "\n".join(relevant) + "\n")

        # Якщо query не передано — беремо найважливіші
        elif not query:
            important = self._get_important_messages(session_id, self.max_long_term_retrieval)
            if important:
                parts.append("[KEY MEMORY]\n" + "\n".join(important) + "\n")

        # 3. Short-term (останні повідомлення)
        recent = self._get_recent_messages(session_id, self.max_short_term)
        if recent:
            parts.append("[RECENT CONTEXT]\n" + "\n".join(recent))

        if not parts:
            return "No previous conversation history."

        return "\n\n".join(parts)

    def get_session_history(self, session_id: str, limit: int = 100) -> List[Dict]:
        """Повертає повну історію сесії як список dict."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT role, content, importance, timestamp, metadata
                FROM interactions
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "importance": row["importance"],
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None
                }
                for row in rows
            ]

    def update_importance(self, interaction_id: int, new_importance: int):
        """Дозволяє Kernel або користувачу позначити повідомлення як важливе."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE interactions SET importance = ? WHERE id = ?",
                (new_importance, interaction_id)
            )

    def delete_session(self, session_id: str):
        """Повне видалення сесії."""
        with self._connect() as conn:
            conn.execute("DELETE FROM interactions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM session_summaries WHERE session_id = ?", (session_id,))

    def cleanup_stale_sessions(self):
        """Видаляє сесії старші за TTL."""
        cutoff = time.time() - (self.session_ttl_hours * 3600)
        with self._connect() as conn:
            conn.execute("DELETE FROM interactions WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM session_summaries WHERE last_updated < ?", (cutoff,))

    def get_all_sessions(self) -> List[str]:
        """Список усіх активних session_id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT session_id FROM interactions ORDER BY session_id"
            ).fetchall()
            return [row["session_id"] for row in rows]

    # ============ INTERNAL HELPERS ============

    def _get_recent_messages(self, session_id: str, limit: int) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT role, content FROM interactions
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            lines = []
            for row in reversed(rows):  # Хронологічний порядок
                speaker = "Founder" if row["role"] == "user" else "ExArchon"
                lines.append(f"{speaker}: {row['content']}")
            return lines

    def _get_important_messages(self, session_id: str, limit: int) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT role, content, importance FROM interactions
                WHERE session_id = ? AND importance >= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, self.importance_threshold, limit)).fetchall()

            lines = []
            for row in rows:
                speaker = "Founder" if row["role"] == "user" else "ExArchon"
                lines.append(f"{speaker} [importance:{row['importance']}]: {row['content']}")
            return lines

    def _search_relevant(self, session_id: str, query: str, limit: int) -> List[str]:
        """FTS-based semantic retrieval."""
        if not self.enable_fts:
            return []

        # Екрануємо спецсимволи FTS
        safe_query = query.replace("'", "''").replace('"', '""')

        with self._connect() as conn:
            try:
                rows = conn.execute("""
                    SELECT i.role, i.content, rank
                    FROM interactions_fts fts
                    JOIN interactions i ON i.id = fts.rowid
                    WHERE interactions_fts MATCH ? AND i.session_id = ?
                    ORDER BY rank
                    LIMIT ?
                """, (safe_query, session_id, limit)).fetchall()

                lines = []
                for row in rows:
                    speaker = "Founder" if row["role"] == "user" else "ExArchon"
                    lines.append(f"{speaker}: {row['content']}")
                return lines
            except sqlite3.OperationalError:
                # FTS може бути недоступний на деяких збірках Python
                return []

    def _get_summary(self, session_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary FROM session_summaries WHERE session_id = ?",
                (session_id,)
            ).fetchone()
            return row["summary"] if row else None

    def _maybe_summarize(self, session_id: str):
        """
        Тригерить summary, якщо в сесії > 50 повідомлень.
        У реальному KernelRuntime це мало б викликати LLM для summary.
        Тут — placeholder, який можна розширити.
        """
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0]

            # Якщо > 50 повідомлень і ще немає summary — позначаємо для summary
            if count > 50:
                existing = conn.execute(
                    "SELECT 1 FROM session_summaries WHERE session_id = ?",
                    (session_id,)
                ).fetchone()
                if not existing:
                    conn.execute("""
                        INSERT INTO session_summaries (session_id, summary, message_count, last_updated)
                        VALUES (?, ?, ?, ?)
                    """, (session_id, "[PENDING SUMMARY]", count, time.time()))

    def set_summary(self, session_id: str, summary_text: str):
        """Викликається KernelRuntime після LLM-summary."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO session_summaries (session_id, summary, message_count, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    message_count = excluded.message_count,
                    last_updated = excluded.last_updated
            """, (session_id, summary_text, 0, time.time()))

    def get_stats(self) -> Dict:
        """Статистика пам'яті (для моніторингу / UI)."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM interactions").fetchone()[0]
            avg_importance = conn.execute("SELECT AVG(importance) FROM interactions").fetchone()[0]
            return {
                "total_interactions": total,
                "active_sessions": sessions,
                "avg_importance": round(avg_importance or 0, 2),
                "db_path": self.db_path
            }