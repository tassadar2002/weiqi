"""
预处理 Worker — 无状态任务执行器

从任务队列取任务（根着点 + .bin 路径），用 DiskTT + DfpnSolver 执行 df-pn，
未完成放回队列，已完成汇报结果。任何 worker 可续传任何根着。
"""

import json
import multiprocessing as mp
import os
import time
from typing import List, Optional, Tuple

from binstore import (DFPN_INF, DiskTT, _HEADER_FMT, _MAGIC, _RESULT_MAP,
                      _read_header)
from board import BOARD_SIZE, Board
from solver import DfpnSolver
import struct

# 默认每段节点预算
DEFAULT_BUDGET = 5_000_000


def _mark_bin_done(bin_path: str, result: str, root_pn: int, root_dn: int) -> None:
    """根着证明完成后，将 .bin header 的 status 标记为 done(1) 并保存根 pn/dn。
    用于断点续传时识别"已完成"的根着。"""
    if not os.path.exists(bin_path):
        return
    hdr = _read_header(bin_path)
    if not hdr:
        return
    count = hdr["count"]
    try:
        with open(bin_path, "r+b") as f:
            f.seek(0)
            f.write(struct.pack(_HEADER_FMT, _MAGIC, 1, 1,  # status=1 done
                                _RESULT_MAP.get(result, 0),
                                count,
                                min(root_pn, 0xFFFFFFFF),
                                min(root_dn, 0xFFFFFFFF)))
    except OSError:
        pass


class Worker:
    """无状态预处理 worker。

    使用方式：
        作为 mp.Process target 调用 Worker.run（类方法），
        或直接实例化用于测试。
    """

    def __init__(self, board_grid: List[int], last_capture: int,
                 region: List[int],
                 kill_targets: List[List[int]], defend_targets: List[List[int]],
                 attacker_color: int, first_turn: int,
                 max_entries: int, budget: int = DEFAULT_BUDGET):
        self.board_grid = board_grid
        self.last_capture = last_capture
        self.region = region
        self.kill_targets = kill_targets
        self.defend_targets = defend_targets
        self.attacker_color = attacker_color
        self.first_turn = first_turn
        self.max_entries = max_entries
        self.budget = budget
        self.total_nodes = 0
        self.tasks_done = 0

    def solve_task(self, task: tuple, progress_path: str = "") -> Optional[dict]:
        """执行单个任务。返回 solver 结果 dict 或 None（非法着）。"""
        root_move, bin_path = task
        mx, my = root_move

        board = self._make_board()
        u = board.play_undoable(mx, my, self.first_turn)
        if u is None:
            return None

        disk_tt = DiskTT(bin_path, self.max_entries)
        pid = os.getpid()

        def on_progress(info):
            info["status"] = "running"
            info["total_nodes"] = self.total_nodes + info["nodes"]
            info["current_move"] = [mx, my]
            info["pid"] = pid
            if progress_path:
                try:
                    with open(progress_path, "w") as pf:
                        json.dump(info, pf)
                except OSError:
                    pass

        solver = DfpnSolver(
            board, self.region,
            attacker_color=self.attacker_color,
            kill_targets=[tuple(c) for c in self.kill_targets],
            defend_targets=[tuple(c) for c in self.defend_targets],
            max_nodes=self.budget,
            progress_callback=on_progress,
            tt=disk_tt,
        )
        r = solver.solve(-self.first_turn)
        disk_tt.close()
        board.undo(u)
        return r

    def _make_board(self) -> Board:
        board = Board(BOARD_SIZE)
        board.grid = list(self.board_grid)
        board.last_capture = self.last_capture
        board.rebuild_zh()
        return board

    def _write_progress(self, progress_path: str, status: str, t0: float) -> None:
        try:
            with open(progress_path, "w") as pf:
                json.dump({
                    "status": status,
                    "total_nodes": self.total_nodes,
                    "tasks_done": self.tasks_done,
                    "pid": os.getpid(),
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }, pf)
        except OSError:
            pass

    # ── 主循环（作为子进程运行）──

    @staticmethod
    def run(task_queue: mp.Queue, result_queue: mp.Queue,
            heartbeat_queue: mp.Queue,
            board_grid: List[int], last_capture: int,
            region: List[int],
            kill_targets: List[List[int]], defend_targets: List[List[int]],
            attacker_color: int, first_turn: int,
            max_entries: int, budget: int,
            progress_path: str) -> None:
        """子进程入口：循环取任务、执行、放回或汇报。"""
        import sys as _sys
        needed = 80 * 3 + 200
        if _sys.getrecursionlimit() < needed:
            _sys.setrecursionlimit(needed)

        w = Worker(board_grid, last_capture, region,
                   kill_targets, defend_targets, attacker_color, first_turn,
                   max_entries, budget)
        pid = os.getpid()
        t0 = time.monotonic()

        while True:
            task = task_queue.get()
            if task is None:
                break

            root_move = task[0]
            heartbeat_queue.put(("start", root_move, pid))

            r = w.solve_task(task, progress_path)

            if r is None:
                result_queue.put((root_move, "ILLEGAL", 0, 0, 0))
                heartbeat_queue.put(("done", root_move, pid))
                continue

            w.total_nodes += r["nodes"]
            w.tasks_done += 1

            if r["result"] == "UNPROVEN":
                task_queue.put(task)
                heartbeat_queue.put(("requeue", root_move, pid))
            else:
                # 标记 .bin 为 done，保存根 pn/dn（用于断点续传时识别）
                bin_path = task[1]
                _mark_bin_done(bin_path, r["result"], r["pn"], r["dn"])
                result_queue.put((root_move, r["result"], r["pn"], r["dn"], r["nodes"]))
                heartbeat_queue.put(("done", root_move, pid))

            w._write_progress(progress_path, "running", t0)

        w._write_progress(progress_path, "done", t0)
