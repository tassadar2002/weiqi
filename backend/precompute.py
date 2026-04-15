"""
预处理系统

- run_precompute_parallel: 多进程并行穷举 df-pn
- solve_from_cache: 预处理完成后的查表求解
- BinCache: mmap + 二分查找，支持超大文件按需查询

二进制缓存格式 (.bin):
  Header (20B): magic "WQ3C" + version u8 + status u8 + result u8 + pad u8
                + count u32 + root_pn u32 + root_dn u32
  Records (12B each, 按 key 排序): key 10B (zh u64 + turn i8 + lc u8) + pn u8 + dn u8
  pn/dn 编码: 0~254 原值, 255 = DFPN_INF (10^9)
  主 .bin 文件中 records 按 key 排序，支持二分查找。
"""

import heapq
import json
import mmap
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
_RECORD_FMT = ">QBBBB"  # 12 bytes: zh(8) + turn(1) + lc(1) + pn(1) + dn(1)
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

def _encode_turn(turn: int) -> int:
    """turn (-1 or 1) → unsigned byte (0 or 2)。保证字节序 = 数值序。"""
    return turn + 1  # -1→0, 1→2

def _decode_turn(v: int) -> int:
    return v - 1  # 0→-1, 2→1

def _encode_lc(lc: int) -> int:
    """last_capture (-1 or 0~168) → unsigned byte。-1 排最前（编码为 0），0~168 编码为 1~169。"""
    return 0 if lc < 0 else lc + 1

def _decode_lc(v: int) -> int:
    return -1 if v == 0 else v - 1

def _pack_key(key: tuple) -> bytes:
    """TT key (zh, turn, lc) → 10 字节。大端序，字节比较 = 数值比较。"""
    zh, turn, lc = key
    return struct.pack(">QBB", zh, _encode_turn(turn), _encode_lc(lc))

def _pack_record(key: tuple, pn: int, dn: int) -> bytes:
    """TT (key, pn, dn) → 12 字节。"""
    zh, turn, lc = key
    return struct.pack(_RECORD_FMT, zh, _encode_turn(turn), _encode_lc(lc),
                       _encode_pn(pn), _encode_pn(dn))

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
    """增量追加新 TT 记录到文件（运行中崩溃安全）。"""
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
                         zh, _encode_turn(turn), _encode_lc(lc),
                         _encode_pn(pn), _encode_pn(dn))
        offset += _RECORD_SIZE
    f.write(buf)
    f.flush()
    return already_flushed + len(new_keys)

def _dump_sorted_bin(bin_path: str, tt: Dict[tuple, Tuple[int, int]],
                     child_results: list) -> None:
    """将内存 TT dict 排序后写入 .bin 文件（去重 + 排序）。"""
    items = sorted(tt.items())
    with open(bin_path, "wb") as f:
        _write_header(f, status=1, count=len(items))
        for key, (pn, dn) in items:
            f.write(_pack_record(key, pn, dn))
    # child_results 写到 JSON
    results_path = bin_path + ".results.json"
    try:
        with open(results_path, "w") as rf:
            json.dump(child_results, rf)
    except OSError:
        pass


# ============================================================
# BinCache: mmap + 二分查找
# ============================================================

class BinCache:
    """mmap 映射 .bin 文件，二分查找按需查询。内存占用 ~0。"""

    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "rb")
        size = os.fstat(self.f.fileno()).st_size
        if size < _HEADER_SIZE:
            self.mm = None
            self.count = 0
            self.header = None
            return
        self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        self.header = _read_header(path)
        self.count = self.header["count"] if self.header else 0

    def lookup(self, key: tuple) -> Optional[Tuple[int, int]]:
        if self.mm is None or self.count == 0:
            return None
        target = _pack_key(key)
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            off = _HEADER_SIZE + mid * _RECORD_SIZE
            rec_key = self.mm[off:off + 10]
            if rec_key < target:
                lo = mid + 1
            elif rec_key > target:
                hi = mid
            else:
                return (_decode_pn(self.mm[off + 10]), _decode_pn(self.mm[off + 11]))
        return None

    def close(self):
        if self.mm:
            self.mm.close()
            self.mm = None
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# 查表求解（预处理完成后）
# ============================================================

def solve_from_cache(cache: BinCache,
                     board: Board, turn: int, region_mask: List[int],
                     attacker_color: int) -> dict:
    """纯查表（mmap 二分查找），不跑 df-pn。毫秒级。"""

    def tt_lookup(t: int) -> Tuple[int, int]:
        r = cache.lookup((board.zh, t, board.last_capture))
        return r if r else (1, 1)

    root_pn, root_dn = tt_lookup(turn)
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
        child_pn, child_dn = tt_lookup(-turn)
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
    """单个 worker：对分到的每个根候选着法跑 df-pn，最终输出排序去重的 .bin。"""
    board = Board(BOARD_SIZE)
    board.grid = list(board_grid)
    board.last_capture = last_capture
    board.rebuild_zh()

    os.makedirs(os.path.dirname(bin_path) or ".", exist_ok=True)
    # 增量 append 文件（运行中崩溃安全）
    tmp_path = bin_path + ".tmp"
    f = open(tmp_path, "wb")
    _write_header(f, status=0)

    total_nodes = 0
    t0 = time.monotonic()
    BATCH = 10_000
    child_results = []
    all_tt: Dict[tuple, Tuple[int, int]] = {}  # 跨 move 的合并 TT

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
            info["tt_size"] = len(solver.tt) + len(all_tt)
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
        # 合并到 all_tt（去重）
        all_tt.update(solver.tt)
        board.undo(u)

    f.close()

    # 最终输出：排序去重的 .bin（覆盖 tmp）
    _dump_sorted_bin(bin_path, all_tt, child_results)
    # 删除 tmp
    try:
        os.remove(tmp_path)
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
# k-way 归并排序合并 worker .bin
# ============================================================

def _iter_records(path: str):
    """流式读取已排序 .bin 的记录，yield 12 字节 bytes。"""
    with open(path, "rb") as f:
        f.seek(_HEADER_SIZE)
        while True:
            rec = f.read(_RECORD_SIZE)
            if len(rec) < _RECORD_SIZE:
                break
            yield rec


def _merge_worker_bins(workers: List[dict], main_bin_path: str,
                       attacker_color: int, first_turn: int) -> None:
    """k-way 归并已排序的 worker .bin → 排序的主 .bin。流式，内存 ~0。"""
    child_results = []
    for w in workers:
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

    # k-way 归并（各 worker .bin 已排序，key 不重叠）
    iters = [_iter_records(w["bin"]) for w in workers if os.path.exists(w["bin"])]
    os.makedirs(os.path.dirname(main_bin_path) or ".", exist_ok=True)
    with open(main_bin_path, "wb") as f:
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=0, root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))
        count = 0
        for rec in heapq.merge(*iters):
            f.write(rec)
            count += 1
        # 回写 count
        f.seek(0)
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=count, root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))

    # 清理 worker 临时文件
    for w in workers:
        for path in (w["bin"], w["bin"] + ".tmp", w["bin"] + ".results.json", w["progress"]):
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
                    import logging
                    logging.warning("worker %d (pid=%d) 异常退出, exitcode=%d",
                                    i, p.pid, p.exitcode)
        time.sleep(2)

    # 最终检查所有 worker 退出码
    for i, w in enumerate(workers):
        p = w["process"]
        if i not in reported_crash and p.exitcode != 0:
            crashed_workers.append((i, p.exitcode, p.pid))
            import logging
            logging.warning("worker %d (pid=%d) 异常退出, exitcode=%d",
                            i, p.pid, p.exitcode)

    _aggregate_progress(workers, progress_path, start_time)

    if crashed_workers:
        import logging
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


# ============================================================
# CLI 入口
# ============================================================

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_PROJECT_ROOT, "backend", "data", "problems.db")
_CACHE_DIR = os.path.join(_PROJECT_ROOT, "backend", "cache")


def _cli_list():
    """列出所有已保存的题目。"""
    from problems import init_db, list_problems
    init_db(_DB_PATH)
    problems = list_problems(_DB_PATH)
    if not problems:
        print("暂无题目。请先在浏览器中创建题目并设定目标。")
        return
    fmt = "{:<14s} {:<14s} {:>3s} {:>3s} {:>4s} {:>3s} {:>3s}  {}"
    print(fmt.format("ID", "名称", "黑", "白", "区域", "杀", "守", "预处理"))
    print("-" * 72)
    for p in problems:
        name = (p["name"] or "")[:12]
        status = {"done": "✓ 已完成", "running": "⋯ 进行中", "none": "✗ 未处理"}.get(
            p["precompute_status"], p["precompute_status"])
        print(fmt.format(
            p["id"], name,
            str(p["black_count"]), str(p["white_count"]),
            str(p["region_count"]),
            str(p["kill_count"]), str(p["defend_count"]),
            status,
        ))


def _cli_status(problem_id: str):
    """查看指定题目的预处理状态。"""
    import glob as _glob
    from problems import get_problem, init_db
    init_db(_DB_PATH)
    p = get_problem(_DB_PATH, problem_id)
    if not p:
        print(f"错误：找不到题目 {problem_id}", file=sys.stderr)
        sys.exit(1)

    print(f"题目: {p['name']} ({problem_id})")
    print(f"杀目标: {p['kill_targets']}  守目标: {p['defend_targets']}")

    status = p["precompute_status"]
    job_id = p.get("precompute_job_id")
    status_label = {"done": "已完成", "running": "进行中", "none": "未开始"}.get(status, status)
    print(f"预处理状态: {status_label}")

    if not job_id:
        if status == "none":
            print("尚未运行过预处理。使用 run 子命令启动。")
        return

    print(f"job_id: {job_id}")
    bin_path = os.path.join(_CACHE_DIR, f"{job_id}.bin")
    progress_path = os.path.join(_CACHE_DIR, f"{job_id}_progress.json")

    # 读 .bin header
    if os.path.exists(bin_path):
        size_mb = os.path.getsize(bin_path) / (1024 * 1024)
        hdr = _read_header(bin_path)
        if hdr and hdr["status"] == 1:
            print(f"缓存文件: {bin_path} ({size_mb:.2f} MB)")
            print(f"结果: {hdr['result']}")
            print(f"TT 条目: {hdr['count']:,}")
            print(f"根节点 pn={hdr['root_pn']}  dn={hdr['root_dn']}")
        elif hdr and hdr["status"] == 2:
            print(f"缓存文件: {bin_path} (失败标记)")
        else:
            print(f"缓存文件: {bin_path} ({size_mb:.2f} MB, 未完成)")
    else:
        print(f"缓存文件: 不存在")

    # 读主 progress.json
    prog = None
    try:
        with open(progress_path) as f:
            prog = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    if prog:
        elapsed = prog.get("elapsed_ms", 0)
        print(f"搜索节点: {prog.get('total_nodes', 0):,}")
        tt = prog.get("total_tt", 0)
        if tt:
            print(f"TT 缓存: {tt:,} 条")
        nps = prog.get("nodes_per_sec", 0)
        if nps:
            print(f"速度: {nps:,} nodes/s")
        if elapsed > 0:
            print(f"用时: {_fmt_duration(int(elapsed))}")
        wa = prog.get("workers_active")
        wt = prog.get("workers_total")
        if wa is not None and wt is not None:
            print(f"进程: {wa}/{wt} 活跃")
        n_crashed = prog.get("crashed_workers", 0)
        n_total_w = prog.get("total_workers", 0)
        if n_crashed > 0:
            print(f"警告: {n_crashed}/{n_total_w} 个 worker 异常退出")

    # ---- worker 明细 ----
    # 优先从主 progress.json 的 workers 快照读取（任务已完成时 worker 文件已清理）
    worker_data = (prog or {}).get("workers")
    if worker_data:
        _print_worker_table(worker_data)
        return

    # 任务运行中：扫描 worker progress 文件
    pattern = os.path.join(_CACHE_DIR, f"{job_id}_w*_progress.json")
    wpaths = sorted(_glob.glob(pattern))
    if not wpaths:
        return

    # 读 pids.json 获取每个 worker 的 pid
    pids = []
    pids_path = os.path.join(_CACHE_DIR, f"{job_id}_pids.json")
    try:
        with open(pids_path) as f:
            pids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    live_workers = []
    for wp in wpaths:
        # 从文件名提取 worker index: {job_id}_w{i}_progress.json
        base = os.path.basename(wp)
        try:
            wi = int(base.split("_w")[1].split("_")[0])
        except (IndexError, ValueError):
            continue
        try:
            with open(wp) as f:
                wd = json.load(f)
        except (json.JSONDecodeError, OSError):
            wd = {}
        wbin = os.path.join(_CACHE_DIR, f"{job_id}_w{wi}.bin")
        pid = pids[wi] if wi < len(pids) else None
        snap = {
            "worker": wi,
            "pid": pid,
            "status": wd.get("status", "unknown"),
            "total_nodes": wd.get("total_nodes", wd.get("nodes", 0)),
            "tt_size": wd.get("tt_size", wd.get("tt_flushed", 0)),
            "elapsed_ms": wd.get("elapsed_ms", 0),
            "current_move": wd.get("current_move"),
        }
        # 检查进程是否还活着
        if pid is not None and wd.get("status") != "done":
            try:
                os.kill(pid, 0)  # 不发信号，仅检测存活
            except ProcessLookupError:
                snap["status"] = "异常退出"
            except PermissionError:
                pass  # 进程存在但无权限，视为活着
        if os.path.exists(wbin):
            whdr = _read_header(wbin)
            if whdr:
                snap["bin_count"] = whdr["count"]
        live_workers.append(snap)
    if live_workers:
        _print_worker_table(live_workers)


def _print_worker_table(workers: list):
    """打印 worker 明细表。"""
    _STATUS_LABEL = {
        "done": "完成", "running": "运行中", "merging": "合并中",
        "crashed": "异常退出", "unknown": "未知",
    }
    print()
    print("Worker 明细:")
    fmt = "  {:<4s}  {:<8s}  {:>7s}  {:>5s}  {:>12s}  {:>10s}  {:>8s}  {}"
    print(fmt.format("#", "状态", "PID", "Exit", "节点", "TT", "用时", "备注"))
    print("  " + "-" * 76)
    for w in sorted(workers, key=lambda x: x.get("worker", 0)):
        wi = w.get("worker", "?")
        st = _STATUS_LABEL.get(w.get("status", ""), w.get("status", "?"))
        nodes = w.get("total_nodes", 0)
        tt = w.get("tt_size", 0) or w.get("bin_count", 0)
        elapsed = w.get("elapsed_ms", 0)
        pid = w.get("pid")
        pid_str = str(pid) if pid is not None else "-"
        ec = w.get("exitcode")
        ec_str = str(ec) if ec is not None else "-"
        # 备注
        notes = []
        cm = w.get("current_move")
        if cm and w.get("status") == "running":
            notes.append(f"当前={cm}")
        moves = w.get("moves")
        if moves:
            notes.append(f"{len(moves)}着")
        note_str = "  ".join(notes)
        print(fmt.format(
            str(wi), st, pid_str, ec_str,
            f"{nodes:,}", f"{tt:,}",
            _fmt_duration(int(elapsed)) if elapsed else "-",
            note_str,
        ))


def _fmt_duration(ms: int) -> str:
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _progress_printer(progress_path: str, stop_event):
    """后台线程：轮询 progress.json 并打印实时进度到终端。"""
    import threading
    last_nodes = -1
    while not stop_event.is_set():
        stop_event.wait(2)
        try:
            with open(progress_path) as f:
                prog = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        nodes = prog.get("total_nodes", 0)
        if nodes == last_nodes:
            continue
        last_nodes = nodes
        tt = prog.get("total_tt", 0)
        nps = prog.get("nodes_per_sec", 0)
        elapsed = prog.get("elapsed_ms", 0)
        wa = prog.get("workers_active", "?")
        wt = prog.get("workers_total", "?")
        st = prog.get("status", "")
        label = "合并中" if st == "merging" else "计算中"
        line = (f"\r  {label} {_fmt_duration(elapsed)}  "
                f"节点={nodes:>12,}  TT={tt:>10,}  "
                f"{nps:>8,} n/s  进程 {wa}/{wt}  ")
        sys.stdout.write(line)
        sys.stdout.flush()


def _cli_run(problem_id: str, num_workers: Optional[int] = None):
    """对指定题目运行预处理。"""
    import threading
    import uuid as _uuid
    from problems import get_problem, init_db, update_problem
    init_db(_DB_PATH)
    p = get_problem(_DB_PATH, problem_id)
    if not p:
        print(f"错误：找不到题目 {problem_id}", file=sys.stderr)
        sys.exit(1)
    if not p["kill_targets"] and not p["defend_targets"]:
        print(f"错误：题目 {p['name']} 未设定目标，请先在浏览器中设定", file=sys.stderr)
        sys.exit(1)

    job_id = _uuid.uuid4().hex[:12]
    os.makedirs(_CACHE_DIR, exist_ok=True)
    bin_path = os.path.join(_CACHE_DIR, f"{job_id}.bin")
    progress_path = os.path.join(_CACHE_DIR, f"{job_id}_progress.json")

    print(f"题目: {p['name']} ({problem_id})")
    print(f"杀目标: {p['kill_targets']}  守目标: {p['defend_targets']}")
    print(f"job_id: {job_id}")
    print(f"缓存: {bin_path}")
    print()

    update_problem(_DB_PATH, problem_id,
                   precompute_status="running", precompute_job_id=job_id)

    # 启动进度打印线程
    stop_event = threading.Event()
    printer = threading.Thread(target=_progress_printer,
                               args=(progress_path, stop_event), daemon=True)
    printer.start()

    try:
        run_precompute_parallel(
            p["board_grid"], p.get("last_capture", -1),
            p["region_mask"],
            p["kill_targets"], p["defend_targets"],
            p.get("attacker_color", BLACK), BLACK,
            bin_path, progress_path,
            num_workers,
        )
        stop_event.set()
        printer.join(timeout=1)
        sys.stdout.write("\r" + " " * 80 + "\r")  # 清行
        # 读取最终进度
        elapsed_str = ""
        crashed_info = ""
        try:
            with open(progress_path) as f:
                final_prog = json.load(f)
            elapsed_str = f"  用时 {_fmt_duration(final_prog.get('elapsed_ms', 0))}"
            n_crashed = final_prog.get("crashed_workers", 0)
            n_total = final_prog.get("total_workers", 0)
            if n_crashed > 0:
                crashed_info = f"\n警告: {n_crashed}/{n_total} 个 worker 异常退出，结果可能不完整"
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        # 读取结果
        hdr = _read_header(bin_path)
        if hdr and hdr["status"] == 1:
            result = hdr["result"]
            count = hdr["count"]
            print(f"完成！结果: {result}  TT 条目: {count:,}{elapsed_str}")
        elif hdr and hdr["status"] == 2:
            print(f"失败：所有 worker 均异常退出{elapsed_str}")
            print(crashed_info)
            update_problem(_DB_PATH, problem_id, precompute_status="none")
            sys.exit(1)
        else:
            print(f"完成（结果未知）{elapsed_str}")
        if crashed_info:
            print(crashed_info)
        update_problem(_DB_PATH, problem_id,
                       precompute_status="done", precompute_job_id=job_id)
    except KeyboardInterrupt:
        stop_event.set()
        printer.join(timeout=1)
        print("\n中断")
        update_problem(_DB_PATH, problem_id, precompute_status="none")
        sys.exit(1)
    except Exception as e:
        stop_event.set()
        printer.join(timeout=1)
        print(f"\n错误: {e}", file=sys.stderr)
        update_problem(_DB_PATH, problem_id, precompute_status="none")
        sys.exit(1)


def _ensure_pypy3():
    """如果当前不是 pypy3，用 pypy3 重新执行自身（同参数）。"""
    import platform
    import shutil
    if platform.python_implementation() == "PyPy":
        return  # 已经是 pypy3
    pypy3 = shutil.which("pypy3")
    if not pypy3:
        print("错误：找不到 pypy3，预处理必须使用 pypy3 运行", file=sys.stderr)
        print("请安装 pypy3 后重试", file=sys.stderr)
        sys.exit(1)
    # 用 pypy3 替换当前进程
    os.execv(pypy3, [pypy3] + sys.argv)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="围棋习题预处理工具",
        usage="python3 precompute.py {list,run,status} ..."
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有题目")

    p_status = sub.add_parser("status", help="查看题目预处理状态")
    p_status.add_argument("problem_id", help="题目 ID")

    p_run = sub.add_parser("run", help="对指定题目运行预处理")
    p_run.add_argument("problem_id", help="题目 ID")
    p_run.add_argument("-w", "--workers", type=int, default=None,
                       help="worker 进程数（默认 CPU-1）")

    # 兼容旧模式: precompute.py <config.json>
    args, unknown = parser.parse_known_args()

    if args.cmd == "list":
        _cli_list()
    elif args.cmd == "status":
        _cli_status(args.problem_id)
    elif args.cmd == "run":
        _ensure_pypy3()
        _cli_run(args.problem_id, args.workers)
    elif len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # 旧模式兼容（也强制 pypy3）
        _ensure_pypy3()
        with open(sys.argv[1]) as f:
            cfg = json.load(f)
        run_precompute_parallel(
            cfg["board"], cfg["last_capture"], cfg["region"],
            cfg["kill_targets"], cfg["defend_targets"],
            cfg["attacker_color"], cfg["turn"],
            cfg["db_path"], cfg["progress_path"],
            cfg.get("num_workers"),
        )
    else:
        parser.print_help()
        sys.exit(1)
