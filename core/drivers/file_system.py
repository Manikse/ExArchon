import os
import shutil
import hashlib
import difflib
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from enum import Enum
from dataclasses import dataclass


class FileOperation(Enum):
    READ = "READ"
    WRITE = "WRITE"
    APPEND = "APPEND"
    DELETE = "DELETE"
    LIST = "LIST"
    COPY = "COPY"
    MOVE = "MOVE"
    DIFF = "DIFF"
    PATCH = "PATCH"


@dataclass
class FileOpResult:
    success: bool
    message: str
    data: Optional[str] = None
    diff: Optional[str] = None


class FileSystemDriver:
    DANGEROUS_EXTENSIONS = {
        '.exe', '.dll', '.bat', '.cmd', '.sh', '.bin',
        '.sys', '.drv', '.com', '.msi', '.scr', '.vbs',
        '.js', '.ps1', '.py', '.rb', '.pl',
    }

    def __init__(
        self,
        working_dir: str = "./kernel_workspace",
        safe_mode: bool = True,
        max_file_size_mb: float = 10.0,
        enable_backups: bool = True,
        allowed_extensions: Optional[List[str]] = None
    ):
        self.name = "FileSystem"
        self.working_dir = os.path.abspath(working_dir)
        self.safe_mode = safe_mode
        self.max_file_size = int(max_file_size_mb * 1024 * 1024)
        self.enable_backups = enable_backups
        self.allowed_extensions = set(allowed_extensions) if allowed_extensions else None

        self.backup_dir = os.path.join(self.working_dir, ".exarchon_backups")
        self.patches_dir = os.path.join(self.working_dir, ".exarchon_patches")
        self.audit_log = os.path.join(self.working_dir, ".exarchon_fs_audit.log")

        os.makedirs(self.working_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)
        os.makedirs(self.patches_dir, exist_ok=True)

    def _resolve_path(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        if not filename or not filename.strip():
            return None, "Empty filename"

        if os.path.isabs(filename):
            return None, f"Absolute paths are not allowed: {filename}"

        clean = os.path.normpath(filename)
        if clean.startswith("..") or "/../" in clean or "\\..\\" in clean:
            return None, f"Path traversal attempt blocked: {filename}"

        final_path = os.path.abspath(os.path.join(self.working_dir, clean))
        if not final_path.startswith(os.path.abspath(self.working_dir)):
            return None, "Access denied: path escapes working directory"

        if self.allowed_extensions:
            ext = os.path.splitext(final_path)[1].lower()
            if ext not in self.allowed_extensions:
                return None, f"File extension {ext!r} not allowed"

        return final_path, None

    def _log_operation(self, op: str, target: str, status: str, details: str = ""):
        timestamp = datetime.now().isoformat()
        log_line = f"[{timestamp}] [{op}] [{status}] {target}"
        if details:
            log_line += f" | {details}"
        log_line += "\n"
        try:
            with open(self.audit_log, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def _backup_file(self, filepath: str) -> Optional[str]:
        if not os.path.exists(filepath):
            return None
        if not self.enable_backups:
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(filepath)
        backup_name = f"{filename}.{timestamp}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)
        try:
            shutil.copy2(filepath, backup_path)
            return backup_path
        except Exception:
            return None

    def _generate_diff(self, old_content: str, new_content: str, filename: str) -> str:
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"{filename}.old",
            tofile=f"{filename}.new",
            lineterm=""
        )
        return "".join(diff)

    def _save_pending_patch(self, filename: str, new_content: str, diff: str) -> str:
        patch_id = hashlib.sha256(f"{filename}:{datetime.now().isoformat()}".encode()).hexdigest()[:16]
        patch_path = os.path.join(self.patches_dir, f"{patch_id}.patch")
        patch_meta = os.path.join(self.patches_dir, f"{patch_id}.meta")
        with open(patch_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        meta = f"target: {filename}\ntime: {datetime.now().isoformat()}\ndiff_size: {len(diff)}\n"
        with open(patch_meta, "w", encoding="utf-8") as f:
            f.write(meta)
        return patch_id

    def _get_file_hash(self, filepath: str) -> str:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    def execute(self, command: str) -> str:
        print(f"\n[Driver: {self.name}]  Processing file operation...")
        lines = command.split("\n", 1)
        first_line = lines[0].strip()
        rest = lines[1] if len(lines) > 1 else ""
        parts = first_line.split(None, 1)
        if len(parts) < 2:
            return "FAILED: Invalid format. Use OPERATION filename"
        op_str = parts[0].upper()
        filename = parts[1].strip()
        try:
            operation = FileOperation(op_str)
        except ValueError:
            allowed = ", ".join([o.value for o in FileOperation])
            return f"FAILED: Unknown operation {op_str!r}. Allowed: {allowed}"
        if operation == FileOperation.READ:
            return self._handle_read(filename)
        elif operation == FileOperation.WRITE:
            return self._handle_write(filename, rest)
        elif operation == FileOperation.APPEND:
            return self._handle_append(filename, rest)
        elif operation == FileOperation.DELETE:
            return self._handle_delete(filename)
        elif operation == FileOperation.LIST:
            return self._handle_list(filename)
        elif operation == FileOperation.COPY:
            return self._handle_copy(filename, rest)
        elif operation == FileOperation.MOVE:
            return self._handle_move(filename, rest)
        elif operation == FileOperation.DIFF:
            return self._handle_diff(filename, rest)
        elif operation == FileOperation.PATCH:
            return self._handle_patch(filename)
        return "FAILED: Unhandled operation"

    def _handle_read(self, filename: str) -> str:
        filepath, err = self._resolve_path(filename)
        if err:
            self._log_operation("READ", filename, "DENIED", err)
            return f"FAILED: {err}"
        if not os.path.exists(filepath):
            self._log_operation("READ", filename, "NOT_FOUND")
            return f"FAILED: File {filename!r} does not exist."
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            self._log_operation("READ", filename, "SUCCESS", f"{len(content)} bytes")
            return f"SUCCESS: File {filename!r} content:\n{content}"
        except Exception as e:
            self._log_operation("READ", filename, "ERROR", str(e))
            return f"FAILED to read file: {e}"

    def _handle_write(self, filename: str, content: str) -> str:
        filepath, err = self._resolve_path(filename)
        if err:
            self._log_operation("WRITE", filename, "DENIED", err)
            return f"FAILED: {err}"
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.max_file_size:
            self._log_operation("WRITE", filename, "DENIED", "File too large")
            return f"FAILED: Content exceeds max size ({self.max_file_size / 1024 / 1024:.1f} MB)"
        if self.safe_mode:
            old_content = ""
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    old_content = f.read()
            diff = self._generate_diff(old_content, content, filename)
            patch_id = self._save_pending_patch(filename, content, diff)
            self._log_operation("WRITE", filename, "PENDING_PATCH", f"patch_id={patch_id}")
            preview = diff[:1000] + ("..." if len(diff) > 1000 else "")
            return (
                f"[SHADOW PROTOCOL] Write blocked by safe_mode.\n"
                f"Target: {filename}\n"
                f"Patch ID: {patch_id}\n"
                f"Diff preview ({len(diff)} chars):\n"
                f"{'-'*40}\n"
                f"{preview}\n"
                f"{'-'*40}\n"
                f"To apply: use PATCH {patch_id}"
            )
        backup_path = self._backup_file(filepath)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            details = f"{len(content)} bytes"
            if backup_path:
                details += f", backup={os.path.basename(backup_path)}"
            self._log_operation("WRITE", filename, "SUCCESS", details)
            return f"SUCCESS: File {filename!r} written ({len(content)} bytes)."
        except Exception as e:
            self._log_operation("WRITE", filename, "ERROR", str(e))
            return f"FAILED to write file: {e}"

    def _handle_append(self, filename: str, content: str) -> str:
        filepath, err = self._resolve_path(filename)
        if err:
            self._log_operation("APPEND", filename, "DENIED", err)
            return f"FAILED: {err}"
        content_bytes = content.encode("utf-8")
        current_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        if current_size + len(content_bytes) > self.max_file_size:
            return "FAILED: Append would exceed max file size"
        backup_path = self._backup_file(filepath)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(content)
            self._log_operation("APPEND", filename, "SUCCESS", f"+{len(content)} bytes")
            return f"SUCCESS: Appended {len(content)} bytes to {filename!r}."
        except Exception as e:
            self._log_operation("APPEND", filename, "ERROR", str(e))
        return f"FAILED to append: {e}"