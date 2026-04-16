"""查看指定题目的预处理状态。"""

import json
import os
import sys
from typing import Optional

from action.base import DB_PATH, PRECOMPUTE_DIR, Action, fmt_duration, fmt_size
from binstore import _read_header


class StatusAction(Action):
    def run(self, args) -> None:
        import glob as _glob
        from problems import get_problem, init_db
        init_db(DB_PATH)
        p = get_problem(DB_PATH, args.problem_id)
        if not p:
            print(f"错误：找不到题目 {args.problem_id}", file=sys.stderr)
            sys.exit(1)

        print(f"题目: {p['name']} ({args.problem_id})")
        print(f"杀目标: {p['kill_targets']}  守目标: {p['defend_targets']}")

        status = p["precompute_status"]
        job_id = p.get("precompute_job_id")
        status_label = {"done": "已完成", "running": "进行中",
                        "none": "未开始"}.get(status, status)
        print(f"预处理状态: {status_label}")

        if not job_id:
            if status == "none":
                print("尚未运行过预处理。使用 run 子命令启动。")
            return

        print(f"job_id: {job_id}")
        bin_path = os.path.join(PRECOMPUTE_DIR, f"{job_id}.bin")
        progress_path = os.path.join(PRECOMPUTE_DIR, f"{job_id}_progress.json")

        _print_bin_info(bin_path)

        prog = _read_progress(progress_path)
        if prog:
            _print_overall_progress(prog)

        _print_workers(job_id, prog)
        _print_kids(job_id, prog)


# ── 辅助函数 ──

def _read_progress(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _print_bin_info(bin_path: str) -> None:
    if not os.path.exists(bin_path):
        print(f"缓存文件: 不存在")
        return
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


def _print_overall_progress(prog: dict) -> None:
    elapsed = prog.get("elapsed_ms", 0)
    done = prog.get("done_moves", 0)
    total = prog.get("total_moves", 0)
    if total:
        print(f"根着进度: {done}/{total} 已证明")
    print(f"搜索节点: {prog.get('total_nodes', 0):,}")
    nps = prog.get("nodes_per_sec", 0)
    if nps:
        print(f"速度: {nps:,} nodes/s")
    if elapsed > 0:
        print(f"用时: {fmt_duration(int(elapsed))}")
    wa = prog.get("workers_active")
    if wa is not None:
        print(f"活跃进程: {wa}")


def _print_workers(job_id: str, prog: Optional[dict]) -> None:
    """打印 worker 明细表（完成后从 progress.json 快照读，运行中扫描文件）。"""
    import glob as _glob
    worker_data = (prog or {}).get("workers")
    if worker_data:
        _print_worker_table(worker_data)
        return

    pattern = os.path.join(PRECOMPUTE_DIR, f"{job_id}_worker*_progress.json")
    wpaths = sorted(_glob.glob(pattern))
    live_workers = []
    for wp in wpaths:
        base = os.path.basename(wp)
        try:
            wi = int(base.replace(f"{job_id}_worker", "")
                         .replace("_progress.json", ""))
        except (IndexError, ValueError):
            continue
        try:
            with open(wp) as f:
                wd = json.load(f)
        except (json.JSONDecodeError, OSError):
            wd = {}
        pid = wd.get("pid")
        snap = {
            "worker": wi,
            "pid": pid,
            "status": wd.get("status", "unknown"),
            "total_nodes": wd.get("total_nodes", 0),
            "tasks_done": wd.get("tasks_done", 0),
            "elapsed_ms": wd.get("elapsed_ms", 0),
            "current_move": wd.get("current_move"),
        }
        if pid is not None and wd.get("status") not in ("done", "crashed"):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                snap["status"] = "异常退出"
            except PermissionError:
                pass
        live_workers.append(snap)
    if live_workers:
        _print_worker_table(live_workers)


def _print_worker_table(workers: list) -> None:
    _STATUS_LABEL = {
        "done": "完成", "running": "运行中",
        "crashed": "异常退出", "unknown": "未知",
    }
    print()
    print("Worker 明细:")
    fmt = "  {:<4s}  {:<8s}  {:>7s}  {:>12s}  {:>6s}  {:>8s}  {}"
    print(fmt.format("#", "状态", "PID", "节点", "任务数", "用时", "备注"))
    print("  " + "-" * 64)
    for w in sorted(workers, key=lambda x: x.get("worker", 0)):
        wi = w.get("worker", "?")
        st = _STATUS_LABEL.get(w.get("status", ""), w.get("status", "?"))
        nodes = w.get("total_nodes", 0)
        tasks = w.get("tasks_done", 0)
        elapsed = w.get("elapsed_ms", 0)
        pid = w.get("pid")
        pid_str = str(pid) if pid is not None else "-"
        notes = []
        cm = w.get("current_move")
        if cm and w.get("status") == "running":
            notes.append(f"当前={cm}")
        note_str = "  ".join(notes)
        print(fmt.format(
            str(wi), st, pid_str,
            f"{nodes:,}", str(tasks),
            fmt_duration(int(elapsed)) if elapsed else "-",
            note_str,
        ))


def _print_kids(job_id: str, prog: Optional[dict]) -> None:
    kids = _collect_root_kids_status(job_id, prog)
    if kids:
        _print_root_kids_table(kids)


def _collect_root_kids_status(job_id: str,
                              prog: Optional[dict] = None) -> list:
    """收集所有根着的状态。

    根着完整列表优先来自：
      - 运行中：{job_id}_root_moves.json
      - 已完成：progress.json 的 root_kids 字段
    若两者都没有，则回退为只列出有 .bin 或正在 run 的根着。
    """
    import glob as _glob

    if prog and prog.get("root_kids"):
        return [{
            "move": tuple(k["move"]),
            "status": "done",
            "result": k.get("result"),
            "tt_count": 0,
            "file_size": 0,
            "pid": None,
        } for k in prog["root_kids"]]

    all_moves = []
    rm_path = os.path.join(PRECOMPUTE_DIR, f"{job_id}_root_moves.json")
    try:
        with open(rm_path) as f:
            all_moves = [tuple(m) for m in json.load(f)]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    worker_pattern = os.path.join(PRECOMPUTE_DIR,
                                  f"{job_id}_worker*_progress.json")
    in_flight = {}  # (x,y) → (pid, tt_size)
    for wp in _glob.glob(worker_pattern):
        try:
            with open(wp) as f:
                wd = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        cm = wd.get("current_move")
        if cm and wd.get("status") == "running":
            in_flight[tuple(cm)] = (wd.get("pid"), wd.get("tt_size", 0))

    bin_info = {}  # (x,y) → (status, result, count, size)
    pattern = os.path.join(PRECOMPUTE_DIR, f"{job_id}_*_*.bin")
    for bp in _glob.glob(pattern):
        base = os.path.basename(bp).replace(f"{job_id}_", "").replace(".bin", "")
        parts = base.split("_")
        if len(parts) != 2:
            continue
        try:
            x, y = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        hdr = _read_header(bp)
        if not hdr:
            continue
        bin_info[(x, y)] = (hdr["status"], hdr["result"], hdr["count"],
                            os.path.getsize(bp))

    if not all_moves:
        all_moves = sorted(set(bin_info) | set(in_flight))

    kids = []
    for move in all_moves:
        bi = bin_info.get(move)
        ifl = in_flight.get(move)
        if bi:
            bin_status, result, count, size = bi
            if bin_status == 1:
                status = "done"
            elif move in in_flight:
                status = "running"
            else:
                status = "partial"
            kids.append({
                "move": move, "status": status,
                "result": result if bin_status == 1 else None,
                "tt_count": count, "file_size": size,
                "pid": ifl[0] if ifl else None,
            })
        elif ifl:
            kids.append({
                "move": move, "status": "running", "result": None,
                "tt_count": ifl[1], "file_size": 0, "pid": ifl[0],
            })
        else:
            kids.append({
                "move": move, "status": "queued", "result": None,
                "tt_count": 0, "file_size": 0, "pid": None,
            })
    return kids


def _print_root_kids_table(kids: list) -> None:
    print()
    print("根着明细:")
    fmt = "  {:<10s}  {:<10s}  {:>12s}  {:<18s}  {}"
    print(fmt.format("坐标", "状态", "TT条目", "结果", "备注"))
    print("  " + "-" * 70)
    counts = {"done": 0, "running": 0, "partial": 0, "queued": 0}
    for k in sorted(kids, key=lambda x: x["move"]):
        coord = f"({k['move'][0]}, {k['move'][1]})"
        if k["status"] == "done":
            st_label = "✓ 已完成"
            note = fmt_size(k["file_size"]) if k["file_size"] else ""
        elif k["status"] == "running":
            st_label = "⋯ 运行中"
            size_str = fmt_size(k["file_size"]) if k["file_size"] else "内存中"
            note = f"pid={k['pid']}  ({size_str})"
        elif k["status"] == "partial":
            st_label = "· 进行中"
            note = f"{fmt_size(k['file_size'])}  (无 worker)"
        else:  # queued
            st_label = "○ 排队中"
            note = ""
        counts[k["status"]] = counts.get(k["status"], 0) + 1
        result = k.get("result") or "—"
        tt = f"{k['tt_count']:,}" if k["tt_count"] else "—"
        print(fmt.format(coord, st_label, tt, result, note))
    total = len(kids)
    if total:
        print(f"\n  共 {total} 个根着: 已完成 {counts['done']}, "
              f"运行中 {counts['running']}, 进行中 {counts['partial']}, "
              f"排队中 {counts['queued']}")
