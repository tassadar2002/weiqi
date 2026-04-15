"""
预处理系统

- run_precompute_parallel: 多进程并行穷举 df-pn
- solve_from_cache: 预处理完成后的查表求解
- load_tt_from_sqlite: 从 SQLite 加载 TT 到内存
"""

import json
import multiprocessing as mp
import os
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

from board import Board, BLACK, WHITE, EMPTY, BOARD_SIZE
from solver import DfpnSolver


# ============================================================
# SQLite 工具
# ============================================================

def _init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS tt (key TEXT PRIMARY KEY, pn INTEGER, dn INTEGER)")
    db.commit()
    return db


def _set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))


def _get_meta(db: sqlite3.Connection, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _flush_tt_from_log(db: sqlite3.Connection, solver, already_flushed: int) -> None:
    """增量写入：只写 tt_log 中 index >= already_flushed 的新条目。O(新增量)。"""
    new_keys = solver.tt_log[already_flushed:]
    if new_keys:
        tt = solver.tt
        db.executemany("INSERT OR REPLACE INTO tt (key, pn, dn) VALUES (?, ?, ?)",
                       [(_tt_key_to_str(k), tt[k][0], tt[k][1]) for k in new_keys])


def _tt_key_to_str(key) -> str:
    """TT key (tuple or str) → SQLite 存储字符串。"""
    if isinstance(key, tuple):
        return f"{key[0]}|{key[1]}|{key[2]}"
    return key


def load_tt_from_sqlite(db_path: str) -> Dict:
    """从 SQLite 加载全部 TT 到内存 dict。key 为 "zh|turn|lc" 字符串。"""
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT key, pn, dn FROM tt").fetchall()
    db.close()
    # 转为 tuple key 以匹配 solver 的 _tt_key 格式
    tt = {}
    for key_str, pn, dn in rows:
        parts = key_str.split("|")
        tt[(int(parts[0]), int(parts[1]), int(parts[2]))] = (pn, dn)
    return tt


# ============================================================
# 查表求解（预处理完成后）
# ============================================================

def solve_from_cache(tt: Dict[str, Tuple[int, int]],
                     board: Board, turn: int, region_mask: List[int],
                     attacker_color: int) -> dict:
    """
    纯查表，不跑 df-pn。毫秒级。
    顽抗着：选对手最难推进的着法（最有可能让对手犯错）。
    """
    size = board.size

    def tt_key(t: int) -> tuple:
        return (board.zh, t, board.last_capture)

    root_pn, root_dn = tt.get(tt_key(turn), (1, 1))
    if root_pn == 0:
        result = "ATTACKER_WINS"
    elif root_dn == 0:
        result = "DEFENDER_WINS"
    else:
        result = "UNPROVEN"

    is_or = (turn == attacker_color)
    winning_move = None
    resist_move = None
    resist_score = -1
    any_move = None

    for x, y in board.legal_moves_in_region(turn, region_mask):
        if any_move is None:
            any_move = (x, y)
        u = board.play_undoable(x, y, turn)
        if u is None:
            continue
        child_pn, child_dn = tt.get(tt_key(-turn), (1, 1))
        board.undo(u)

        # 胜着
        if is_or and child_pn == 0:
            winning_move = (x, y)
            break
        elif not is_or and child_dn == 0:
            winning_move = (x, y)
            break

        # 顽抗着：对手证胜代价最大 = 对手最容易犯错
        score = child_pn if is_or else child_dn
        if score > resist_score:
            resist_score = score
            resist_move = (x, y)

    if winning_move:
        move = {"x": winning_move[0], "y": winning_move[1], "certain": True}
    elif resist_move:
        move = {"x": resist_move[0], "y": resist_move[1], "certain": False}
    elif any_move:
        move = {"x": any_move[0], "y": any_move[1], "certain": False}
    else:
        move = None

    return {"result": result, "move": move}


# ============================================================
# 单 worker
# ============================================================

def _worker_solve(board_grid: List[int], last_capture: int,
                  region: List[int],
                  kill_targets: List[List[int]], defend_targets: List[List[int]],
                  attacker_color: int, first_turn: int,
                  my_moves: List[Tuple[int, int]],
                  db_path: str, progress_path: str) -> None:
    """单个 worker：对分到的每个根候选着法跑 df-pn，增量写入 SQLite。"""
    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture

    db = _init_db(db_path)
    db.execute("CREATE TABLE IF NOT EXISTS results "
               "(move TEXT PRIMARY KEY, pn INTEGER, dn INTEGER, nodes INTEGER)")
    db.commit()

    total_nodes = 0
    t0 = time.monotonic()
    BATCH = 10_000

    for mx, my in my_moves:
        u = board.play_undoable(mx, my, first_turn)
        if u is None:
            continue

        last_flush = 0

        def on_progress(info):
            nonlocal last_flush
            new_count = len(solver.tt_log)
            if new_count - last_flush >= BATCH:
                _flush_tt_from_log(db, solver, last_flush)
                last_flush = new_count
                db.commit()
            info["status"] = "running"
            info["total_nodes"] = total_nodes + info["nodes"]
            info["current_move"] = [mx, my]
            info["tt_flushed"] = last_flush
            info["tt_size"] = len(solver.tt)
            try:
                with open(progress_path, "w") as f:
                    json.dump(info, f)
            except OSError:
                pass

        solver = DfpnSolver(
            board, region,
            attacker_color=attacker_color,
            kill_targets=[tuple(c) for c in kill_targets],
            defend_targets=[tuple(c) for c in defend_targets],
            progress_callback=on_progress,
        )
        r = solver.solve(-first_turn)
        total_nodes += r["nodes"]

        # flush 剩余
        _flush_tt_from_log(db, solver, last_flush)
        db.execute("INSERT OR REPLACE INTO results (move, pn, dn, nodes) VALUES (?, ?, ?, ?)",
                   (f"{mx},{my}", r["pn"], r["dn"], r["nodes"]))
        db.commit()
        board.undo(u)

    db.close()
    try:
        with open(progress_path, "w") as f:
            json.dump({"status": "done", "total_nodes": total_nodes,
                       "elapsed_ms": int((time.monotonic() - t0) * 1000)}, f)
    except OSError:
        pass


# ============================================================
# 进度汇总
# ============================================================

def _aggregate_progress(workers: List[dict], progress_path: str,
                        start_time: float) -> None:
    total_nodes = 0
    total_tt = 0
    active = 0
    for w in workers:
        try:
            with open(w["progress"]) as f:
                wp = json.load(f)
            total_nodes += wp.get("total_nodes", wp.get("nodes", 0))
            total_tt += wp.get("tt_size", wp.get("tt_flushed", 0))
            if wp.get("status") == "running":
                active += 1
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    nps = int(total_nodes / max(elapsed_ms, 1) * 1000)
    try:
        with open(progress_path, "w") as f:
            json.dump({
                "status": "running" if active > 0 else "merging",
                "total_nodes": total_nodes,
                "total_tt": total_tt,
                "elapsed_ms": elapsed_ms,
                "nodes_per_sec": nps,
                "workers_active": active,
                "workers_total": len(workers),
            }, f)
    except OSError:
        pass


# ============================================================
# 合并分片 DB
# ============================================================

def _merge_worker_dbs(workers: List[dict], main_db_path: str,
                      attacker_color: int, first_turn: int) -> None:
    main_db = _init_db(main_db_path)
    child_results = []
    for w in workers:
        if not os.path.exists(w["db"]):
            continue
        wdb = sqlite3.connect(w["db"])
        rows = wdb.execute("SELECT key, pn, dn FROM tt").fetchall()
        if rows:
            main_db.executemany("INSERT OR REPLACE INTO tt (key, pn, dn) VALUES (?, ?, ?)", rows)
        results = wdb.execute("SELECT move, pn, dn FROM results").fetchall()
        child_results.extend(results)
        wdb.close()
    main_db.commit()

    # 计算根 pn/dn
    is_or = (first_turn == attacker_color)
    if child_results:
        if is_or:
            root_pn = min(pn for _, pn, _ in child_results)
            root_dn = min(sum(dn for _, _, dn in child_results), 10**9)
        else:
            root_pn = min(sum(pn for _, pn, _ in child_results), 10**9)
            root_dn = min(dn for _, _, dn in child_results)
    else:
        root_pn, root_dn = 10**9, 10**9

    result_str = "ATTACKER_WINS" if root_pn == 0 else (
        "DEFENDER_WINS" if root_dn == 0 else "UNPROVEN")

    _set_meta(main_db, "status", "done")
    _set_meta(main_db, "result", result_str)
    _set_meta(main_db, "root_pn", str(root_pn))
    _set_meta(main_db, "root_dn", str(root_dn))
    total_tt = main_db.execute("SELECT COUNT(*) FROM tt").fetchone()[0]
    _set_meta(main_db, "tt_size", str(total_tt))
    main_db.commit()
    main_db.close()


# ============================================================
# 多进程并行入口（coordinator）
# ============================================================

def run_precompute_parallel(board_grid: List[int], last_capture: int,
                            region: List[int],
                            kill_targets: List[List[int]],
                            defend_targets: List[List[int]],
                            attacker_color: int, first_turn: int,
                            db_path: str, progress_path: str,
                            num_workers: Optional[int] = None) -> None:
    """
    coordinator：生成根候选 → 分配到 W 个 worker → 等待 → 合并。
    """
    if num_workers is None:
        num_workers = max(1, mp.cpu_count() - 1)

    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture

    # 生成根候选
    tmp_solver = DfpnSolver(
        board, region, attacker_color=attacker_color,
        kill_targets=[tuple(c) for c in kill_targets],
        defend_targets=[tuple(c) for c in defend_targets],
        max_nodes=1, max_time_ms=1,
    )
    root_kids = tmp_solver._gen_children(first_turn, allow_pass=(first_turn != attacker_color))
    root_moves = [m for m, _ in root_kids if m is not None]

    if not root_moves:
        db = _init_db(db_path)
        _set_meta(db, "status", "done")
        _set_meta(db, "result", "DEFENDER_WINS")
        _set_meta(db, "root_pn", str(10**9))
        _set_meta(db, "root_dn", "0")
        db.commit(); db.close()
        with open(progress_path, "w") as f:
            json.dump({"status": "done", "total_nodes": 0}, f)
        return

    # LPT 调度：浅探估算难度，按难度降序交替分配
    difficulty = []
    for mx, my in root_moves:
        u = board.play_undoable(mx, my, first_turn)
        if u is None:
            difficulty.append(0)
            continue
        probe = DfpnSolver(
            board, region, attacker_color=attacker_color,
            kill_targets=[tuple(c) for c in kill_targets],
            defend_targets=[tuple(c) for c in defend_targets],
            max_nodes=500, max_time_ms=200,
        )
        r = probe.solve(-first_turn)
        difficulty.append(r["nodes"] if r["result"] == "UNPROVEN" else 0)
        board.undo(u)

    sorted_moves = sorted(zip(difficulty, root_moves), reverse=True)
    buckets: List[List[Tuple[int, int]]] = [[] for _ in range(num_workers)]
    for i, (_, move) in enumerate(sorted_moves):
        buckets[i % num_workers].append(move)

    # 启动 workers
    cache_dir = os.path.dirname(db_path) or "."
    job_id = os.path.splitext(os.path.basename(db_path))[0]
    workers = []
    start_time = time.monotonic()
    for wi in range(num_workers):
        if not buckets[wi]:
            continue
        wdb = os.path.join(cache_dir, f"{job_id}_w{wi}.db")
        wprog = os.path.join(cache_dir, f"{job_id}_w{wi}_progress.json")
        p = mp.Process(target=_worker_solve, args=(
            board_grid, last_capture, region,
            kill_targets, defend_targets, attacker_color, first_turn,
            buckets[wi], wdb, wprog,
        ))
        p.start()
        workers.append({"process": p, "db": wdb, "progress": wprog})

    # 写 worker PID 文件（供外部终止）
    pids_path = os.path.join(cache_dir, f"{job_id}_pids.json")
    try:
        with open(pids_path, "w") as f:
            json.dump([w["process"].pid for w in workers], f)
    except OSError:
        pass

    # 等待
    while any(w["process"].is_alive() for w in workers):
        _aggregate_progress(workers, progress_path, start_time)
        time.sleep(2)
    _aggregate_progress(workers, progress_path, start_time)

    # 合并
    _merge_worker_dbs(workers, db_path, attacker_color, first_turn)

    # 最终进度
    with open(progress_path, "w") as f:
        json.dump({"status": "done", "total_nodes": 0,
                   "message": "merged"}, f)
