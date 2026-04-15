#!/usr/bin/env python3
"""
围棋习题预处理 CLI

用法:
  python3 cli_precompute.py list                    # 列出所有题目
  python3 cli_precompute.py status <problem_id>     # 查看预处理状态
  python3 cli_precompute.py run <problem_id> [-w N] # 运行预处理
"""

import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bincache import _read_header
from board import BLACK

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_PROJECT_ROOT, "backend", "data", "problems.db")
_CACHE_DIR = os.path.join(_PROJECT_ROOT, "backend", "cache")


# ============================================================
# list
# ============================================================

def cli_list():
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


# ============================================================
# status
# ============================================================

def cli_status(problem_id: str):
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
        done = prog.get("done_moves", 0)
        total = prog.get("total_moves", 0)
        if total:
            print(f"根着进度: {done}/{total} 已证明")
        print(f"搜索节点: {prog.get('total_nodes', 0):,}")
        nps = prog.get("nodes_per_sec", 0)
        if nps:
            print(f"速度: {nps:,} nodes/s")
        if elapsed > 0:
            print(f"用时: {_fmt_duration(int(elapsed))}")
        wa = prog.get("workers_active")
        if wa is not None:
            print(f"活跃进程: {wa}")

    # ---- worker 明细 ----
    # 优先从主 progress.json 的 workers 快照读取（完成后 worker 文件已清理）
    worker_data = (prog or {}).get("workers")
    if worker_data:
        _print_worker_table(worker_data)
        return

    # 运行中：扫描 worker progress 文件
    pattern = os.path.join(_CACHE_DIR, f"{job_id}_worker*_progress.json")
    wpaths = sorted(_glob.glob(pattern))
    if not wpaths:
        return

    live_workers = []
    for wp in wpaths:
        base = os.path.basename(wp)
        try:
            wi = int(base.replace(f"{job_id}_worker", "").replace("_progress.json", ""))
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


# ============================================================
# run
# ============================================================

def cli_run(problem_id: str, num_workers: Optional[int] = None):
    """对指定题目运行预处理。"""
    import threading
    import uuid as _uuid
    from coordinator import Coordinator
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
        Coordinator(
            p["board_grid"], p.get("last_capture", -1),
            p["region_mask"],
            p["kill_targets"], p["defend_targets"],
            p.get("attacker_color", BLACK), BLACK,
            bin_path, progress_path,
            num_workers,
        ).run()
        stop_event.set()
        printer.join(timeout=1)
        sys.stdout.write("\r" + " " * 80 + "\r")  # 清行
        # 读取最终进度
        elapsed_str = ""
        retry_info = ""
        try:
            with open(progress_path) as f:
                final_prog = json.load(f)
            elapsed_str = f"  用时 {_fmt_duration(final_prog.get('elapsed_ms', 0))}"
            total_retries = final_prog.get("total_retries", 0)
            if total_retries > 0:
                retry_info = f"\n提示: 过程中共重启 worker {total_retries} 次（断点续传）"
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        # 读取结果
        hdr = _read_header(bin_path)
        if hdr and hdr["status"] == 1:
            result = hdr["result"]
            count = hdr["count"]
            print(f"完成！结果: {result}  TT 条目: {count:,}{elapsed_str}")
        else:
            print(f"完成（结果未知）{elapsed_str}")
        if retry_info:
            print(retry_info)
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


# ============================================================
# 工具函数
# ============================================================

def _print_worker_table(workers: list):
    """打印 worker 明细表。"""
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
        nps = prog.get("nodes_per_sec", 0)
        elapsed = prog.get("elapsed_ms", 0)
        done = prog.get("done_moves", 0)
        total = prog.get("total_moves", "?")
        wa = prog.get("workers_active", "?")
        line = (f"\r  计算中 {_fmt_duration(elapsed)}  "
                f"根着 {done}/{total}  "
                f"节点={nodes:>12,}  "
                f"{nps:>8,} n/s  进程 {wa}  ")
        sys.stdout.write(line)
        sys.stdout.flush()


def _ensure_pypy3():
    """如果当前不是 pypy3，用 pypy3 重新执行自身（同参数）。"""
    import platform
    import shutil
    if platform.python_implementation() == "PyPy":
        return
    pypy3 = shutil.which("pypy3")
    if not pypy3:
        print("错误：找不到 pypy3，预处理必须使用 pypy3 运行", file=sys.stderr)
        print("请安装 pypy3 后重试", file=sys.stderr)
        sys.exit(1)
    os.execv(pypy3, [pypy3] + sys.argv)


# ============================================================
# main
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="围棋习题预处理工具",
        usage="python3 cli_precompute.py {list,run,status} ..."
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有题目")

    p_status = sub.add_parser("status", help="查看题目预处理状态")
    p_status.add_argument("problem_id", help="题目 ID")

    p_run = sub.add_parser("run", help="对指定题目运行预处理")
    p_run.add_argument("problem_id", help="题目 ID")
    p_run.add_argument("-w", "--workers", type=int, default=None,
                       help="worker 进程数（默认 CPU×70%%）")

    args, unknown = parser.parse_known_args()

    if args.cmd == "list":
        cli_list()
    elif args.cmd == "status":
        cli_status(args.problem_id)
    elif args.cmd == "run":
        _ensure_pypy3()
        cli_run(args.problem_id, args.workers)
    else:
        parser.print_help()
        sys.exit(1)
