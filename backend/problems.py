"""
习题 CRUD — problems.db 读写
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from board import BOARD_SIZE, EMPTY


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS problems (
            id                TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            description       TEXT DEFAULT '',
            board_size        INTEGER DEFAULT 13,
            board_grid        TEXT NOT NULL,
            region_mask       TEXT NOT NULL,
            kill_targets      TEXT DEFAULT '[]',
            defend_targets    TEXT DEFAULT '[]',
            attacker_color    INTEGER DEFAULT 1,
            precompute_status TEXT DEFAULT 'none',
            precompute_job_id TEXT DEFAULT NULL,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()


def list_problems(db_path: str) -> List[dict]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, name, description, board_size, board_grid, region_mask, "
        "kill_targets, defend_targets, attacker_color, "
        "precompute_status, precompute_job_id, created_at, updated_at "
        "FROM problems ORDER BY updated_at DESC"
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        grid = json.loads(r["board_grid"])
        black_count = sum(1 for c in grid if c == 1)
        white_count = sum(1 for c in grid if c == -1)
        region = json.loads(r["region_mask"])
        region_count = sum(region)
        kill_targets = json.loads(r["kill_targets"])
        defend_targets = json.loads(r["defend_targets"])
        result.append({
            "id": r["id"],
            "name": r["name"],
            "board_size": r["board_size"],
            "black_count": black_count,
            "white_count": white_count,
            "region_count": region_count,
            "kill_count": len(kill_targets),
            "defend_count": len(defend_targets),
            "precompute_status": r["precompute_status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return result


def get_problem(db_path: str, problem_id: str) -> Optional[dict]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()
    db.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "board_size": row["board_size"],
        "board_grid": json.loads(row["board_grid"]),
        "region_mask": json.loads(row["region_mask"]),
        "kill_targets": json.loads(row["kill_targets"]),
        "defend_targets": json.loads(row["defend_targets"]),
        "attacker_color": row["attacker_color"],
        "precompute_status": row["precompute_status"],
        "precompute_job_id": row["precompute_job_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_problem(db_path: str, name: str = "未命名习题",
                   board_grid: Optional[List[int]] = None) -> str:
    pid = uuid.uuid4().hex[:12]
    now = _now()
    size = BOARD_SIZE
    grid = board_grid if board_grid else [EMPTY] * (size * size)
    region = [0] * (size * size)
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO problems (id, name, board_size, board_grid, region_mask, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, name, size, json.dumps(grid), json.dumps(region), now, now),
    )
    db.commit()
    db.close()
    return pid


def update_problem(db_path: str, problem_id: str, **fields) -> bool:
    sets = []
    vals = []
    json_fields = {"board_grid", "region_mask", "kill_targets", "defend_targets"}
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if k in json_fields else v)
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(problem_id)
    db = sqlite3.connect(db_path)
    c = db.execute(f"UPDATE problems SET {', '.join(sets)} WHERE id=?", vals)
    db.commit()
    db.close()
    return c.rowcount > 0


def delete_problem(db_path: str, problem_id: str, cache_dir: str = "") -> bool:
    # 先获取 job_id 以删缓存
    p = get_problem(db_path, problem_id)
    if p is None:
        return False
    db = sqlite3.connect(db_path)
    db.execute("DELETE FROM problems WHERE id=?", (problem_id,))
    db.commit()
    db.close()
    # 删除关联的预处理缓存
    if cache_dir and p.get("precompute_job_id"):
        jid = p["precompute_job_id"]
        import glob
        for f in glob.glob(os.path.join(cache_dir, f"{jid}*")):
            try:
                os.remove(f)
            except OSError:
                pass
    return True
