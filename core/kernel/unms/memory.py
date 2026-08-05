"""
kernel/unms/memory.py
Unified Neural Memory System v3 — Persistent Connection Edition.

Зміни від v2:
1. PERSISTENT CONNECTION замість open/close на кожну операцію.
2. WAL mode (PRAGMA journal_mode=WAL) для конкурентних writes.
3. Connection pool для thread-safety через asyncio.Lock.
4. Graceful cleanup при shutdown.
5. FTS5 + importance-based retention.

Аналогія з Raphael: це "пам'ять Рафаєля" — постійно активна,
не втрачається при перезавантаженні, оптимізована для швидкого доступу.
"""
import sqlite3
import json
import time
import os
import asyncio
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class Interaction:
    session_id: str
    user_input: str
    response: str
    user_importance: int
    response_importance: int
    timestamp: float


class UNMSController:
    """
    UNMS v3 — Persistent SQLite connection with WAL mode.
    """

    def __init__(self, db_path: str = "./kernel_workspace/unms.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        # Persistent connection
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self._closed = False

        self._init_connection()
        self._init_schema()

    def _init_connection(self):
        """Відкриває persistent connection з WAL mode."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # WAL mode — дозволяє читати під час запису, критично для kernel
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # Баланс швидкості/надійності
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        self._conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        self._conn.commit()
        print(f"[UNMS] Persistent connection opened. WAL mode active. DB: {self.db_path}")

    def _init_schema(self):
        """Ініціалізує схему БД."""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    response TEXT NOT NULL,
                    user_importance INTEGER DEFAULT 5,
                    response_importance INTEGER DEFAULT 5,
                    timestamp REAL NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session ON interactions(session_id)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON interactions(timestamp)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_importance ON interactions(user_importance DESC, response_importance DESC)
            """)
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS interactions_fts USING fts5(
                    user_input, response,
                    content=interactions,
                    content_rowid=id
                )
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS interactions_ai AFTER INSERT ON interactions BEGIN
                    INSERT INTO interactions_fts(rowid, user_input, response)
                    VALUES (new.id, new.user_input, new.response);
                END
            """)
            self._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS interactions_ad AFTER DELETE ON interactions BEGIN
                    INSERT INTO interactions_fts(interactions_fts, rowid, user_input, response)
                    VALUES ('delete', old.id, old.user_input, old.response);
                END
            """)

    async def add_interaction(
        self,
        session_id: str,
        user_input: str,
        response: str,
        user_importance: int = 5,
        response_importance: int = 5,
    ):
        """Додає взаємодію. Thread-safe через asyncio.Lock."""
        if self._closed:
            raise RuntimeError("UNMS controller is closed")

        async with self._lock:
            try:
                with self._conn:
                    self._conn.execute("""
                        INSERT INTO interactions
                        (session_id, user_input, response, user_importance, response_importance, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, user_input, response, user_importance, response_importance, time.time()))
            except sqlite3.OperationalError as e:
                print(f"[UNMS] Write error (WAL busy?): {e}. Retrying...")
                await asyncio.sleep(0.1)
                # Retry once
                with self._conn:
                    self._conn.execute("""
                        INSERT INTO interactions
                        (session_id, user_input, response, user_importance, response_importance, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, user_input, response, user_importance, response_importance, time.time()))

    async def search_memory(
        self,
        query: str,
        session_id: Optional[str] = None,
        limit: int = 5,
        min_importance: int = 0,
    ) -> List[Dict]:
        """Пошук по пам'яті з FTS5 fallback."""
        if self._closed:
            raise RuntimeError("UNMS controller is closed")

        async with self._lock:
            # Спробуємо FTS5
            try:
                if session_id:
                    rows = self._conn.execute("""
                        SELECT i.* FROM interactions i
                        JOIN interactions_fts fts ON i.id = fts.rowid
                        WHERE interactions_fts MATCH ? AND i.session_id = ?
                        AND (i.user_importance >= ? OR i.response_importance >= ?)
                        ORDER BY rank
                        LIMIT ?
                    """, (query, session_id, min_importance, min_importance, limit)).fetchall()
                else:
                    rows = self._conn.execute("""
                        SELECT i.* FROM interactions i
                        JOIN interactions_fts fts ON i.id = fts.rowid
                        WHERE interactions_fts MATCH ?
                        AND (i.user_importance >= ? OR i.response_importance >= ?)
                        ORDER BY rank
                        LIMIT ?
                    """, (query, min_importance, min_importance, limit)).fetchall()
            except sqlite3.OperationalError:
                # FTS5 може бути недоступний — fallback на LIKE
                rows = []

            if not rows:
                # Fallback: LIKE search
                pattern = f"%{query}%"
                if session_id:
                    rows = self._conn.execute("""
                        SELECT * FROM interactions
                        WHERE session_id = ? AND (user_input LIKE ? OR response LIKE ?)
                        AND (user_importance >= ? OR response_importance >= ?)
                        ORDER BY timestamp DESC
                        LIMIT ?
                    """, (session_id, pattern, pattern, min_importance, min_importance, limit)).fetchall()
                else:
                    rows = self._conn.execute("""
                        SELECT * FROM interactions
                        WHERE user_input LIKE ? OR response LIKE ?
                        AND (user_importance >= ? OR response_importance >= ?)
                        ORDER BY timestamp DESC
                        LIMIT ?
                    """, (pattern, pattern, min_importance, min_importance, limit)).fetchall()

            return [dict(r) for r in rows]

    async def get_session_context(self, session_id: str, limit: int = 10) -> List[Dict]:
        """Останні N взаємодій сесії."""
        if self._closed:
            raise RuntimeError("UNMS controller is closed")

        async with self._lock:
            rows = self._conn.execute("""
                SELECT * FROM interactions
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()
            return [dict(r) for r in rows]

    async def cleanup_low_importance(self, threshold: int = 3, keep_last_n: int = 100):
        """Видаляє старі, маловажливі записи."""
        if self._closed:
            return

        async with self._lock:
            # Спочатку порахуємо скільки видалимо
            count = self._conn.execute("""
                SELECT COUNT(*) FROM interactions
                WHERE user_importance <= ? AND response_importance <= ?
                AND id NOT IN (SELECT id FROM interactions ORDER BY timestamp DESC LIMIT ?)
            """, (threshold, threshold, keep_last_n)).fetchone()[0]

            if count > 0:
                with self._conn:
                    self._conn.execute("""
                        DELETE FROM interactions
                        WHERE user_importance <= ? AND response_importance <= ?
                        AND id NOT IN (SELECT id FROM interactions ORDER BY timestamp DESC LIMIT ?)
                    """, (threshold, threshold, keep_last_n))
                print(f"[UNMS] Cleaned up {count} low-importance interactions")
                # VACUUM для звільнення місця (може блокувати, тому обережно)
                # self._conn.execute("VACUUM")  # Робимо окремо, не тут

    async def get_stats(self) -> Dict:
        """Статистика UNMS."""
        if self._closed:
            return {"error": "closed"}

        async with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
            sessions = self._conn.execute("SELECT COUNT(DISTINCT session_id) FROM interactions").fetchone()[0]
            avg_user_imp = self._conn.execute("SELECT AVG(user_importance) FROM interactions").fetchone()[0] or 0
            avg_resp_imp = self._conn.execute("SELECT AVG(response_importance) FROM interactions").fetchone()[0] or 0
            db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            wal_size = os.path.getsize(self.db_path + "-wal") if os.path.exists(self.db_path + "-wal") else 0

            return {
                "total_interactions": total,
                "unique_sessions": sessions,
                "avg_user_importance": round(avg_user_imp, 2),
                "avg_response_importance": round(avg_resp_imp, 2),
                "db_size_bytes": db_size,
                "wal_size_bytes": wal_size,
            }

    def checkpoint_wal(self):
        """Примусово записує WAL у основну БД (для backup)."""
        if self._conn and not self._closed:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            print("[UNMS] WAL checkpointed")

    async def close(self):
        """Graceful shutdown. Checkpoint WAL, close connection."""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            if self._conn:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
                self._conn = None
        print("[UNMS] Connection closed gracefully.")

    def __del__(self):
        """Фіналізатор — намагаємось закрити, якщо забули."""
        if hasattr(self, '_conn') and self._conn and not getattr(self, '_closed', True):
            try:
                self._conn.close()
            except Exception:
                pass