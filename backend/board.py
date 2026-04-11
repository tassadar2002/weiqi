"""
SimBoard - 围棋规则引擎（Python 端）

10×10 棋盘，支持：
  - 落子 + 提子
  - 自杀检测
  - 简单 ko 检测（禁立即回提）
  - 撤销式落子（play_undoable + undo），便于搜索时高效回溯
  - 群与气的洪水填充

设计原则：可读性 > 性能。Python 单线程速度对解题题目尺寸够用。
"""

from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Set, Tuple


# ---- 棋子常量 ----
BLACK = 1
WHITE = -1
EMPTY = 0
BOARD_SIZE = 10


@dataclass
class UndoInfo:
    """落子的撤销句柄。`captured` 是被提子的坐标和颜色，便于反向恢复。"""
    x: int
    y: int
    color: int
    captured: List[Tuple[int, int, int]] = field(default_factory=list)
    prev_last_capture: int = -1


class Board:
    """10×10 棋盘。grid 用一维 list 存储，索引 = y * size + x。"""

    def __init__(self, size: int = BOARD_SIZE):
        self.size = size
        self.grid: List[int] = [EMPTY] * (size * size)
        self.last_capture: int = -1  # 最近一次单子提子的位置（用于 ko），-1 表无

    # ---- 基本访问 ----

    def get(self, x: int, y: int) -> int:
        return self.grid[y * self.size + x]

    def set(self, x: int, y: int, value: int) -> None:
        self.grid[y * self.size + x] = value

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def neighbors(self, x: int, y: int) -> Iterator[Tuple[int, int]]:
        """生成 (x,y) 的 4 个正交邻居，自动跳过越界点。"""
        if x > 0:
            yield x - 1, y
        if x < self.size - 1:
            yield x + 1, y
        if y > 0:
            yield x, y - 1
        if y < self.size - 1:
            yield x, y + 1

    def clone(self) -> "Board":
        b = Board(self.size)
        b.grid = list(self.grid)
        b.last_capture = self.last_capture
        return b

    def count(self, color: int) -> int:
        return sum(1 for c in self.grid if c == color)

    # ---- 群与气 ----

    def group_and_libs(self, x: int, y: int) -> Tuple[List[Tuple[int, int]], Set[int]]:
        """
        从 (x,y) 出发洪水填充，返回：
          - group: 同色连通块的所有 (x,y) 坐标列表
          - libs:  该群的气集合（每个气存为扁平索引 y*size+x）
        若起点为空点，返回空数据。
        """
        color = self.get(x, y)
        if color == EMPTY:
            return [], set()

        group: List[Tuple[int, int]] = []
        libs: Set[int] = set()
        visited: Set[Tuple[int, int]] = set()
        stack: List[Tuple[int, int]] = [(x, y)]

        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in visited:
                continue
            cell = self.get(cx, cy)
            if cell != color:
                if cell == EMPTY:
                    libs.add(cy * self.size + cx)
                continue
            visited.add((cx, cy))
            group.append((cx, cy))
            for nx, ny in self.neighbors(cx, cy):
                stack.append((nx, ny))

        return group, libs

    # ---- 落子（撤销式 + 兼容包装）----

    def play_undoable(self, x: int, y: int, color: int) -> Optional[UndoInfo]:
        """
        在 (x,y) 落子。原地修改棋盘，返回 UndoInfo 句柄。
        若非法（越界 / 占用 / 自杀 / 打劫禁着），返回 None。
        """
        if not self.in_bounds(x, y) or self.get(x, y) != EMPTY:
            return None

        prev_lc = self.last_capture
        self.set(x, y, color)
        opp = -color
        captured: List[Tuple[int, int, int]] = []

        # 检查 4 邻是否有对方无气群
        for nx, ny in self.neighbors(x, y):
            if self.get(nx, ny) == opp:
                group, libs = self.group_and_libs(nx, ny)
                if len(libs) == 0:
                    for gx, gy in group:
                        self.set(gx, gy, EMPTY)
                        captured.append((gx, gy, opp))

        # 自杀检测
        _, own_libs = self.group_and_libs(x, y)
        if len(own_libs) == 0:
            # 回滚
            for gx, gy, c in reversed(captured):
                self.set(gx, gy, c)
            self.set(x, y, EMPTY)
            return None

        # 简单 ko：当且仅当"自方为单子且仅提一子"时检查
        own_is_single = all(
            self.get(nx, ny) != color
            for nx, ny in self.neighbors(x, y)
        )
        if len(captured) == 1 and own_is_single:
            this_key = y * self.size + x
            if prev_lc == this_key:
                # 打劫禁着 → 回滚
                for gx, gy, c in reversed(captured):
                    self.set(gx, gy, c)
                self.set(x, y, EMPTY)
                return None
            cx, cy, _ = captured[0]
            self.last_capture = cy * self.size + cx
        else:
            self.last_capture = -1

        return UndoInfo(
            x=x, y=y, color=color,
            captured=captured,
            prev_last_capture=prev_lc,
        )

    def undo(self, u: UndoInfo) -> None:
        """撤销 play_undoable 的修改。"""
        self.set(u.x, u.y, EMPTY)
        for gx, gy, c in reversed(u.captured):
            self.set(gx, gy, c)
        self.last_capture = u.prev_last_capture

    def play(self, x: int, y: int, color: int) -> Optional[int]:
        """便捷接口：落子并返回提子数；非法返回 None。"""
        u = self.play_undoable(x, y, color)
        return None if u is None else len(u.captured)

    # ---- 合法走法枚举 ----

    def legal_moves_in_region(self, color: int, region_mask: List[int]) -> List[Tuple[int, int]]:
        """枚举区域内的所有合法落子点。使用 play/undo 避免克隆。"""
        moves: List[Tuple[int, int]] = []
        for y in range(self.size):
            for x in range(self.size):
                idx = y * self.size + x
                if not region_mask[idx]:
                    continue
                if self.get(x, y) != EMPTY:
                    continue
                u = self.play_undoable(x, y, color)
                if u is not None:
                    moves.append((x, y))
                    self.undo(u)
        return moves

    # ---- 哈希（用于转置表）----

    def hash(self) -> str:
        """
        100 字符精确字符串：每个格子编码为 '0' / '1' / '2'。
        无碰撞，可直接作为 dict key。
        """
        return "".join(chr(48 + g + 1) for g in self.grid)
