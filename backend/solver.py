"""
df-pn 求解器（简化版，面向预处理）

职责：穷举证明。不含跨请求缓存、vitalness、穷举根证明等在线优化。
预处理完成后查询走 solve_from_cache（在 precompute.py 中）。

保留：
  - TT 转置表（df-pn 核心）
  - 走法排序（提子优先，加快收敛）
  - progress_callback（报告进度）
  - try/finally 保护 play/undo
  - 多目标复合终止条件
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Set, Tuple

from board import Board, EMPTY, UndoInfo
from eyes import count_real_eyes, get_target_group

DFPN_INF = 10**9


class _Timeout(Exception):
    pass


class DfpnSolver:
    def __init__(self, board: Board, region_mask: List[int],
                 attacker_color: int = 1,
                 kill_targets: Optional[List[Tuple[int, int]]] = None,
                 defend_targets: Optional[List[Tuple[int, int]]] = None,
                 max_nodes: int = 10**18,
                 max_time_ms: int = 10**18,
                 max_depth: int = 80,
                 progress_callback: Optional[Callable] = None):
        self.board = board
        self.region_mask = region_mask
        self.attacker_color = attacker_color
        self.defender_color = -attacker_color
        self.kill_targets: List[Tuple[int, int]] = list(kill_targets or [])
        self.defend_targets: List[Tuple[int, int]] = list(defend_targets or [])
        self.max_nodes = max_nodes
        self.max_time_ms = max_time_ms
        self.max_depth = max_depth
        self.progress_callback = progress_callback

        self.tt: Dict[tuple, Tuple[int, int]] = {}
        self.tt_log: List[tuple] = []  # 新增 key 的有序列表（用于增量 flush）
        self.nodes = 0
        self.start_time = 0.0
        self._initial_turn = 0  # 记录 solve() 传入的 turn，供 progress 查根
        self._killers: List[List[Tuple[int, int]]] = [[] for _ in range(max_depth + 1)]

    # ---- TT ----

    def _tt_key(self, turn: int) -> tuple:
        return (self.board.zh, turn, self.board.last_capture)

    def _tt_get(self, turn: int) -> Tuple[int, int]:
        return self.tt.get(self._tt_key(turn), (1, 1))

    def _tt_set(self, turn: int, pn: int, dn: int) -> None:
        key = self._tt_key(turn)
        if key not in self.tt:
            self.tt_log.append(key)
        self.tt[key] = (pn, dn)

    # ---- 终止判定（多目标）----

    def _terminal(self) -> Optional[str]:
        board = self.board
        for dx, dy in self.defend_targets:
            if board.get(dx, dy) == EMPTY:
                return "DEF"
        all_killed = True
        for kx, ky in self.kill_targets:
            color = board.get(kx, ky)
            if color == EMPTY:
                continue
            tgt = get_target_group(board, (kx, ky))
            if tgt is None:
                continue
            eyes = count_real_eyes(board, tgt)
            if eyes >= 2:
                return "DEF"
            all_killed = False
        if self.kill_targets and all_killed:
            return "ATK"
        return None

    # ---- 走法生成（提子优先排序）----

    def _gen_children(self, turn: int, allow_pass: bool, depth: int = 0) -> List[Tuple[Optional[Tuple[int, int]], int]]:
        board = self.board
        size = board.size
        grid = board.grid
        region = self.region_mask
        killer_set = set(self._killers[depth]) if depth < len(self._killers) else set()
        kids: List[Tuple[Optional[Tuple[int, int]], int]] = []
        for y in range(size):
            for x in range(size):
                idx = y * size + x
                if not region[idx] or grid[idx] != EMPTY:
                    continue
                u = board.play_undoable(x, y, turn)
                if u is None:
                    continue
                score = len(u.captured) * 10000
                for nx, ny in board.neighbors(x, y):
                    if grid[ny * size + nx] != EMPTY:
                        score += 5
                if (x, y) in killer_set:
                    score += 50000
                board.undo(u)
                kids.append(((x, y), score))
        kids.sort(key=lambda k: -k[1])
        if allow_pass:
            kids.append((None, -1))
        return kids

    def _record_killer(self, depth: int, move: Tuple[int, int]) -> None:
        """记录 killer 着法，每深度保留最近 2 个。"""
        if depth >= len(self._killers):
            return
        killers = self._killers[depth]
        if move in killers:
            return
        killers.insert(0, move)
        if len(killers) > 2:
            killers.pop()

    # ---- play/undo helpers ----

    def _play_kid(self, move, turn):
        if move is None:
            prev = self.board.last_capture
            self.board.last_capture = -1
            return ("pass", prev)
        u = self.board.play_undoable(move[0], move[1], turn)
        if u is None: return None
        return ("move", u)

    def _undo_kid(self, handle):
        if handle is None: return
        kind, payload = handle
        if kind == "pass":
            self.board.last_capture = payload
        else:
            self.board.undo(payload)

    # ---- 预算检查 + 进度 ----

    def _check_limits(self):
        if self.nodes >= self.max_nodes:
            raise _Timeout()
        if self.nodes & 8191 == 0:
            elapsed = (time.monotonic() - self.start_time) * 1000
            if elapsed > self.max_time_ms:
                raise _Timeout()
            if self.progress_callback:
                root_pn, root_dn = self.tt.get(
                    self._tt_key(self._initial_turn), (1, 1))
                self.progress_callback({
                    "nodes": self.nodes,
                    "elapsed_ms": int(elapsed),
                    "tt_size": len(self.tt),
                    "root_pn": root_pn,
                    "root_dn": root_dn,
                })

    # ---- df-pn 主递归 ----

    def _mid(self, turn: int, depth: int, th_pn: int, th_dn: int) -> None:
        self.nodes += 1
        self._check_limits()
        if depth > self.max_depth:
            self._tt_set(turn, DFPN_INF, DFPN_INF)
            return
        term = self._terminal()
        if term == "ATK":
            self._tt_set(turn, 0, DFPN_INF)
            return
        if term == "DEF":
            self._tt_set(turn, DFPN_INF, 0)
            return
        is_or = (turn == self.attacker_color)
        kids = self._gen_children(turn, allow_pass=not is_or, depth=depth)
        if not kids:
            self._tt_set(turn, DFPN_INF, 0)
            return

        while True:
            self.nodes += 1
            self._check_limits()
            pn, dn, best_idx, second_best, best_child_pn, best_child_dn = \
                self._aggregate(kids, turn, is_or)
            self._tt_set(turn, pn, dn)
            if pn == 0 or dn == 0:
                if best_idx >= 0 and kids[best_idx][0] is not None:
                    self._record_killer(depth, kids[best_idx][0])
                return
            if pn >= th_pn or dn >= th_dn: return

            if is_or:
                th_pn_c = min(th_pn, int(second_best * 1.25) + 1)
                th_dn_c = th_dn - dn + best_child_dn
            else:
                th_pn_c = th_pn - pn + best_child_pn
                th_dn_c = min(th_dn, int(second_best * 1.25) + 1)

            best_move, _ = kids[best_idx]
            handle = self._play_kid(best_move, turn)
            try:
                self._mid(-turn, depth + 1, th_pn_c, th_dn_c)
            finally:
                self._undo_kid(handle)

    def _aggregate(self, kids, turn, is_or):
        if is_or:
            pn, dn = DFPN_INF, 0
            best_idx, best_pn, second = -1, DFPN_INF, DFPN_INF
            best_child_pn, best_child_dn = DFPN_INF, DFPN_INF
        else:
            pn, dn = 0, DFPN_INF
            best_idx, best_dn, second = -1, DFPN_INF, DFPN_INF
            best_child_pn, best_child_dn = DFPN_INF, DFPN_INF

        for i, (move, _) in enumerate(kids):
            handle = self._play_kid(move, turn)
            if handle is None: continue
            cpn, cdn = self._tt_get(-turn)
            self._undo_kid(handle)
            if is_or:
                if cpn < best_pn:
                    second = best_pn; best_pn = cpn; best_idx = i
                    best_child_pn, best_child_dn = cpn, cdn
                elif cpn < second:
                    second = cpn
                if cpn < pn: pn = cpn
                dn = min(DFPN_INF, dn + cdn)
            else:
                if cdn < best_dn:
                    second = best_dn; best_dn = cdn; best_idx = i
                    best_child_pn, best_child_dn = cpn, cdn
                elif cdn < second:
                    second = cdn
                pn = min(DFPN_INF, pn + cpn)
                if cdn < dn: dn = cdn
        return pn, dn, best_idx, second, best_child_pn, best_child_dn

    # ---- 入口 ----

    def solve(self, current_turn: int) -> dict:
        self.nodes = 0
        self.start_time = time.monotonic()
        self._initial_turn = current_turn
        timed_out = False
        try:
            self._mid(current_turn, 0, DFPN_INF, DFPN_INF)
        except _Timeout:
            timed_out = True
        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
        root_pn, root_dn = self._tt_get(current_turn)
        if root_pn == 0: result = "ATTACKER_WINS"
        elif root_dn == 0: result = "DEFENDER_WINS"
        else: result = "UNPROVEN"
        return {
            "result": result,
            "nodes": self.nodes,
            "elapsed_ms": elapsed_ms,
            "pn": root_pn,
            "dn": root_dn,
            "timed_out": timed_out,
        }
