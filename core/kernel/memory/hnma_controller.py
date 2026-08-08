"""
kernel/memory/hnma_controller.py
Hierarchical Neural Memory Architecture v2.
L1: CASS mmap (Muscle Flash)
L2: Working RAM (LRU + embeddings)
L3: Episodic Disk (SQLite + brotli summaries)
"""
import os
import time
import sqlite3
from collections import OrderedDict
from typing import Dict, List, Optional, Any
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


@dataclass
class MemoryTier:
    name: str
    max_bytes: int
    current_bytes: int = 0
    hit_count: int = 0
    miss_count: int = 0


class HNMAController:
    """
    HNMA v2 — manages L1 Muscle Flash, L2 Working RAM, L3 Episodic Disk.
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

        # L3: Episodic Disk (SQLite + compression)
        self.l3_path = os.path.join(base_path, "episodic.db")
        self._l3_conn: Optional[sqlite3.Connection] = None
        self._init_l3()

        self.tiers = {
            "L1": MemoryTier("L1_muscle", 100 * 1024 * 1024),
            "L2": MemoryTier("L2_working", 500 * 1024 * 1024),
            "L3": MemoryTier("L3_episodic", 10 * 1024 ** 3),
        }

    def _init_l3(self):
        self._l3_conn = sqlite3.connect(self.l3_path, check_same_thread=False)
        self._l3_conn.row_factory = sqlite3.Row
        self._l3_conn.execute("PRAGMA journal_mode=WAL")
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
        self._l3_conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_atoms_time ON context_atoms(timestamp_ns)
        """)
        self._l3_conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_atoms_importance ON context_atoms(importance DESC)
        """)

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
        raw = atom.get("raw_content", "").encode("utf-8")
        compressed = _COMPRESS(raw) if len(raw) > 256 else raw

        with self._l3_conn:
            self._l3_conn.execute("""
                INSERT OR REPLACE INTO context_atoms
                (atom_id, prev_hash, timestamp_ns, intent_embedding, facts_json,
                 summary, raw_content, importance, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                atom["atom_id"],
                atom.get("prev_hash"),
                atom["timestamp_ns"],
                atom.get("intent_embedding"),
                atom.get("facts_json", "[]"),
                atom.get("summary", ""),
                compressed,
                atom.get("importance", 0.5),
                atom.get("access_count", 0),
            ))

    def query_episodic(self, query: str, limit: int = 10) -> List[Dict]:
        rows = self._l3_conn.execute("""
            SELECT * FROM context_atoms
            WHERE summary LIKE ?
            ORDER BY importance DESC, timestamp_ns DESC
            LIMIT ?
        """, (f"%{query}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def get_atom_by_hash(self, atom_id: bytes) -> Optional[Dict]:
        row = self._l3_conn.execute(
            "SELECT * FROM context_atoms WHERE atom_id = ?", (atom_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        raw = result.get("raw_content", b"")
        try:
            result["raw_content"] = _DECOMPRESS(raw).decode("utf-8")
        except Exception:
            result["raw_content"] = raw.decode("utf-8", errors="replace")
        return result

    def drill_down(self, start_atom_id: bytes, steps: int = 5) -> List[Dict]:
        chain = []
        current = start_atom_id
        for _ in range(steps):
            atom = self.get_atom_by_hash(current)
            if not atom:
                break
            chain.append(atom)
            current = atom.get("prev_hash")
            if not current:
                break
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
            "tier_hits": {k: v.hit_count for k, v in self.tiers.items()},
            "tier_misses": {k: v.miss_count for k, v in self.tiers.items()},
        }

    def close(self):
        if self._l3_conn:
            self._l3_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._l3_conn.close()
            self._l3_conn = None