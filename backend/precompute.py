"""
预处理系统 — 小任务 + 任务队列

架构：
  - Coordinator: 生成根候选 → 放入任务队列 → 事件循环（监控/崩溃恢复）→ 合并
  - Worker: 无状态，循环取任务执行，未完成放回队列
  - DiskTT: 内存缓冲 + 磁盘 mmap，内存可控

每个任务 = 一个根着点 + 节点预算。Worker 用 DiskTT 执行 df-pn，
预算用完未证明 → 放回队列（任何 worker 可续传）。
"""

import heapq
import json
import multiprocessing as mp
import os
import time
from typing import Dict, List, Optional, Tuple

from bincache import (DFPN_INF, BinCache, DiskTT, _HEADER_SIZE, _RECORD_SIZE,
                      _RESULT_MAP, _calc_max_tt_entries, _calc_num_workers,
                      _iter_records, _pack_record, _read_header, _write_header)
from board import BLACK, BOARD_SIZE, Board
from solver import DfpnSolver

# 默认每段节点预算
DEFAULT_BUDGET = 5_000_000


# ============================================================
# 无状态 Worker
# ============================================================

def _worker_loop(task_queue: mp.Queue, result_queue: mp.Queue,
                 heartbeat_queue: mp.Queue,
                 board_grid: List[int], last_capture: int,
                 region: List[int],
                 kill_targets: List[List[int]], defend_targets: List[List[int]],
                 attacker_color: int, first_turn: int,
                 max_entries: int, budget: int,
                 progress_path: str) -> None:
    """无状态 worker：循环取任务、执行、放回或汇报结果。"""
    import sys as _sys
    needed = 80 * 3 + 200
    if _sys.getrecursionlimit() < needed:
        _sys.setrecursionlimit(needed)

    pid = os.getpid()
    total_nodes = 0
    tasks_done = 0
    t0 = time.monotonic()

    while True:
        task = task_queue.get()
        if task is None:  # poison pill → 退出
            break

        root_move, bin_path = task
        mx, my = root_move
        heartbeat_queue.put(("start", root_move, pid))

        board = Board(BOARD_SIZE)
        board.grid = list(board_grid)
        board.last_capture = last_capture
        board.rebuild_zh()

        u = board.play_undoable(mx, my, first_turn)
        if u is None:
            result_queue.put((root_move, "ILLEGAL", 0, 0, 0))
            heartbeat_queue.put(("done", root_move, pid))
            continue

        disk_tt = DiskTT(bin_path, max_entries)

        def on_progress(info):
            info["status"] = "running"
            info["total_nodes"] = total_nodes + info["nodes"]
            info["current_move"] = [mx, my]
            info["pid"] = pid
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
            max_nodes=budget,
            progress_callback=on_progress,
            tt=disk_tt,
        )
        r = solver.solve(-first_turn)
        total_nodes += r["nodes"]
        tasks_done += 1

        disk_tt.close()
        board.undo(u)

        if r["result"] == "UNPROVEN":
            # 未证明 → 放回队列
            task_queue.put(task)
            heartbeat_queue.put(("requeue", root_move, pid))
        else:
            # 已证明 → 汇报
            result_queue.put((root_move, r["result"], r["pn"], r["dn"], r["nodes"]))
            heartbeat_queue.put(("done", root_move, pid))

        # 更新 worker 进度
        try:
            with open(progress_path, "w") as pf:
                json.dump({
                    "status": "running",
                    "total_nodes": total_nodes,
                    "tasks_done": tasks_done,
                    "pid": pid,
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }, pf)
        except OSError:
            pass

    # Worker 退出
    try:
        with open(progress_path, "w") as pf:
            json.dump({
                "status": "done",
                "total_nodes": total_nodes,
                "tasks_done": tasks_done,
                "pid": pid,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }, pf)
    except OSError:
        pass


# ============================================================
# 进度汇总
# ============================================================

def _aggregate_progress(worker_progress_paths: List[str], progress_path: str,
                        start_time: float, done_count: int, total_count: int) -> None:
    total_nodes = 0
    total_tasks = 0
    active = 0
    for wp_path in worker_progress_paths:
        try:
            with open(wp_path) as f:
                wp = json.load(f)
            total_nodes += wp.get("total_nodes", 0)
            total_tasks += wp.get("tasks_done", 0)
            if wp.get("status") == "running":
                active += 1
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    nps = int(total_nodes / max(elapsed_ms, 1) * 1000)
    try:
        with open(progress_path, "w") as f:
            json.dump({
                "status": "running",
                "total_nodes": total_nodes,
                "elapsed_ms": elapsed_ms,
                "nodes_per_sec": nps,
                "workers_active": active,
                "done_moves": done_count,
                "total_moves": total_count,
            }, f)
    except OSError:
        pass


# ============================================================
# 合并所有根着 .bin → 主 .bin
# ============================================================

def _merge_all_bins(all_results: dict, cache_dir: str, job_id: str,
                    main_bin_path: str, attacker_color: int, first_turn: int) -> None:
    """k-way 归并所有根着的排序 .bin → 主 .bin。"""
    # 计算根 pn/dn
    is_or = (first_turn == attacker_color)
    child_pns = []
    child_dns = []
    for move, (result, pn, dn, _nodes) in all_results.items():
        child_pns.append(pn)
        child_dns.append(dn)

    if child_pns:
        if is_or:
            root_pn = min(child_pns)
            root_dn = min(sum(child_dns), DFPN_INF)
        else:
            root_pn = min(sum(child_pns), DFPN_INF)
            root_dn = min(child_dns)
    else:
        root_pn, root_dn = DFPN_INF, DFPN_INF

    result_str = "ATTACKER_WINS" if root_pn == 0 else (
        "DEFENDER_WINS" if root_dn == 0 else "UNPROVEN")

    # 收集所有根着的 .bin 文件
    bin_paths = []
    for move in all_results:
        bp = os.path.join(cache_dir, f"{job_id}_{move[0]}_{move[1]}.bin")
        if os.path.exists(bp) and os.path.getsize(bp) >= _HEADER_SIZE:
            bin_paths.append(bp)

    # k-way 归并
    os.makedirs(os.path.dirname(main_bin_path) or ".", exist_ok=True)
    iters = [_iter_records(p) for p in bin_paths]
    with open(main_bin_path, "wb") as f:
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=0, root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))
        count = 0
        for rec in heapq.merge(*iters):
            f.write(rec)
            count += 1
        f.seek(0)
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=count, root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))

    # 清理根着 .bin 和临时文件
    for bp in bin_paths:
        for suffix in ("", ".tmp", ".flush0.tmp", ".flush1.tmp"):
            try:
                os.remove(bp + suffix)
            except OSError:
                pass


# ============================================================
# Coordinator 辅助
# ============================================================

def _gen_root_moves(board_grid: List[int], last_capture: int, region: List[int],
                    kill_targets: List[List[int]], defend_targets: List[List[int]],
                    attacker_color: int, first_turn: int) -> List[Tuple[int, int]]:
    """生成所有合法的根着法。"""
    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture
    board.rebuild_zh()
    tmp = DfpnSolver(
        board, region, attacker_color=attacker_color,
        kill_targets=[tuple(c) for c in kill_targets],
        defend_targets=[tuple(c) for c in defend_targets],
        max_nodes=1, max_time_ms=1,
    )
    kids = tmp._gen_children(first_turn, allow_pass=(first_turn != attacker_color))
    return [m for m, _ in kids if m is not None]


def _start_workers(num_workers: int, task_queue, result_queue, heartbeat_queue,
                   board_grid, last_capture, region, kill_targets, defend_targets,
                   attacker_color, first_turn, max_entries, cache_dir, job_id):
    """启动 worker 进程，返回 (workers, worker_progress_paths)。"""
    workers = []
    progress_paths = []
    for wi in range(num_workers):
        wp = os.path.join(cache_dir, f"{job_id}_worker{wi}_progress.json")
        progress_paths.append(wp)
        p = mp.Process(target=_worker_loop, args=(
            task_queue, result_queue, heartbeat_queue,
            board_grid, last_capture, region,
            kill_targets, defend_targets, attacker_color, first_turn,
            max_entries, DEFAULT_BUDGET, wp,
        ))
        p.start()
        workers.append(p)
    return workers, progress_paths


def _drain_queues(heartbeat_queue, result_queue, in_flight, all_results):
    """非阻塞收集心跳和结果。"""
    while not heartbeat_queue.empty():
        try:
            action, move, pid = heartbeat_queue.get_nowait()
            if action == "start":
                in_flight[move] = pid
            elif action in ("done", "requeue"):
                in_flight.pop(move, None)
        except Exception:
            break
    while not result_queue.empty():
        try:
            move, result, pn, dn, nodes = result_queue.get_nowait()
            all_results[move] = (result, pn, dn, nodes)
        except Exception:
            break


def _recover_crashed_workers(workers, worker_progress, in_flight, all_results,
                             task_queue, result_queue, heartbeat_queue,
                             board_grid, last_capture, region,
                             kill_targets, defend_targets, attacker_color,
                             first_turn, max_entries, cache_dir, job_id):
    """检测崩溃的 worker，回收任务，启动替补。"""
    for i, p in enumerate(workers):
        if p.is_alive() or p.exitcode == 0:
            continue
        if p.exitcode is None:
            continue
        # 崩溃 → 回收 in_flight 任务
        dead_pid = p.pid
        for m in [m for m, pid in in_flight.items() if pid == dead_pid]:
            if m not in all_results:
                move_bin = os.path.join(cache_dir, f"{job_id}_{m[0]}_{m[1]}.bin")
                task_queue.put((m, move_bin))
            in_flight.pop(m, None)
        # 启动替补
        wp = worker_progress[i]
        new_p = mp.Process(target=_worker_loop, args=(
            task_queue, result_queue, heartbeat_queue,
            board_grid, last_capture, region,
            kill_targets, defend_targets, attacker_color, first_turn,
            max_entries, DEFAULT_BUDGET, wp,
        ))
        new_p.start()
        workers[i] = new_p


def _shutdown_workers(workers, task_queue, num_workers):
    """发送 poison pills 并等待所有 worker 退出。"""
    for _ in range(num_workers):
        task_queue.put(None)
    for p in workers:
        if p.is_alive():
            p.join(timeout=10)


def _cleanup(worker_progress, pids_path):
    """清理临时文件。"""
    for wp in worker_progress:
        try:
            os.remove(wp)
        except OSError:
            pass
    try:
        os.remove(pids_path)
    except OSError:
        pass


def _write_final_progress(progress_path, all_results, root_moves, start_time):
    """写入最终进度文件。"""
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    total_nodes = sum(v[3] for v in all_results.values())
    with open(progress_path, "w") as f:
        json.dump({
            "status": "done",
            "total_nodes": total_nodes,
            "elapsed_ms": elapsed_ms,
            "message": "merged",
            "done_moves": len(all_results),
            "total_moves": len(root_moves),
        }, f)


# ============================================================
# Coordinator（主入口）
# ============================================================

def run_precompute_parallel(board_grid: List[int], last_capture: int,
                            region: List[int],
                            kill_targets: List[List[int]],
                            defend_targets: List[List[int]],
                            attacker_color: int, first_turn: int,
                            bin_path: str, progress_path: str,
                            num_workers: Optional[int] = None) -> None:
    """Coordinator：生成根候选 → 任务队列 → 启动 worker → 事件循环 → 合并。"""
    if num_workers is None:
        num_workers = _calc_num_workers()
    max_entries = _calc_max_tt_entries(num_workers)

    cache_dir = os.path.dirname(bin_path) or "."
    job_id = os.path.splitext(os.path.basename(bin_path))[0]
    os.makedirs(cache_dir, exist_ok=True)

    # ── 1. 生成根候选 ──
    root_moves = _gen_root_moves(board_grid, last_capture, region,
                                 kill_targets, defend_targets,
                                 attacker_color, first_turn)
    if not root_moves:
        with open(bin_path, "wb") as f:
            _write_header(f, status=1, result=_RESULT_MAP["DEFENDER_WINS"],
                          count=0, root_pn=0xFFFFFFFF, root_dn=0)
        with open(progress_path, "w") as f:
            json.dump({"status": "done", "total_nodes": 0}, f)
        return

    # ── 2. 初始化队列 + 放入任务 ──
    task_queue = mp.Queue()
    result_queue = mp.Queue()
    heartbeat_queue = mp.Queue()
    for move in root_moves:
        move_bin = os.path.join(cache_dir, f"{job_id}_{move[0]}_{move[1]}.bin")
        task_queue.put((move, move_bin))

    # ── 3. 启动 workers ──
    workers, worker_progress = _start_workers(
        num_workers, task_queue, result_queue, heartbeat_queue,
        board_grid, last_capture, region, kill_targets, defend_targets,
        attacker_color, first_turn, max_entries, cache_dir, job_id)

    pids_path = os.path.join(cache_dir, f"{job_id}_pids.json")
    try:
        with open(pids_path, "w") as f:
            json.dump([p.pid for p in workers], f)
    except OSError:
        pass

    # ── 4. 事件循环 ──
    start_time = time.monotonic()
    all_results: Dict[Tuple[int, int], Tuple[str, int, int, int]] = {}
    in_flight: Dict[Tuple[int, int], int] = {}

    while len(all_results) < len(root_moves):
        _drain_queues(heartbeat_queue, result_queue, in_flight, all_results)
        _recover_crashed_workers(
            workers, worker_progress, in_flight, all_results,
            task_queue, result_queue, heartbeat_queue,
            board_grid, last_capture, region,
            kill_targets, defend_targets, attacker_color,
            first_turn, max_entries, cache_dir, job_id)
        _aggregate_progress(worker_progress, progress_path, start_time,
                            len(all_results), len(root_moves))
        time.sleep(2)

    # ── 5. 关闭 workers ──
    _shutdown_workers(workers, task_queue, num_workers)

    # ── 6. 合并 + 清理 ──
    _merge_all_bins(all_results, cache_dir, job_id, bin_path,
                    attacker_color, first_turn)
    _cleanup(worker_progress, pids_path)
    _write_final_progress(progress_path, all_results, root_moves, start_time)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: pypy3 precompute.py <config.json>", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
    run_precompute_parallel(
        cfg["board"], cfg["last_capture"], cfg["region"],
        cfg["kill_targets"], cfg["defend_targets"],
        cfg["attacker_color"], cfg["turn"],
        cfg["db_path"], cfg["progress_path"],
        cfg.get("num_workers"),
    )
