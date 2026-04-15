"""
预处理 Coordinator — 任务队列调度 + 事件循环

职责：生成根候选 → 放入任务队列 → 启动 Worker → 监控/崩溃恢复 → 合并 .bin
"""

import heapq
import json
import multiprocessing as mp
import os
import time
from typing import Dict, List, Optional, Tuple

from bincache import (DFPN_INF, _HEADER_SIZE, _RESULT_MAP,
                      _calc_max_tt_entries, _calc_num_workers,
                      _iter_records, _read_header, _write_header)
from board import BOARD_SIZE, Board
from solver import DfpnSolver
from worker import DEFAULT_BUDGET, Worker


class Coordinator:
    """预处理调度器。管理任务队列、Worker 进程、崩溃恢复、结果合并。"""

    def __init__(self, board_grid: List[int], last_capture: int,
                 region: List[int],
                 kill_targets: List[List[int]], defend_targets: List[List[int]],
                 attacker_color: int, first_turn: int,
                 bin_path: str, progress_path: str,
                 num_workers: Optional[int] = None):
        self.board_grid = board_grid
        self.last_capture = last_capture
        self.region = region
        self.kill_targets = kill_targets
        self.defend_targets = defend_targets
        self.attacker_color = attacker_color
        self.first_turn = first_turn
        self.bin_path = bin_path
        self.progress_path = progress_path

        self.num_workers = num_workers or _calc_num_workers()
        self.max_entries = _calc_max_tt_entries(self.num_workers)
        self.cache_dir = os.path.dirname(bin_path) or "."
        self.job_id = os.path.splitext(os.path.basename(bin_path))[0]

        self.task_queue: Optional[mp.Queue] = None
        self.result_queue: Optional[mp.Queue] = None
        self.heartbeat_queue: Optional[mp.Queue] = None
        self.workers: List[mp.Process] = []
        self.worker_progress: List[str] = []
        self.all_results: Dict[Tuple[int, int], Tuple[str, int, int, int]] = {}
        self.in_flight: Dict[Tuple[int, int], int] = {}
        self.root_moves: List[Tuple[int, int]] = []
        self.start_time = 0.0

    # ── 生成根候选 ──

    def _gen_root_moves(self) -> List[Tuple[int, int]]:
        board = Board(BOARD_SIZE)
        board.grid = list(self.board_grid)
        board.last_capture = self.last_capture
        board.rebuild_zh()
        tmp = DfpnSolver(
            board, self.region, attacker_color=self.attacker_color,
            kill_targets=[tuple(c) for c in self.kill_targets],
            defend_targets=[tuple(c) for c in self.defend_targets],
            max_nodes=1, max_time_ms=1,
        )
        kids = tmp._gen_children(self.first_turn,
                                 allow_pass=(self.first_turn != self.attacker_color))
        return [m for m, _ in kids if m is not None]

    # ── 队列 + Worker 启动 ──

    def _init_queues(self) -> None:
        self.task_queue = mp.Queue()
        self.result_queue = mp.Queue()
        self.heartbeat_queue = mp.Queue()
        for move in self.root_moves:
            move_bin = os.path.join(self.cache_dir,
                                    f"{self.job_id}_{move[0]}_{move[1]}.bin")
            self.task_queue.put((move, move_bin))

    def _start_workers(self) -> None:
        self.workers = []
        self.worker_progress = []
        for wi in range(self.num_workers):
            wp = os.path.join(self.cache_dir,
                              f"{self.job_id}_worker{wi}_progress.json")
            self.worker_progress.append(wp)
            p = mp.Process(target=Worker.run, args=(
                self.task_queue, self.result_queue, self.heartbeat_queue,
                self.board_grid, self.last_capture, self.region,
                self.kill_targets, self.defend_targets,
                self.attacker_color, self.first_turn,
                self.max_entries, DEFAULT_BUDGET, wp,
            ))
            p.start()
            self.workers.append(p)

    def _write_pids(self) -> str:
        pids_path = os.path.join(self.cache_dir, f"{self.job_id}_pids.json")
        try:
            with open(pids_path, "w") as f:
                json.dump([p.pid for p in self.workers], f)
        except OSError:
            pass
        return pids_path

    # ── 事件循环 ──

    def _drain_queues(self) -> None:
        """非阻塞收集心跳和结果。"""
        while not self.heartbeat_queue.empty():
            try:
                action, move, pid = self.heartbeat_queue.get_nowait()
                if action == "start":
                    self.in_flight[move] = pid
                elif action in ("done", "requeue"):
                    self.in_flight.pop(move, None)
            except Exception:
                break
        while not self.result_queue.empty():
            try:
                move, result, pn, dn, nodes = self.result_queue.get_nowait()
                self.all_results[move] = (result, pn, dn, nodes)
            except Exception:
                break

    def _recover_crashed(self) -> None:
        """检测崩溃的 worker，回收任务，启动替补。"""
        for i, p in enumerate(self.workers):
            if p.is_alive() or p.exitcode == 0 or p.exitcode is None:
                continue
            dead_pid = p.pid
            for m in [m for m, pid in self.in_flight.items() if pid == dead_pid]:
                if m not in self.all_results:
                    move_bin = os.path.join(self.cache_dir,
                                            f"{self.job_id}_{m[0]}_{m[1]}.bin")
                    self.task_queue.put((m, move_bin))
                self.in_flight.pop(m, None)
            wp = self.worker_progress[i]
            new_p = mp.Process(target=Worker.run, args=(
                self.task_queue, self.result_queue, self.heartbeat_queue,
                self.board_grid, self.last_capture, self.region,
                self.kill_targets, self.defend_targets,
                self.attacker_color, self.first_turn,
                self.max_entries, DEFAULT_BUDGET, wp,
            ))
            new_p.start()
            self.workers[i] = new_p

    def _update_progress(self) -> None:
        total_nodes = 0
        active = 0
        for wp_path in self.worker_progress:
            try:
                with open(wp_path) as f:
                    wp = json.load(f)
                total_nodes += wp.get("total_nodes", 0)
                if wp.get("status") == "running":
                    active += 1
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
        nps = int(total_nodes / max(elapsed_ms, 1) * 1000)
        try:
            with open(self.progress_path, "w") as f:
                json.dump({
                    "status": "running",
                    "total_nodes": total_nodes,
                    "elapsed_ms": elapsed_ms,
                    "nodes_per_sec": nps,
                    "workers_active": active,
                    "done_moves": len(self.all_results),
                    "total_moves": len(self.root_moves),
                }, f)
        except OSError:
            pass

    def _event_loop(self) -> None:
        while len(self.all_results) < len(self.root_moves):
            self._drain_queues()
            self._recover_crashed()
            self._update_progress()
            time.sleep(2)

    # ── 关闭 + 合并 ──

    def _shutdown_workers(self) -> None:
        for _ in range(self.num_workers):
            self.task_queue.put(None)
        for p in self.workers:
            if p.is_alive():
                p.join(timeout=10)

    def _merge_bins(self) -> None:
        """k-way 归并所有根着的排序 .bin → 主 .bin。"""
        is_or = (self.first_turn == self.attacker_color)
        child_pns = [pn for _, pn, _, _ in self.all_results.values()]
        child_dns = [dn for _, _, dn, _ in self.all_results.values()]

        if child_pns:
            root_pn = min(child_pns) if is_or else min(sum(child_pns), DFPN_INF)
            root_dn = min(sum(child_dns), DFPN_INF) if is_or else min(child_dns)
        else:
            root_pn, root_dn = DFPN_INF, DFPN_INF

        result_str = ("ATTACKER_WINS" if root_pn == 0 else
                      "DEFENDER_WINS" if root_dn == 0 else "UNPROVEN")

        bin_paths = []
        for move in self.all_results:
            bp = os.path.join(self.cache_dir,
                              f"{self.job_id}_{move[0]}_{move[1]}.bin")
            if os.path.exists(bp) and os.path.getsize(bp) >= _HEADER_SIZE:
                bin_paths.append(bp)

        os.makedirs(os.path.dirname(self.bin_path) or ".", exist_ok=True)
        iters = [_iter_records(p) for p in bin_paths]
        with open(self.bin_path, "wb") as f:
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

        for bp in bin_paths:
            for suffix in ("", ".tmp", ".flush0.tmp", ".flush1.tmp"):
                try:
                    os.remove(bp + suffix)
                except OSError:
                    pass

    def _cleanup(self, pids_path: str) -> None:
        for wp in self.worker_progress:
            try:
                os.remove(wp)
            except OSError:
                pass
        try:
            os.remove(pids_path)
        except OSError:
            pass

    def _write_final_progress(self) -> None:
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
        total_nodes = sum(v[3] for v in self.all_results.values())
        with open(self.progress_path, "w") as f:
            json.dump({
                "status": "done",
                "total_nodes": total_nodes,
                "elapsed_ms": elapsed_ms,
                "message": "merged",
                "done_moves": len(self.all_results),
                "total_moves": len(self.root_moves),
            }, f)

    # ── 主入口 ──

    def run(self) -> None:
        """执行完整的预处理流程。"""
        os.makedirs(self.cache_dir, exist_ok=True)

        self.root_moves = self._gen_root_moves()
        if not self.root_moves:
            with open(self.bin_path, "wb") as f:
                _write_header(f, status=1, result=_RESULT_MAP["DEFENDER_WINS"],
                              count=0, root_pn=0xFFFFFFFF, root_dn=0)
            with open(self.progress_path, "w") as f:
                json.dump({"status": "done", "total_nodes": 0}, f)
            return

        self._init_queues()
        self._start_workers()
        pids_path = self._write_pids()

        self.start_time = time.monotonic()
        self._event_loop()
        self._shutdown_workers()

        self._merge_bins()
        self._cleanup(pids_path)
        self._write_final_progress()


# ── CLI 入口 ──

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: pypy3 precompute.py <config.json>", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
    Coordinator(
        cfg["board"], cfg["last_capture"], cfg["region"],
        cfg["kill_targets"], cfg["defend_targets"],
        cfg["attacker_color"], cfg["turn"],
        cfg["db_path"], cfg["progress_path"],
        cfg.get("num_workers"),
    ).run()
