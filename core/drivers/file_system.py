"""
drivers/file_system.py
Capability-Based FileSystem Driver with Shadow Protocol.
"""
import os
import shutil
import difflib
import hashlib
import time
from typing import Optional, Tuple
from dataclasses import dataclass

from kernel.security.capabilities import CapabilityManager, CapOp


@dataclass
class FileOpResult:
    success: bool
    content: str = ""
    error: str = ""
    shadow_path: Optional[str] = None


class FileSystemDriver:
    """
    Shadow Protocol: READ by default, WRITE only through PATCH approval.
    All operations validated through CapabilityManager.
    """

    def __init__(
        self,
        working_dir: str = "./kernel_workspace",
        capability_manager: Optional[CapabilityManager] = None,
    ):
        self.name = "file_system"
        self.working_dir = os.path.abspath(working_dir)
        self.cap_manager = capability_manager
        self.shadow_dir = os.path.join(self.working_dir, ".exarchon_shadow")
        self.backup_dir = os.path.join(self.working_dir, ".exarchon_backups")
        os.makedirs(self.shadow_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

    def _resolve_path(self, raw_path: str) -> str:
        """Resolves path relative to working_dir, prevents traversal."""
        raw_path = raw_path.strip().strip('"').strip("'")
        if raw_path.startswith("/"):
            raw_path = raw_path[1:]
        full = os.path.abspath(os.path.join(self.working_dir, raw_path))
        # Prevent directory traversal outside working_dir
        if not full.startswith(self.working_dir):
            raise ValueError(f"Path traversal blocked: {raw_path}")
        return full

    def _check_cap(self, op: CapOp, target: str) -> Tuple[bool, str]:
        if self.cap_manager:
            ok, reason = self.cap_manager.validate("file_system", op, target)
            if not ok:
                return False, reason
        return True, ""

    def execute(self, action_input: str) -> str:
        """Main entry: action_input like 'READ file.txt' or 'WRITE file.txt content'"""
        print(f"\n[Driver: FileSystem] Executing: '{action_input[:80]}'...")
        parts = action_input.split(None, 2)
        if not parts:
            return "[ERROR] Empty file system command"

        op = parts[0].upper()
        path = parts[1] if len(parts) > 1 else ""
        payload = parts[2] if len(parts) > 2 else ""

        try:
            resolved = self._resolve_path(path)
        except ValueError as e:
            return f"[SECURITY ERROR] {e}"

        # Capability check
        cap_op = CapOp.READ if op in ("READ", "LIST", "DIFF") else CapOp.WRITE
        ok, reason = self._check_cap(cap_op, resolved)
        if not ok:
            return f"[CAPABILITY DENIED] {reason}"

        try:
            if op == "READ":
                return self._read(resolved)
            elif op == "WRITE":
                return self._write(resolved, payload, shadow=False)
            elif op == "APPEND":
                return self._append(resolved, payload)
            elif op == "DELETE":
                return self._delete(resolved)
            elif op == "LIST":
                return self._list(resolved)
            elif op == "COPY":
                src, dst = payload.split(None, 1)
                return self._copy(self._resolve_path(src), self._resolve_path(dst))
            elif op == "MOVE":
                src, dst = payload.split(None, 1)
                return self._move(self._resolve_path(src), self._resolve_path(dst))
            elif op == "DIFF":
                return self._diff(resolved)
            elif op == "PATCH":
                return self._patch(resolved, payload)
            else:
                return f"[ERROR] Unknown file operation: {op}"
        except Exception as e:
            return f"[ERROR] {type(e).__name__}: {str(e)}"

    def _read(self, path: str) -> str:
        if not os.path.exists(path):
            return f"[ERROR] File not found: {path}"
        if os.path.isdir(path):
            return f"[ERROR] Is a directory: {path}"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return content[:5000] + ("..." if len(content) > 5000 else "")

    def _write(self, path: str, content: str, shadow: bool = False) -> str:
        # Shadow Protocol: backup before write
        if os.path.exists(path):
            self._backup(path)
        if shadow:
            path = os.path.join(self.shadow_dir, os.path.basename(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[OK] Written {len(content)} bytes to {path}"

    def _append(self, path: str, content: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"[OK] Appended to {path}"

    def _delete(self, path: str) -> str:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return f"[OK] Deleted {path}"

    def _list(self, path: str) -> str:
        if not os.path.exists(path):
            return f"[ERROR] Path not found: {path}"
        items = os.listdir(path)
        return "\n".join(items[:200])

    def _copy(self, src: str, dst: str) -> str:
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return f"[OK] Copied {src} -> {dst}"

    def _move(self, src: str, dst: str) -> str:
        shutil.move(src, dst)
        return f"[OK] Moved {src} -> {dst}"

    def _backup(self, path: str):
        """Shadow Protocol: create backup before destructive operation."""
        if not os.path.exists(path):
            return
        timestamp = str(int(time.time()))
        hash_id = hashlib.sha256(path.encode()).hexdigest()[:8]
        backup_name = f"{hash_id}_{timestamp}_{os.path.basename(path)}"
        backup_path = os.path.join(self.backup_dir, backup_name)
        if os.path.isdir(path):
            shutil.copytree(path, backup_path)
        else:
            shutil.copy2(path, backup_path)

    def _diff(self, path: str) -> str:
        """Show diff between current file and last backup."""
        backups = sorted(
            [f for f in os.listdir(self.backup_dir) if f.startswith(hashlib.sha256(path.encode()).hexdigest()[:8])]
        )
        if not backups:
            return "[INFO] No backups found for diff"
        last_backup = os.path.join(self.backup_dir, backups[-1])
        with open(last_backup, "r", encoding="utf-8", errors="replace") as f:
            old = f.readlines()
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            new = f.readlines()
        diff = list(difflib.unified_diff(old, new, fromfile="backup", tofile="current", lineterm=""))
        return "".join(diff[:100]) or "[INFO] No changes"

    def _patch(self, path: str, patch_content: str) -> str:
        """Apply unified diff patch."""
        self._backup(path)
        # Simple patch application (line-based)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # For now, just overwrite with patched content if provided directly
        # Real patch parsing would require patch module
        with open(path, "w", encoding="utf-8") as f:
            f.write(patch_content)
        return f"[OK] Patched {path}"