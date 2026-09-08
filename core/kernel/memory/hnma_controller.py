"""
kernel/memory/hnma_controller.py
Hierarchical Neural Memory Architecture v3.
L1: CASS mmap (Muscle Flash)
L2: Working RAM (LRU + embeddings)
L3: Episodic Disk (SQLite + FTS5 + Vector Search + brotli summaries)
"""
import os
import time
import sqlite3
import json
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

from kernel.memory.cass_engine import CASSEngine, CASSEntry

# Optional brotli — fallback to zlib if unavailable
try:
    import brotli
    _COMPRESS = lambda data: brotli.compress(data)
    _DECOMPRESS = lambda data: brotli.decompress(data)
    _COMPRESSION_NAME = "brotli"
except ImportError:
    import zlib
    _COMPRESS = lambda data: zlib.compress(data, level=6)
    _DECOMPRESS = lambda data: zlib.decompress(data)
    _COMPRESSION_NAME = "zlib"

# Optional sentence-transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer
    _EMBEDDINGS_AVAILABLE = True
except ImportError:
    _EMBEDDINGS_AVAILABLE = False


@dataclass
class MemoryTier:
    name: str
    max_bytes: int
    current_bytes: int = 0
    hit_count: int = 0
    miss_count: int = 0


class HNMAController:
    """
    HNMA v3 — manages L1 Muscle Flash, L2 Working RAM, L3 Episodic Disk.
    Now with FTS5 full-text search and optional semantic (vector) search.
    """

    def __init__(self, base_path: str = "./kernel_workspace"):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

        # L1: Muscle Flash (CASS binary, persistent)
        self.l1 = CASSEngine(os.path.join(base_path, "muscle.cass"))

        # L2: Working RAM (LRU cache in Python dict, later mmap)
        self.l2_cache: OrderedDict[str, Any] = OrderedDict()
        self.l2_max_items = 500
        self.l2_embeddings: Dict[str, bytes] = {}

        # L3: Episodic Disk (SQLite + FTS5 + compression)
        self.l3_path = os.path.join(base_path, "episodic.db")
        self._l3_conn: Optional[sqlite3.Connection] = None
        self.l3_conn: Optional[sqlite3.Connection] = None
        self._init_l3()

        # Semantic search model (lazy-loaded)
        self._embedder: Optional[Any] = None
        self._embedding_dim: int = 384  # all-MiniLM-L6-v2

        self.tiers = {
            "L1": MemoryTier("L1_muscle", 100 * 1024 * 1024),
            "L2": MemoryTier("L2_working", 500 * 1024 * 1024),
            "L3": MemoryTier("L3_episodic", 10 * 1024 ** 3),
        }

    def _init_l3(self):
        self._l3_conn = sqlite3.connect(self.l3_path, check_same_thread=False)
        self.l3_conn = self._l3_conn
        self._l3_conn.row_factory = sqlite3.Row
        self._l3_conn.execute("PRAGMA journal_mode=WAL")
        self._l3_conn.execute("PRAGMA synchronous=NORMAL")

        # Main atoms table
        self._l3_conn.execute("""
            CREATE TABLE IF NOT EXISTS context_atoms (
                atom_id BLOB PRIMARY KEY,
                prev_hash BLOB,
                timestamp_ns INTEGER,
                intent_embedding BLOB,
                facts_json TEXT,
                summary TEXT,
                raw_content BLOB,
                importance REAL,
                access_count INTEGER DEFAULT 0
            )
        """)

        # FTS5 virtual table for full-text search
        self._l3_conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS context_atoms_fts USING fts5(
                summary, facts_json,
                content='context_atoms',
                content_rowid='rowid'
            )
        """)

        # Triggers to keep FTS5 in sync
        self._l3_conn.execute("""
            CREATE TRIGGER IF NOT EXISTS atoms_fts_insert AFTER INSERT ON context_atoms BEGIN
                INSERT INTO context_atoms_fts(rowid, summary, facts_json)
                VALUES (new.rowid, new.summary, new.facts_json);
            END
        """)
        self._l3_conn.execute("""
            CREATE TRIGGER IF NOT EXISTS atoms_fts_delete AFTER DELETE ON context_atoms BEGIN
                INSERT INTO context_atoms_fts(context_atoms_fts, rowid, summary, facts_json)
                VALUES ('delete', old.rowid, old.summary, old.facts_json);
            END
        """)
        self._l3_conn.execute("""
            CREATE TRIGGER IF NOT EXISTS atoms_fts_update AFTER UPDATE ON context_atoms BEGIN
                INSERT INTO context_atoms_fts(context_atoms_fts, rowid, summary, facts_json)
                VALUES ('delete', old.rowid, old.summary, old.facts_json);
                INSERT INTO context_atoms_fts(rowid, summary, facts_json)
                VALUES (new.rowid, new.summary, new.facts_json);
            END
        """)

        self._l3_conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_atoms_time ON context_atoms(timestamp_ns)
        """)
        self._l3_conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_atoms_importance ON context_atoms(importance DESC)
        """)

    # ── Semantic Embeddings ──
    def _get_embedder(self):
        if self._embedder is None and _EMBEDDINGS_AVAILABLE:
            try:
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                self._embedder = None
        return self._embedder

    def _embed(self, text: str) -> Optional[bytes]:
        model = self._get_embedder()
        if model is None:
            return None
        try:
            vec = model.encode(text, convert_to_numpy=True)
            return vec.astype("float32").tobytes()
        except Exception:
            return None

    @staticmethod
    def _cosine_similarity(a: bytes, b: bytes) -> float:
        import numpy as np
        va = np.frombuffer(a, dtype="float32")
        vb = np.frombuffer(b, dtype="float32")
        if len(va) != len(vb):
            return 0.0
        dot = np.dot(va, vb)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(dot / (na * nb))

    # ── L1: Muscle Flash ──
    def store_skill(self, name: str, bytecode: bytes, description: str = "", preconditions: str = "[]") -> bytes:
        entry = CASSEntry(
            skill_hash=b"",
            bytecode=bytecode,
            name=name,
            description=description,
            preconditions=preconditions,
            created_at=time.time(),
        )
        h = self.l1.store(entry)
        self.l2_put(h, entry)
        return h

    def load_skill(self, skill_hash: bytes) -> Optional[CASSEntry]:
        cached = self.l2_get(skill_hash)
        if cached:
            self.tiers["L2"].hit_count += 1
            return cached
        self.tiers["L2"].miss_count += 1

        entry = self.l1.load(skill_hash)
        if entry:
            self.l2_put(skill_hash, entry)
            self.tiers["L1"].hit_count += 1
        else:
            self.tiers["L1"].miss_count += 1
        return entry

    def query_skills(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            entries = self.l1.search(query, limit=limit)
            results = []
            for e in entries:
                h = e.skill_hash
                self.l2_put(h, e)
                self.tiers["L1"].hit_count += 1
                results.append({
                    "hash": h,
                    "name": e.name,
                    "description": e.description,
                    "bytecode": e.bytecode,
                    "preconditions": e.preconditions,
                })
            return results
        except Exception:
            return []

    def store_cass(self, name: str, bytecode: bytes, description: str = "") -> bytes:
        return self.store_skill(name, bytecode, description)

    # ── L2: Working RAM ──
    def l2_put(self, key: bytes, value: CASSEntry):
        key_str = key.hex()
        if key_str in self.l2_cache:
            self.l2_cache.move_to_end(key_str)
            return
        if len(self.l2_cache) >= self.l2_max_items:
            self.l2_cache.popitem(last=False)
        self.l2_cache[key_str] = value

    def l2_get(self, key: bytes) -> Optional[CASSEntry]:
        key_str = key.hex()
        if key_str in self.l2_cache:
            self.l2_cache.move_to_end(key_str)
            return self.l2_cache[key_str]
        return None

    def l2_clear(self):
        self.l2_cache.clear()

    # ── L3: Episodic Disk ──
    def store_atom(self, atom: Dict[str, Any]):
        raw = atom.get("raw_content", "")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        raw_bytes = raw.encode("utf-8")
        compressed = _COMPRESS(raw_bytes) if len(raw_bytes) > 256 else raw_bytes

        atom_id = atom["atom_id"]
        if isinstance(atom_id, str):
            atom_id = bytes.fromhex(atom_id)
        prev_hash = atom.get("prev_hash")
        if isinstance(prev_hash, str) and prev_hash:
            prev_hash = bytes.fromhex(prev_hash)
        elif prev_hash is None:
            prev_hash = b""

        intent_emb = atom.get("intent_embedding", b"")
        if isinstance(intent_emb, str) and intent_emb:
            intent_emb = bytes.fromhex(intent_emb)

        # Generate embedding if not provided and model available
        summary = atom.get("summary", "")
        if (not intent_emb or len(intent_emb) == 0) and summary:
            emb = self._embed(summary)
            if emb:
                intent_emb = emb

        with self._l3_conn:
            self._l3_conn.execute("""
                INSERT OR REPLACE INTO context_atoms
                (atom_id, prev_hash, timestamp_ns, intent_embedding, facts_json,
                 summary, raw_content, importance, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                atom_id,
                prev_hash,
                atom["timestamp_ns"],
                intent_emb,
                atom.get("facts_json", "[]"),
                summary,
                compressed,
                atom.get("importance", 0.5),
                atom.get("access_count", 0),
            ))

    def query_episodic(self, query: str, limit: int = 5) -> list:
        """Search L3 using FTS5 full-text search first, then semantic fallback."""
        try:
            conn = self._l3_conn if self._l3_conn is not None else self.l3_conn
            if conn is None:
                return []

            # 1. Try FTS5 full-text search
            try:
                cursor = conn.execute(
                    """SELECT summary, timestamp_ns, intent_embedding, facts_json
                       FROM context_atoms
                       WHERE rowid IN (
                           SELECT rowid FROM context_atoms_fts
                           WHERE context_atoms_fts MATCH ?
                           ORDER BY rank
                       )
                       ORDER BY timestamp_ns DESC
                       LIMIT ?""",
                    (query, limit)
                )
                rows = cursor.fetchall()
                if rows:
                    return [{"summary": r[0], "timestamp": r[1], "facts": r[3]} for r in rows]
            except Exception:
                pass  # FTS5 might fail if query has special chars

            # 2. Fallback: semantic search with embeddings
            query_emb = self._embed(query)
            if query_emb:
                cursor = conn.execute(
                    "SELECT summary, timestamp_ns, intent_embedding, facts_json FROM context_atoms ORDER BY timestamp_ns DESC LIMIT 200"
                )
                scored = []
                for row in cursor.fetchall():
                    emb = row[2]
                    if emb:
                        sim = self._cosine_similarity(query_emb, emb)
                        if sim > 0.5:
                            scored.append((sim, row))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [{"summary": r[1][0], "timestamp": r[1][1], "facts": r[1][3], "similarity": r[0]} for r in scored[:limit]]

            # 3. Last fallback: simple LIKE
            words = [w for w in query.lower().split() if len(w) > 3]
            if not words:
                return []
            conditions = " OR ".join(["summary LIKE ?" for _ in words])
            params = [f"%{w}%" for w in words] + [limit]
            cursor = conn.execute(
                f"SELECT summary, timestamp_ns, facts_json FROM context_atoms WHERE {conditions} ORDER BY timestamp_ns DESC LIMIT ?",
                params
            )
            return [{"summary": r[0], "timestamp": r[1], "facts": r[2]} for r in cursor.fetchall()]
        except Exception:
            return []

    def get_atom_by_hash(self, atom_id: bytes) -> Optional[Dict]:
        if isinstance(atom_id, str):
            atom_id = bytes.fromhex(atom_id)

        row = self._l3_conn.execute(
            "SELECT * FROM context_atoms WHERE atom_id = ?", (atom_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        raw = result.get("raw_content", b"")
        try:
            decompressed = _DECOMPRESS(raw)
            result["raw_content"] = decompressed.decode("utf-8")
        except Exception:
            result["raw_content"] = raw.decode("utf-8", errors="replace")
        for k in ("atom_id", "prev_hash"):
            v = result.get(k)
            if isinstance(v, bytes):
                result[k] = v.hex()
        result["timestamp"] = result.get("timestamp_ns")
        return result

    def drill_down(self, start_atom_id: bytes, steps: int = 5) -> List[Dict]:
        chain = []
        current = start_atom_id
        for _ in range(steps):
            atom = self.get_atom_by_hash(current)
            if not atom:
                break
            chain.append(atom)
            prev = atom.get("prev_hash")
            if not prev:
                break
            current = prev
        return chain

    def query_working(self, query: str) -> str:
        for key, entry in list(self.l2_cache.items())[-20:]:
            if query.lower() in entry.name.lower() or query.lower() in entry.description.lower():
                return f"[L2] {entry.name}: {entry.description}"
        rows = self.query_episodic(query, limit=3)
        if rows:
            return "[L3] " + "; ".join(r["summary"] for r in rows)
        return "[HNMA] No memory found"

    def get_stats(self) -> Dict[str, Any]:
        l3_count = self._l3_conn.execute("SELECT COUNT(*) FROM context_atoms").fetchone()[0]
        l3_size = os.path.getsize(self.l3_path) if os.path.exists(self.l3_path) else 0
        return {
            "L1_skills": self.l1.get_stats(),
            "L2_cached": len(self.l2_cache),
            "L3_atoms": l3_count,
            "L3_size_mb": round(l3_size / (1024 * 1024), 2),
            "compression": _COMPRESSION_NAME,
            "embeddings_available": _EMBEDDINGS_AVAILABLE,
            "tier_hits": {k: v.hit_count for k, v in self.tiers.items()},
            "tier_misses": {k: v.miss_count for k, v in self.tiers.items()},
        }

    def close(self):
        if self._l3_conn:
            self._l3_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._l3_conn.close()
            self._l3_conn = None
            self.l3_conn = None