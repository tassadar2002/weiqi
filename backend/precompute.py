"""
预处理系统

- run_precompute_parallel: 多进程并行穷举 df-pn
- solve_from_cache: 预处理完成后的查表求解
- load_tt_from_bin: 从二进制文件加载 TT 到内存

二进制缓存格式 (.bin):
  Header (20B): magic "WQ3C" + version u8 + status u8 + result u8 + pad u8
                + count u32 + root_pn u32 + root_dn u32
  Records (12B each): key 10B (zh u64 + turn i8 + lc u8) + pn u8 + dn u8
  pn/dn 编码: 0~254 原值, 255 = DFPN_INF (10^9)
  增量写入时允许重复 key，合并阶段去重（后覆盖前）。
"""

import json
import multiprocessing as mp
import os
import struct
import time
from typing import Dict, List, Optional, Tuple

from board import Board, BLACK, WHITE, EMPTY, BOARD_SIZE
from solver import DfpnSolver

DFPN_INF = 10**9
_MAGIC = b"WQ3C"
_HEADER_FMT = ">4sBBBxIII"  # 20 bytes
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 20
_RECORD_FMT = ">QbBBB"  # 12 bytes: zh(8) + turn(1) + lc(1) + pn(1) + dn(1)
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)  # 12

# status: 0=running 1=done 2=stopped
# result: 0=UNPROVEN 1=ATTACKER_WINS 2=DEFENDER_WINS
_RESULT_MAP = {"UNPROVEN": 0, "ATTACKER_WINS": 1, "DEFENDER_WINS": 2}
_RESULT_RMAP = {0: "UNPROVEN", 1: "ATTACKER_WINS", 2: "DEFENDER_WINS"}


# ============================================================
# 二进制读写
# ============================================================

def _encode_pn(v: int) -> int:
    return 255 if v >= 255 else v

def _decode_pn(v: int) -> int:
    return DFPN_INF if v == 255 else v

def _pack_key(key: tuple) -> bytes:
    """TT key (zh, turn, lc) → 10 字节。"""
    zh, turn, lc = key
    return struct.pack(">QbB", zh, turn, lc if lc >= 0 else 255)

def _unpack_key(blob: bytes) -> tuple:
    """10 字节 → (zh, turn, lc)。"""
    zh, turn, lc_u8 = struct.unpack(">QbB", blob)
    return (zh, turn, -1 if lc_u8 == 255 else lc_u8)

def _write_header(f, status=0, result=0, count=0, root_pn=0, root_dn=0):
    f.seek(0)
    f.write(struct.pack(_HEADER_FMT, _MAGIC, 1, status, result, count, root_pn, root_dn))

def _read_header(path: str) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            data = f.read(_HEADER_SIZE)
        if len(data) < _HEADER_SIZE:
            return None
        magic, ver, status, result, count, root_pn, root_dn = struct.unpack(_HEADER_FMT, data)
        if magic != _MAGIC:
            return None
        return {
            "status": status, "result": _RESULT_RMAP.get(result, "UNPROVEN"),
            "count": count, "root_pn": root_pn, "root_dn": root_dn,
        }
    except (OSError, struct.error):
        return None

def _flush_records(f, solver, already_flushed: int) -> int:
    """增量追加新 TT 记录到文件。返回新的 flushed 计数。"""
    new_keys = solver.tt_log[already_flushed:]
    if not new_keys:
        return already_flushed
    tt = solver.tt
    buf = bytearray(len(new_keys) * _RECORD_SIZE)
    offset = 0
    for k in new_keys:
        pn, dn = tt[k]
        zh, turn, lc = k
        struct.pack_into(_RECORD_FMT, buf, offset,
                         zh, turn, lc if lc >= 0 else 255,
                         _encode_pn(pn), _encode_pn(dn))
        offset += _RECORD_SIZE
    f.write(buf)
    f.flush()
    return already_flushed + len(new_keys)

def _read_records(path: str) -> Dict[tuple, Tuple[int, int]]:
    """读取 .bin 文件所有记录，后出现的 key 覆盖前面的（去重）。"""
    tt = {}
    try:
        with open(path, "rb") as f:
            header = f.read(_HEADER_SIZE)
            if len(header) < _HEADER_SIZE:
                return tt
            while True:
                rec = f.read(_RECORD_SIZE)
                if len(rec) < _RECORD_SIZE:
                    break
                zh, turn, lc_u8, pn_u8, dn_u8 = struct.unpack(_RECORD_FMT, rec)
                key = (zh, turn, -1 if lc_u8 == 255 else lc_u8)
                tt[key] = (_decode_pn(pn_u8), _decode_pn(dn_u8))
    except OSError:
        pass
    return tt

def load_tt_from_bin(path: str) -> Dict[tuple, Tuple[int, int]]:
    """从 .bin 文件加载 TT 到内存 dict。"""
    return _read_records(path)


# ============================================================
# 查表求解（预处理完成后）
# ============================================================

def solve_from_cache(tt: Dict[tuple, Tuple[int, int]],
                     board: Board, turn: int, region_mask: List[int],
                     attacker_color: int) -> dict:
    """纯查表，不跑 df-pn。毫秒级。"""

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

        if is_or and child_pn == 0:
            winning_move = (x, y)
            break
        elif not is_or and child_dn == 0:
            winning_move = (x, y)
            break

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
                  bin_path: str, progress_path: str) -> None:
    """单个 worker：对分到的每个根候选着法跑 df-pn，增量写入 .bin。"""
    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture
    board.rebuild_zh()

    os.makedirs(os.path.dirname(bin_path) or ".", exist_ok=True)
    f = open(bin_path, "wb")
    _write_header(f, status=0)  # running

    total_nodes = 0
    t0 = time.monotonic()
    BATCH = 10_000
    child_results = []  # [(move_str, pn, dn, nodes)]

    for mx, my in my_moves:
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
            info["tt_size"] = len(solver.tt)
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

        # flush 剩余
        _flush_records(f, solver, last_flush)
        child_results.append((f"{mx},{my}", r["pn"], r["dn"], r["nodes"]))
        board.undo(u)

    f.close()

    # 写 child_results 到 JSON（供 coordinator 合并时计算根 pn/dn）
    results_path = bin_path + ".results.json"
    try:
        with open(results_path, "w") as rf:
            json.dump(child_results, rf)
    except OSError:
        pass

    try:
        with open(progress_path, "w") as pf:
            json.dump({"status": "done", "total_nodes": total_nodes,
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
# 合并 worker .bin 文件
# ============================================================

def _merge_worker_bins(workers: List[dict], main_bin_path: str,
                       attacker_color: int, first_turn: int) -> None:
    """读所有 worker .bin → dict 去重 → 写主 .bin + header。"""
    merged_tt: Dict[tuple, Tuple[int, int]] = {}
    child_results = []

    for w in workers:
        # 读 TT records（后覆盖前，自动去重）
        wtt = _read_records(w["bin"])
        merged_tt.update(wtt)
        # 读 child results
        results_path = w["bin"] + ".results.json"
        try:
            with open(results_path) as rf:
                for move_str, pn, dn, nodes in json.load(rf):
                    child_results.append((move_str, pn, dn))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    # 计算根 pn/dn
    is_or = (first_turn == attacker_color)
    if child_results:
        if is_or:
            root_pn = min(pn for _, pn, _ in child_results)
            root_dn = min(sum(dn for _, _, dn in child_results), DFPN_INF)
        else:
            root_pn = min(sum(pn for _, pn, _ in child_results), DFPN_INF)
            root_dn = min(dn for _, _, dn in child_results)
    else:
        root_pn, root_dn = DFPN_INF, DFPN_INF

    result_str = "ATTACKER_WINS" if root_pn == 0 else (
        "DEFENDER_WINS" if root_dn == 0 else "UNPROVEN")

    # 写主 .bin
    os.makedirs(os.path.dirname(main_bin_path) or ".", exist_ok=True)
    with open(main_bin_path, "wb") as f:
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=len(merged_tt), root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))
        for key, (pn, dn) in merged_tt.items():
            zh, turn, lc = key
            f.write(struct.pack(_RECORD_FMT,
                                zh, turn, lc if lc >= 0 else 255,
                                _encode_pn(pn), _encode_pn(dn)))

    # 清理 worker 临时文件
    for w in workers:
        for path in (w["bin"], w["bin"] + ".results.json", w["progress"]):
            try:
                os.remove(path)
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

    # 启动 workers
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

    # 写 worker PID 文件
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
    _merge_worker_bins(workers, bin_path, attacker_color, first_turn)

    # 清理 pids 文件
    try:
        os.remove(pids_path)
    except OSError:
        pass

    # 最终进度
    with open(progress_path, "w") as f:
        json.dump({"status": "done", "total_nodes": 0, "message": "merged"}, f)


# ============================================================
# CLI 入口（供 subprocess 调用，确保用 pypy3 运行）
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
