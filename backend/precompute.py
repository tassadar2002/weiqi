"""
预处理系统 — 多进程并行穷举 df-pn

- run_precompute_parallel: coordinator 入口（生成根候选 → 分配 worker → 监控 → 合并）
- _worker_solve: 单 worker 求解逻辑
"""

import heapq
import json
import logging
import multiprocessing as mp
import os
import time
from typing import Dict, List, Optional, Tuple

from bincache import (DFPN_INF, _HEADER_SIZE, _RECORD_SIZE, _RESULT_MAP,
                      _dump_sorted_bin, _flush_records, _iter_records,
                      _merge_worker_bins, _read_header, _write_header)
from board import BLACK, BOARD_SIZE, Board
from solver import DfpnSolver


# ============================================================
# 单 worker
# ============================================================

def _worker_solve(board_grid: List[int], last_capture: int,
                  region: List[int],
                  kill_targets: List[List[int]], defend_targets: List[List[int]],
                  attacker_color: int, first_turn: int,
                  my_moves: List[Tuple[int, int]],
                  bin_path: str, progress_path: str) -> None:
    """单个 worker：对分到的每个根候选着法跑 df-pn，最终输出排序去重的 .bin。"""
    import sys as _sys

    # 防递归溢出：max_depth=80，每层递归含 _mid + _check_limits 等帧，留 3x 余量
    needed = 80 * 3 + 200
    if _sys.getrecursionlimit() < needed:
        _sys.setrecursionlimit(needed)

    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture
    board.rebuild_zh()

    os.makedirs(os.path.dirname(bin_path) or ".", exist_ok=True)
    cache_dir = os.path.dirname(bin_path) or "."
    job_base = os.path.splitext(os.path.basename(bin_path))[0]

    # 增量 append 文件（运行中崩溃安全）
    tmp_path = bin_path + ".tmp"
    f = open(tmp_path, "wb")
    _write_header(f, status=0)

    total_nodes = 0
    total_tt_count = 0
    t0 = time.monotonic()
    BATCH = 10_000
    child_results = []
    move_bins = []  # 每个 move 的排序 .bin 路径（用于最终 k-way 合并）

    try:
        for mi, (mx, my) in enumerate(my_moves):
            u = board.play_undoable(mx, my, first_turn)
            if u is None:
                continue

            last_flush = 0

            def on_progress(info):
                nonlocal last_flush
                new_count = len(solver.tt_log)
                if new_count - last_flush >= BATCH:
                    last_flush = _flush_records(f, solver, last_flush)
                info["status"] = "running"
                info["total_nodes"] = total_nodes + info["nodes"]
                info["current_move"] = [mx, my]
                info["tt_flushed"] = last_flush
                info["tt_size"] = total_tt_count + len(solver.tt)
                try:
                    with open(progress_path, "w") as pf:
                        json.dump(info, pf)
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

            _flush_records(f, solver, last_flush)
            child_results.append((f"{mx},{my}", r["pn"], r["dn"], r["nodes"]))

            # 每个 move 完成后：排序写入独立 .bin，立即释放内存
            move_bin = os.path.join(cache_dir, f"{job_base}_m{mi}.bin")
            _dump_sorted_bin(move_bin, solver.tt, [])
            total_tt_count += len(solver.tt)
            move_bins.append(move_bin)
            del solver  # 释放 TT dict

            board.undo(u)

    except Exception as e:
        # 记录错误到 progress，让 coordinator 和 status 命令能看到
        import traceback
        try:
            with open(progress_path, "w") as pf:
                json.dump({"status": "crashed", "total_nodes": total_nodes,
                           "error": f"{type(e).__name__}: {e}",
                           "traceback": traceback.format_exc(),
                           "elapsed_ms": int((time.monotonic() - t0) * 1000)}, pf)
        except OSError:
            pass
        raise
    finally:
        f.close()

    # k-way 合并所有 per-move .bin → 最终 worker .bin
    if move_bins:
        iters = [_iter_records(p) for p in move_bins if os.path.exists(p)]
        count = 0
        with open(bin_path, "wb") as out:
            _write_header(out, status=1, count=0)
            for rec in heapq.merge(*iters):
                out.write(rec)
                count += 1
            out.seek(0)
            _write_header(out, status=1, count=count)
        # 清理 per-move 临时文件
        for p in move_bins:
            for suffix in ("", ".results.json"):
                try:
                    os.remove(p + suffix)
                except OSError:
                    pass
    else:
        # 无有效 move，写空 .bin
        with open(bin_path, "wb") as out:
            _write_header(out, status=1, count=0)

    # 清理增量 tmp
    try:
        os.remove(tmp_path)
    except OSError:
        pass

    # 写 child_results
    results_path = bin_path + ".results.json"
    try:
        with open(results_path, "w") as rf:
            json.dump(child_results, rf)
    except OSError:
        pass

    try:
        with open(progress_path, "w") as pf:
            json.dump({"status": "done", "total_nodes": total_nodes,
                       "tt_size": total_tt_count,
                       "elapsed_ms": int((time.monotonic() - t0) * 1000)}, pf)
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
# 多进程并行入口（coordinator）
# ============================================================

def run_precompute_parallel(board_grid: List[int], last_capture: int,
                            region: List[int],
                            kill_targets: List[List[int]],
                            defend_targets: List[List[int]],
                            attacker_color: int, first_turn: int,
                            bin_path: str, progress_path: str,
                            num_workers: Optional[int] = None) -> None:
    """coordinator：生成根候选 → 分配到 W 个 worker → 等待 → 合并。"""
    if num_workers is None:
        num_workers = max(1, mp.cpu_count() - 1)

    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture
    board.rebuild_zh()

    tmp_solver = DfpnSolver(
        board, region, attacker_color=attacker_color,
        kill_targets=[tuple(c) for c in kill_targets],
        defend_targets=[tuple(c) for c in defend_targets],
        max_nodes=1, max_time_ms=1,
    )
    root_kids = tmp_solver._gen_children(first_turn, allow_pass=(first_turn != attacker_color))
    root_moves = [m for m, _ in root_kids if m is not None]

    if not root_moves:
        os.makedirs(os.path.dirname(bin_path) or ".", exist_ok=True)
        with open(bin_path, "wb") as f:
            _write_header(f, status=1, result=_RESULT_MAP["DEFENDER_WINS"],
                          count=0, root_pn=0xFFFFFFFF, root_dn=0)
        with open(progress_path, "w") as f:
            json.dump({"status": "done", "total_nodes": 0}, f)
        return

    # LPT 调度
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

    cache_dir = os.path.dirname(bin_path) or "."
    job_id = os.path.splitext(os.path.basename(bin_path))[0]
    workers = []
    start_time = time.monotonic()
    for wi in range(num_workers):
        if not buckets[wi]:
            continue
        wbin = os.path.join(cache_dir, f"{job_id}_w{wi}.bin")
        wprog = os.path.join(cache_dir, f"{job_id}_w{wi}_progress.json")
        p = mp.Process(target=_worker_solve, args=(
            board_grid, last_capture, region,
            kill_targets, defend_targets, attacker_color, first_turn,
            buckets[wi], wbin, wprog,
        ))
        p.start()
        workers.append({"process": p, "bin": wbin, "progress": wprog})

    pids_path = os.path.join(cache_dir, f"{job_id}_pids.json")
    try:
        with open(pids_path, "w") as f:
            json.dump([w["process"].pid for w in workers], f)
    except OSError:
        pass

    # 监控 worker 进程
    crashed_workers = []
    reported_crash = set()
    while any(w["process"].is_alive() for w in workers):
        _aggregate_progress(workers, progress_path, start_time)
        # 检查已退出的 worker
        for i, w in enumerate(workers):
            p = w["process"]
            if not p.is_alive() and i not in reported_crash:
                reported_crash.add(i)
                if p.exitcode != 0:
                    crashed_workers.append((i, p.exitcode, p.pid))
                    logging.warning("worker %d (pid=%d) 异常退出, exitcode=%d",
                                    i, p.pid, p.exitcode)
        time.sleep(2)

    # 最终检查所有 worker 退出码
    for i, w in enumerate(workers):
        p = w["process"]
        if i not in reported_crash and p.exitcode != 0:
            crashed_workers.append((i, p.exitcode, p.pid))
            logging.warning("worker %d (pid=%d) 异常退出, exitcode=%d",
                            i, p.pid, p.exitcode)

    _aggregate_progress(workers, progress_path, start_time)

    if crashed_workers:
        logging.error("共 %d/%d 个 worker 异常退出: %s",
                      len(crashed_workers), len(workers),
                      ", ".join(f"w{i}(pid={pid},exit={ec})"
                                for i, ec, pid in crashed_workers))

    # 收集每个 worker 的最终状态（合并后 worker 文件会被清理，这里保留快照）
    crashed_set = {i for i, _, _ in crashed_workers}
    worker_snapshots = []
    for i, w in enumerate(workers):
        snap = {
            "worker": i,
            "pid": w["process"].pid,
            "exitcode": w["process"].exitcode,
            "moves": buckets[i] if i < len(buckets) else [],
        }
        # 读 worker progress
        try:
            with open(w["progress"]) as f:
                wp = json.load(f)
            snap["total_nodes"] = wp.get("total_nodes", wp.get("nodes", 0))
            snap["tt_size"] = wp.get("tt_size", wp.get("tt_flushed", 0))
            snap["elapsed_ms"] = wp.get("elapsed_ms", 0)
            snap["status"] = wp.get("status", "unknown")
            snap["current_move"] = wp.get("current_move")
            if wp.get("error"):
                snap["error"] = wp["error"]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            snap["status"] = "crashed" if i in crashed_set else "unknown"
            snap["total_nodes"] = 0
            snap["tt_size"] = 0
            snap["elapsed_ms"] = 0
        # 读 worker .bin header（如果存在）
        if os.path.exists(w["bin"]):
            whdr = _read_header(w["bin"])
            if whdr:
                snap["bin_count"] = whdr["count"]
        if i in crashed_set:
            snap["status"] = "crashed"
        worker_snapshots.append(snap)

    # 只合并成功完成的 worker
    ok_workers = [w for i, w in enumerate(workers)
                  if w["process"].exitcode == 0 and os.path.exists(w["bin"])]
    if ok_workers:
        _merge_worker_bins(ok_workers, bin_path, attacker_color, first_turn)
    else:
        # 所有 worker 都失败了，写一个空的标记文件
        os.makedirs(os.path.dirname(bin_path) or ".", exist_ok=True)
        with open(bin_path, "wb") as f:
            _write_header(f, status=2, result=0, count=0)

    # 清理失败 worker 的临时文件
    for i, ec, pid in crashed_workers:
        w = workers[i]
        for path in (w["bin"], w["bin"] + ".tmp",
                     w["bin"] + ".results.json", w["progress"]):
            try:
                os.remove(path)
            except OSError:
                pass

    try:
        os.remove(pids_path)
    except OSError:
        pass

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    total_nodes = sum(s.get("total_nodes", 0) for s in worker_snapshots)
    msg = "merged"
    if crashed_workers:
        msg = (f"merged ({len(ok_workers)}/{len(workers)} workers ok, "
               f"{len(crashed_workers)} crashed)")
    with open(progress_path, "w") as f:
        json.dump({"status": "done", "total_nodes": total_nodes,
                   "elapsed_ms": elapsed_ms,
                   "message": msg,
                   "crashed_workers": len(crashed_workers),
                   "total_workers": len(workers),
                   "workers": worker_snapshots}, f)
