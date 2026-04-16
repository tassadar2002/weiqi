"""运行预处理（支持断点续传）。"""

import glob
import json
import os
import sys
import threading
import uuid as _uuid

from action.base import DB_PATH, STORE_DIR, Action, fmt_duration
from board import BLACK
from precompute.binstore import _read_header


class RunAction(Action):
    def run(self, args) -> None:
        from precompute.coordinator import Coordinator
        from problems import get_problem, init_db, update_problem

        init_db(DB_PATH)
        p = get_problem(DB_PATH, args.problem_id)
        if not p:
            print(f"错误：找不到题目 {args.problem_id}", file=sys.stderr)
            sys.exit(1)
        if not p["kill_targets"] and not p["defend_targets"]:
            print(f"错误：题目 {p['name']} 未设定目标，请先在浏览器中设定",
                  file=sys.stderr)
            sys.exit(1)

        # 复用已有 job_id（断点续传）或创建新的
        job_id = p.get("precompute_job_id") or _uuid.uuid4().hex[:12]
        os.makedirs(STORE_DIR, exist_ok=True)
        bin_path = os.path.join(STORE_DIR, f"{job_id}.bin")
        progress_path = os.path.join(STORE_DIR, f"{job_id}_progress.json")

        # 已完成检查
        if os.path.exists(bin_path):
            hdr = _read_header(bin_path)
            if hdr and hdr["status"] == 1:
                print(f"题目 {p['name']} 已完成预处理"
                      f"（{hdr['result']}, TT={hdr['count']:,}）")
                print(f"如需重新处理，请先删除：rm backend/store/{job_id}.bin")
                update_problem(DB_PATH, args.problem_id,
                               precompute_status="done",
                               precompute_job_id=job_id)
                return

        done_bins = glob.glob(os.path.join(STORE_DIR,
                                          f"{job_id}_*_*.bin"))
        resume = len(done_bins) > 0

        print(f"题目: {p['name']} ({args.problem_id})")
        print(f"杀目标: {p['kill_targets']}  守目标: {p['defend_targets']}")
        print(f"job_id: {job_id}{'  (断点续传)' if resume else ''}")
        print(f"预处理结果: {bin_path}")
        if resume:
            print(f"已有 {len(done_bins)} 个根着 .bin 文件")
        print()

        update_problem(DB_PATH, args.problem_id,
                       precompute_status="running",
                       precompute_job_id=job_id)

        stop_event = threading.Event()
        printer = threading.Thread(
            target=_progress_printer,
            args=(progress_path, stop_event), daemon=True)
        printer.start()

        try:
            Coordinator(
                p["board_grid"], p.get("last_capture", -1),
                p["region_mask"],
                p["kill_targets"], p["defend_targets"],
                p.get("attacker_color", BLACK), BLACK,
                bin_path, progress_path,
                args.workers,
            ).run()
            stop_event.set()
            printer.join(timeout=1)
            sys.stdout.write("\r" + " " * 80 + "\r")
            _print_final(bin_path, progress_path)
            update_problem(DB_PATH, args.problem_id,
                           precompute_status="done",
                           precompute_job_id=job_id)
        except KeyboardInterrupt:
            stop_event.set()
            printer.join(timeout=1)
            print("\n中断")
            update_problem(DB_PATH, args.problem_id,
                           precompute_status="none")
            sys.exit(1)
        except Exception as e:
            stop_event.set()
            printer.join(timeout=1)
            print(f"\n错误: {e}", file=sys.stderr)
            update_problem(DB_PATH, args.problem_id,
                           precompute_status="none")
            sys.exit(1)


def _print_final(bin_path: str, progress_path: str) -> None:
    elapsed_str = ""
    retry_info = ""
    try:
        with open(progress_path) as f:
            final_prog = json.load(f)
        elapsed_str = f"  用时 {fmt_duration(final_prog.get('elapsed_ms', 0))}"
        total_retries = final_prog.get("total_retries", 0)
        if total_retries > 0:
            retry_info = f"\n提示: 过程中共重启 worker {total_retries} 次（断点续传）"
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    hdr = _read_header(bin_path)
    if hdr and hdr["status"] == 1:
        print(f"完成！结果: {hdr['result']}  TT 条目: {hdr['count']:,}{elapsed_str}")
    else:
        print(f"完成（结果未知）{elapsed_str}")
    if retry_info:
        print(retry_info)


def _progress_printer(progress_path: str, stop_event) -> None:
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
        line = (f"\r  计算中 {fmt_duration(elapsed)}  "
                f"根着 {done}/{total}  "
                f"节点={nodes:>12,}  "
                f"{nps:>8,} n/s  进程 {wa}  ")
        sys.stdout.write(line)
        sys.stdout.flush()
