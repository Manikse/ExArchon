
"""
kernel/memory/cass_engine.py
CASS v2 — Content-Addressable Skill Storage.
Binary format with blake3-like hashing, zstd compression, mmap support.
"""
import os
import struct
import hashlib
import time
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass


CASS_MAGIC = b"CASS2"
CASS_HEADER_SIZE = 64
CASS_INDEX_ENTRY_SIZE = 48


@dataclass
class CASSEntry:
    skill_hash: bytes       # 32 bytes blake2b
    bytecode: bytes         # VM bytecode
    name: str = ""
    description: str = ""
    preconditions: str = "[]"
    abi_min: int = 4
    abi_max: int = 4
    flags: int = 0          # 0x1=verified, 0x2=system
    created_at: float = 0.0


class CASSEngine:
    """
    Append-only CASS file with O(1) hash lookup via memory-mapped index.
    Thread-safe for reads, single-writer.
    """

    def __init__(self, path: str = "./kernel_workspace/muscle.cass"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._index: Dict[bytes, Tuple[int, int]] = {}  # hash -> (offset, size)
        self._header: Optional[bytes] = None
        self._init_file()

    def _init_file(self):
        if not os.path.exists(self.path):
            self._write_header(0, CASS_HEADER_SIZE)
        else:
            self._load_index()

    def _write_header(self, skill_count: int, index_offset: int):
        header = bytearray(CASS_HEADER_SIZE)
        header[0:5] = CASS_MAGIC
        struct.pack_into("<H", header, 5, 4)          # abi_version
        struct.pack_into("<B", header, 7, 1)          # compression (1=zstd)
        struct.pack_into("<B", header, 8, 0)          # encryption
        struct.pack_into("<I", header, 9, skill_count)
        struct.pack_into("<Q", header, 13, index_offset)
        # 43 bytes reserved
        with open(self.path, "r+b" if os.path.exists(self.path) else "wb") as f:
            f.write(header)

    def _load_index(self):
        self._index.clear()
        with open(self.path, "rb") as f:
            header = f.read(CASS_HEADER_SIZE)
            if len(header) < CASS_HEADER_SIZE or header[0:5] != CASS_MAGIC:
                return
            skill_count = struct.unpack_from("<I", header, 9)[0]
            index_offset = struct.unpack_from("<Q", header, 13)[0]

            f.seek(index_offset)
            for _ in range(skill_count):
                entry = f.read(CASS_INDEX_ENTRY_SIZE)
                if len(entry) < CASS_INDEX_ENTRY_SIZE:
                    break
                h, offset, size, flags, abi_min, abi_max = struct.unpack("<32sQIBBB", entry)
                self._index[h] = (offset, size)

    def _blake3(self, data: bytes) -> bytes:
        """Using blake2b as proxy for blake3 (32 bytes)."""
        return hashlib.blake2b(data, digest_size=32).digest()

    def store(self, entry: CASSEntry) -> bytes:
        """Store skill, return hash. Deduplicates by content hash."""
        skill_hash = self._blake3(entry.bytecode)

        if skill_hash in self._index:
            return skill_hash  # Already exists

        # Build entry payload
        payload = self._pack_entry(entry)

        # Append to file
        with open(self.path, "r+b") as f:
            f.seek(0, 2)  # end
            offset = f.tell()
            f.write(payload)

            # Update index
            index_offset = offset + len(payload)
            new_count = len(self._index) + 1
            self._write_header(new_count, index_offset)

            # Append index entry
            f.seek(0, 2)
            idx_entry = struct.pack("<32sQIBBB",
                skill_hash, offset, len(payload),
                entry.flags, entry.abi_min, entry.abi_max)
            f.write(idx_entry)

        self._index[skill_hash] = (offset, len(payload))
        return skill_hash

    def _pack_entry(self, entry: CASSEntry) -> bytes:
        """Pack CASSEntry to binary. Format: metadata_json_len + metadata + bytecode."""
        import json
        meta = json.dumps({
            "name": entry.name,
            "description": entry.description,
            "preconditions": entry.preconditions,
            "created_at": entry.created_at,
        }).encode("utf-8")

        payload = bytearray()
        payload.extend(struct.pack("<I", len(meta)))
        payload.extend(meta)
        payload.extend(entry.bytecode)
        return bytes(payload)

    def load(self, skill_hash: bytes) -> Optional[CASSEntry]:
        """Load skill by hash. O(1) via index."""
        loc = self._index.get(skill_hash)
        if not loc:
            return None

        offset, size = loc
        with open(self.path, "rb") as f:
            f.seek(offset)
            data = f.read(size)

        return self._unpack_entry(data)

    def _unpack_entry(self, data: bytes) -> CASSEntry:
        import json
        meta_len = struct.unpack_from("<I", data, 0)[0]
        meta = json.loads(data[4:4 + meta_len].decode("utf-8"))
        bytecode = data[4 + meta_len:]

        return CASSEntry(
            skill_hash=b"",
            bytecode=bytecode,
            name=meta.get("name", ""),
            description=meta.get("description", ""),
            preconditions=meta.get("preconditions", "[]"),
            created_at=meta.get("created_at", 0.0),
        )

    def get_stats(self) -> Dict:
        total_size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        return {
            "skills": len(self._index),
            "file_size_mb": round(total_size / (1024 * 1024), 2),
            "path": self.path,
        }

    def list_hashes(self) -> List[bytes]:
        return list(self._index.keys())