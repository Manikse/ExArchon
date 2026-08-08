"""
migrate_to_v2.py
One-time migration: SQLite SkillLibrary → CASS v2 + HNMA.
Run once after deploying v1.0 files.
"""
import os
import sys
import sqlite3

# Add core to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))

from kernel.skills.compiler_v2 import SkillCompiler
from kernel.memory.hnma_controller import HNMAController


def migrate_skills(old_db_path: str, workspace: str = "./kernel_workspace"):
    """
    Migrate skills from old SQLite SkillLibrary to new CASS/HNMA.
    Safe to run multiple times — deduplicates by content hash.
    """
    if not os.path.exists(old_db_path):
        print(f"[MIGRATE] Old skill DB not found at {old_db_path}, nothing to migrate.")
        return

    print(f"[MIGRATE] Reading old skills from {old_db_path}...")

    conn = sqlite3.connect(old_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM skills").fetchall()
    conn.close()

    if not rows:
        print("[MIGRATE] No skills found in old DB.")
        return

    hnma = HNMAController(base_path=workspace)
    compiler = SkillCompiler()
    migrated = 0
    skipped = 0

    for row in rows:
        name = row["name"]
        try:
            import json
            graph_raw = json.loads(row["execution_graph"])
            steps = [{"tool": s["tool"], "action_input": s["action_input"]} for s in graph_raw]

            bytecode = compiler.compile_from_trace(steps)

            # Store in HNMA (dedup by hash automatically)
            h = hnma.store_skill(
                name=name,
                bytecode=bytecode,
                description=row.get("input_pattern", name),
                preconditions=row.get("preconditions", "[]"),
            )
            migrated += 1
            print(f"  ✓ {name[:40]:40} → {h.hex()[:16]}...")

        except Exception as e:
            skipped += 1
            print(f"  ✗ {name[:40]:40} → {e}")

    hnma.close()
    print(f"\n[MIGRATE] Done: {migrated} migrated, {skipped} skipped.")
    print(f"[MIGRATE] New CASS file: {os.path.join(workspace, 'muscle.cass')}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Migrate ExArchon skills to v2")
    parser.add_argument("--old-db", default="./kernel_workspace/skills.db", help="Old SQLite skills DB")
    parser.add_argument("--workspace", default="./kernel_workspace", help="Target workspace")
    args = parser.parse_args()

    migrate_skills(args.old_db, args.workspace)