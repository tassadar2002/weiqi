"""
df-pn (Depth-First Proof Number) 求解器

针对围棋死活/对杀题目的严格证明搜索。给定一个目标群和攻方颜色，证明：
  - 攻方胜（target 必死）        → result = ATTACKER_WINS
  - 防方胜（target 能做出 2 真眼）→ result = DEFENDER_WINS
  - 在预算内未能证明              → result = UNPROVEN

核心思想：
  - pn (proof number)    = 证明"攻方胜"还需的最小代价
  - dn (disproof number) = 证明"防方胜"还需的最小代价
  - 终止节点：攻方胜 → (0, ∞)；防方胜 → (∞, 0)
  - OR 节点（攻方行棋）：pn = min(子.pn)，dn = sum(子.dn)
  - AND 节点（防方行棋）：pn = sum(子.pn)，dn = min(子.dn)
  - 每次扩展选 pn 最小（OR）或 dn 最小（AND）的子，最像"最易证明的方向"

特性：
  - 转置表 (TT) 缓存每个 (局面, 行棋方) 的 pn/dn
  - **跨请求 TT 缓存**：同一 (region, target, attacker) 组合下，多次 solve 共用一张 TT。
    适用于自动对弈场景——连续 N 手都是同一棵证明树的子局面，复用率极高。
  - 防方允许 pass（处理 seki / 双活）
  - 路径深度上限避免死循环
  - 节点 / 时间双预算
  - 穷举根节点：第一手证完后继续证其他根子节点，便于按棋形要点 (vitalness) 选最优着
  - 提取最优着：必胜按 vitalness 破平；落败方退化为"顽抗着"以保证游戏推进
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from board import Board, EMPTY, UndoInfo
from eyes import count_real_eyes, get_target_group


DFPN_INF = 10**9


# ============================================================
# 跨请求 TT 缓存
# ============================================================
#
# key:   (region_fingerprint, target_coord, attacker_color)
# value: Dict[position_key, (pn, dn)]
#
# 场景：自动对弈 9 手都是同一道题（同 region / target / attacker），
# 每个后续局面都是前一手的子状态，TT 命中率极高。
#
# 内存限制：
#   - 单张 TT 超过 MAX_ENTRIES → 丢弃（防止无限增长）
#   - 缓存中不同 key 超过 MAX_TABLES → 按插入顺序淘汰最旧的
#
# 注意：Python stdlib HTTPServer 单线程处理请求，无需加锁。
# 若改用 ThreadingHTTPServer 则需用 threading.Lock 保护。
# ============================================================

_TT_CACHE: Dict[Tuple[str, Tuple[int, int], int],
                Dict[str, Tuple[int, int]]] = {}
_TT_MAX_ENTRIES_PER_TABLE = 500_000
_TT_MAX_TABLES = 8


def _region_fingerprint(region_mask: List[int]) -> str:
    """把 region_mask 转成紧凑字符串 key（100 字符）。"""
    return "".join("1" if c else "0" for c in region_mask)


def clear_tt_cache() -> None:
    """调试/测试用：清空所有跨请求 TT。"""
    _TT_CACHE.clear()


def tt_cache_stats() -> dict:
    """返回缓存状态：表数量、每表条目数。"""
    return {
        "tables": len(_TT_CACHE),
        "sizes": {str(k): len(v) for k, v in _TT_CACHE.items()},
        "total_entries": sum(len(v) for v in _TT_CACHE.values()),
    }


class _Timeout(Exception):
    """节点 / 时间预算耗尽时抛出。"""
    pass


@dataclass
class Move:
    """提取出的最佳着法。pass=True 表示停一手；x, y 表示落子坐标。"""
    x: Optional[int] = None
    y: Optional[int] = None
    is_pass: bool = False
    certain: bool = False  # True 表示该手已被证明必胜


class DfpnSolver:
    def __init__(self, board: Board, region_mask: List[int],
                 target_coord: Tuple[int, int], attacker_color: int,
                 max_nodes: int = 5_000_000,
                 max_time_ms: int = 60_000,
                 max_depth: int = 60,
                 reuse_tt: bool = True):
        self.board = board
        self.region_mask = region_mask
        self.target_coord = target_coord
        self.attacker_color = attacker_color
        self.defender_color = -attacker_color
        self.max_nodes = max_nodes
        self.max_time_ms = max_time_ms
        self.max_depth = max_depth
        self.reuse_tt = reuse_tt

        # 跨请求 TT 缓存：相同 (region, target, attacker) 复用一张 TT
        self._cache_key = (
            _region_fingerprint(region_mask),
            tuple(target_coord),
            int(attacker_color),
        )
        if reuse_tt:
            self.tt: Dict[str, Tuple[int, int]] = _TT_CACHE.get(self._cache_key, {})
        else:
            self.tt = {}

        self.nodes = 0
        self.start_time = 0.0
        self.tt_hits_at_start = len(self.tt)  # 统计：本次开始时 TT 已有多少条目

        # 计算根目标的气集合，用于 vitalness（棋形要点破平）
        root_target = get_target_group(board, target_coord)
        self.root_target_libs: Set[int] = (
            set(root_target["libs"]) if root_target else set()
        )

    # ============================================================
    # 主入口
    # ============================================================

    def solve(self, current_turn: int) -> dict:
        """
        从 current_turn 行棋的局面开始求解。
        返回字典字段：result, move, nodes, elapsed_ms, pn, dn, timed_out, tt_reused
        """
        # 注意：self.tt 来自 __init__（若启用 reuse_tt，可能包含上一次求解的缓存）
        self.nodes = 0
        self.start_time = time.monotonic()
        timed_out = False

        try:
            self._mid(current_turn, depth=0, th_pn=DFPN_INF, th_dn=DFPN_INF)
        except _Timeout:
            timed_out = True

        root_pn, root_dn = self._tt_get(current_turn)

        # 穷举证明所有根子节点：使 _extract_best_move 能在所有真胜着中按要点选优
        if not timed_out and (root_pn == 0 or root_dn == 0):
            try:
                self._prove_all_root_children(current_turn)
            except _Timeout:
                timed_out = True

        elapsed_ms = int((time.monotonic() - self.start_time) * 1000)

        if root_pn == 0:
            result = "ATTACKER_WINS"
        elif root_dn == 0:
            result = "DEFENDER_WINS"
        else:
            result = "UNPROVEN"

        best = self._extract_best_move(current_turn)
        move_dict: Optional[dict] = None
        if best is not None:
            if best.is_pass:
                move_dict = {"pass": True, "certain": best.certain}
            else:
                move_dict = {"x": best.x, "y": best.y, "certain": best.certain}

        # 回写跨请求 TT 缓存
        tt_final_size = len(self.tt)
        if self.reuse_tt:
            if tt_final_size > _TT_MAX_ENTRIES_PER_TABLE:
                # 表过大 → 整张丢弃，避免无限增长
                _TT_CACHE.pop(self._cache_key, None)
            else:
                _TT_CACHE[self._cache_key] = self.tt
                # 表数量超上限 → 按插入顺序淘汰最旧的（dict 3.7+ 有序）
                while len(_TT_CACHE) > _TT_MAX_TABLES:
                    oldest = next(iter(_TT_CACHE))
                    if oldest == self._cache_key:
                        break
                    del _TT_CACHE[oldest]

        return {
            "result": result,
            "move": move_dict,
            "nodes": self.nodes,
            "elapsed_ms": elapsed_ms,
            "pn": root_pn,
            "dn": root_dn,
            "timed_out": timed_out,
            "tt_reused": self.tt_hits_at_start,
            "tt_final_size": tt_final_size,
        }

    # ============================================================
    # 转置表
    # ============================================================

    def _tt_key(self, turn: int) -> str:
        return f"{self.board.hash()}|{turn}|{self.board.last_capture}"

    def _tt_get(self, turn: int) -> Tuple[int, int]:
        return self.tt.get(self._tt_key(turn), (1, 1))

    def _tt_set(self, turn: int, pn: int, dn: int) -> None:
        self.tt[self._tt_key(turn)] = (pn, dn)

    # ============================================================
    # 终止判定
    # ============================================================

    def _terminal(self) -> Optional[str]:
        """检查当前局面是否对目标群构成终止状态。"""
        tx, ty = self.target_coord
        if self.board.get(tx, ty) != self.defender_color:
            return "ATK"  # 目标代表子已被提
        target = get_target_group(self.board, self.target_coord)
        if target is None:
            return "ATK"
        eyes = count_real_eyes(self.board, target)
        if eyes >= 2:
            return "DEF"
        return None

    # ============================================================
    # 走法生成 + vitalness 棋形要点
    # ============================================================

    def _vitalness(self, x: int, y: int) -> int:
        """落子点 (x,y) 紧到了多少个根目标的气 — 数字越大棋形越要点。"""
        v = 0
        for nx, ny in self.board.neighbors(x, y):
            if (ny * self.board.size + nx) in self.root_target_libs:
                v += 1
        return v

    def _gen_children(self, turn: int, allow_pass: bool
                      ) -> List[Tuple[Optional[Tuple[int, int]], int]]:
        """
        生成所有合法子节点，按 vitalness 降序排序。
        每项是 (move_or_None, vitalness)；move=None 表示 pass。
        """
        moves = self.board.legal_moves_in_region(turn, self.region_mask)
        kids: List[Tuple[Optional[Tuple[int, int]], int]] = [
            ((x, y), self._vitalness(x, y)) for x, y in moves
        ]
        kids.sort(key=lambda k: -k[1])  # vitalness 降序
        if allow_pass:
            kids.append((None, -1))
        return kids

    def _play_kid(self, move: Optional[Tuple[int, int]], turn: int):
        """在当前棋盘上播放一个子节点。返回 (kind, undo_handle)。"""
        if move is None:
            # pass：清空 ko，记录原值便于撤销
            prev_lc = self.board.last_capture
            self.board.last_capture = -1
            return ("pass", prev_lc)
        x, y = move
        u = self.board.play_undoable(x, y, turn)
        if u is None:
            return None
        return ("move", u)

    def _undo_kid(self, handle) -> None:
        if handle is None:
            return
        kind, payload = handle
        if kind == "pass":
            self.board.last_capture = payload
        else:
            self.board.undo(payload)

    # ============================================================
    # 预算检查
    # ============================================================

    def _check_limits(self) -> None:
        if self.nodes >= self.max_nodes:
            raise _Timeout()
        # 每 1024 节点检查一次墙钟时间
        if self.nodes & 1023 == 0:
            elapsed_ms = (time.monotonic() - self.start_time) * 1000
            if elapsed_ms > self.max_time_ms:
                raise _Timeout()

    # ============================================================
    # df-pn 主递归
    # ============================================================

    def _mid(self, turn: int, depth: int, th_pn: int, th_dn: int) -> None:
        self.nodes += 1
        self._check_limits()

        if depth > self.max_depth:
            self._tt_set(turn, DFPN_INF, DFPN_INF)
            return

        # 终止判定
        term = self._terminal()
        if term == "ATK":
            self._tt_set(turn, 0, DFPN_INF)
            return
        if term == "DEF":
            self._tt_set(turn, DFPN_INF, 0)
            return

        is_or = (turn == self.attacker_color)
        kids = self._gen_children(turn, allow_pass=not is_or)

        if not kids:
            # 攻方无合法着 → 防方胜（防方有 pass 不会到这里）
            self._tt_set(turn, DFPN_INF, 0)
            return

        # 迭代细化：每轮重新聚合子节点的 pn/dn，再下探最佳子
        while True:
            self.nodes += 1
            self._check_limits()

            agg = self._aggregate_children(kids, turn, is_or)
            pn, dn, best_idx, second_best, best_child_pn, best_child_dn = agg

            self._tt_set(turn, pn, dn)

            if pn == 0 or dn == 0:
                return
            if pn >= th_pn or dn >= th_dn:
                return

            # 计算下层阈值
            if is_or:
                th_pn_child = min(th_pn, second_best + 1)
                th_dn_child = th_dn - dn + best_child_dn
            else:
                th_pn_child = th_pn - pn + best_child_pn
                th_dn_child = min(th_dn, second_best + 1)

            best_move, _ = kids[best_idx]
            handle = self._play_kid(best_move, turn)
            self._mid(-turn, depth + 1, th_pn_child, th_dn_child)
            self._undo_kid(handle)

    def _aggregate_children(self, kids, turn: int, is_or: bool):
        """
        聚合所有子节点的 pn / dn，返回:
          (pn, dn, best_idx, second_best_metric, best_child_pn, best_child_dn)
        其中 best_idx 是最值得下探的子节点（OR 取最小 pn，AND 取最小 dn）。
        """
        if is_or:
            pn = DFPN_INF
            dn = 0
            best_pn = DFPN_INF
            second_best = DFPN_INF
            best_idx = -1
            best_child_pn = DFPN_INF
            best_child_dn = DFPN_INF
        else:
            pn = 0
            dn = DFPN_INF
            best_dn = DFPN_INF
            second_best = DFPN_INF
            best_idx = -1
            best_child_pn = DFPN_INF
            best_child_dn = DFPN_INF

        for i, (move, _vital) in enumerate(kids):
            handle = self._play_kid(move, turn)
            if handle is None:
                continue
            child_pn, child_dn = self._tt_get(-turn)
            self._undo_kid(handle)

            if is_or:
                if child_pn < best_pn:
                    second_best = best_pn
                    best_pn = child_pn
                    best_idx = i
                    best_child_pn = child_pn
                    best_child_dn = child_dn
                elif child_pn < second_best:
                    second_best = child_pn
                if child_pn < pn:
                    pn = child_pn
                dn = min(DFPN_INF, dn + child_dn)
            else:
                if child_dn < best_dn:
                    second_best = best_dn
                    best_dn = child_dn
                    best_idx = i
                    best_child_pn = child_pn
                    best_child_dn = child_dn
                elif child_dn < second_best:
                    second_best = child_dn
                pn = min(DFPN_INF, pn + child_pn)
                if child_dn < dn:
                    dn = child_dn

        return pn, dn, best_idx, second_best, best_child_pn, best_child_dn

    # ============================================================
    # 穷举根证明（提升 vitalness 破平的命中率）
    # ============================================================

    def _prove_all_root_children(self, current_turn: int) -> None:
        """
        主搜索结束后，对所有未决的根子节点继续 _mid 直到证完。
        让 _extract_best_move 能在 *所有* 真胜着中按棋形要点选最佳。
        """
        kids = self._gen_children(current_turn, allow_pass=current_turn != self.attacker_color)
        for move, _vital in kids:
            if self.nodes > self.max_nodes * 2:
                break
            handle = self._play_kid(move, current_turn)
            if handle is None:
                continue
            child_pn, child_dn = self._tt_get(-current_turn)
            if child_pn == 0 or child_dn == 0:
                self._undo_kid(handle)
                continue
            self._mid(-current_turn, depth=1, th_pn=DFPN_INF, th_dn=DFPN_INF)
            self._undo_kid(handle)

    # ============================================================
    # 提取最佳着法（含 vitalness 破平 + 落败方顽抗）
    # ============================================================

    def _extract_best_move(self, current_turn: int) -> Optional[Move]:
        is_or = (current_turn == self.attacker_color)
        kids = self._gen_children(current_turn, allow_pass=not is_or)

        win_move: Optional[Tuple[int, int]] = None
        win_vital = -1
        win_metric = DFPN_INF + 1
        win_pass = False

        resist_move: Optional[Tuple[int, int]] = None
        resist_metric = -1
        resist_vital = -1
        any_move: Optional[Tuple[int, int]] = None

        for move, vital in kids:
            if move is not None and any_move is None:
                any_move = move
            handle = self._play_kid(move, current_turn)
            if handle is None:
                continue
            child_pn, child_dn = self._tt_get(-current_turn)
            self._undo_kid(handle)
            wins_for_us = (child_pn == 0) if is_or else (child_dn == 0)

            if wins_for_us:
                if move is None:
                    win_pass = True
                else:
                    metric = child_dn if is_or else child_pn
                    if vital > win_vital or (vital == win_vital and metric < win_metric):
                        win_vital = vital
                        win_metric = metric
                        win_move = move
            else:
                # 落败方：选对方胜利指标最大的着法 = 最难推进 = 顽抗
                if move is None:
                    continue
                metric = child_dn if is_or else child_pn
                if metric > resist_metric or (metric == resist_metric and vital > resist_vital):
                    resist_metric = metric
                    resist_vital = vital
                    resist_move = move

        if win_move is not None:
            return Move(x=win_move[0], y=win_move[1], certain=True)
        if win_pass:
            return Move(is_pass=True, certain=True)
        if resist_move is not None:
            return Move(x=resist_move[0], y=resist_move[1], certain=False)
        if any_move is not None:
            return Move(x=any_move[0], y=any_move[1], certain=False)
        return None
