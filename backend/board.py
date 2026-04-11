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


class UndoInfo:
    """落子的撤销句柄。`captured` 是被提子的坐标和颜色，便于反向恢复。"""
    __slots__ = ("x", "y", "color", "captured", "prev_last_capture")

    def __init__(self, x: int, y: int, color: int,
                 captured: List[Tuple[int, int, int]],
                 prev_last_capture: int):
        self.x = x
        self.y = y
        self.color = color
        self.captured = captured
        self.prev_last_capture = prev_last_capture


class Board:
    """10×10 棋盘。grid 用一维 list 存储，索引 = y * size + x。"""

    __slots__ = ("size", "grid", "last_capture")

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

        此函数是热点：使用扁平索引 + bytearray visited + 内联 4 邻判断，避免
        Python 生成器与元组分配开销。
        """
        size = self.size
        grid = self.grid
        i0 = y * size + x
        color = grid[i0]
        if color == EMPTY:
            return [], set()

        # 本地变量缓存（在 PyPy 下帮助 JIT；CPython 下减少属性查找）
        sz_m1 = size - 1
        visited = bytearray(size * size)
        group: List[Tuple[int, int]] = []
        libs: Set[int] = set()

        stack: List[int] = [i0]
        visited[i0] = 1

        while stack:
            pos = stack.pop()
            # 由扁平索引还原 (x,y)
            cy, cx = divmod(pos, size)
            group.append((cx, cy))

            # --- 北 ---
            if cy > 0:
                ni = pos - size
                if not visited[ni]:
                    s = grid[ni]
                    if s == color:
                        visited[ni] = 1
                        stack.append(ni)
                    elif s == EMPTY:
                        libs.add(ni)
            # --- 南 ---
            if cy < sz_m1:
                ni = pos + size
                if not visited[ni]:
                    s = grid[ni]
                    if s == color:
                        visited[ni] = 1
                        stack.append(ni)
                    elif s == EMPTY:
                        libs.add(ni)
            # --- 西 ---
            if cx > 0:
                ni = pos - 1
                if not visited[ni]:
                    s = grid[ni]
                    if s == color:
                        visited[ni] = 1
                        stack.append(ni)
                    elif s == EMPTY:
                        libs.add(ni)
            # --- 东 ---
            if cx < sz_m1:
                ni = pos + 1
                if not visited[ni]:
                    s = grid[ni]
                    if s == color:
                        visited[ni] = 1
                        stack.append(ni)
                    elif s == EMPTY:
                        libs.add(ni)

        return group, libs

    # ---- 落子（撤销式 + 兼容包装）----

    def play_undoable(self, x: int, y: int, color: int) -> Optional[UndoInfo]:
        """
        在 (x,y) 落子。原地修改棋盘，返回 UndoInfo 句柄。
        若非法（越界 / 占用 / 自杀 / 打劫禁着），返回 None。
        """
        size = self.size
        grid = self.grid
        if x < 0 or x >= size or y < 0 or y >= size:
            return None
        i = y * size + x
        if grid[i] != EMPTY:
            return None

        prev_lc = self.last_capture
        grid[i] = color
        opp = -color
        captured: List[Tuple[int, int, int]] = []
        sz_m1 = size - 1

        # 检查 4 邻是否有对方无气群（内联 4 邻，避免生成器调用）
        # 北
        if y > 0 and grid[i - size] == opp:
            group, libs = self.group_and_libs(x, y - 1)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))
        # 南
        if y < sz_m1 and grid[i + size] == opp:
            group, libs = self.group_and_libs(x, y + 1)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))
        # 西
        if x > 0 and grid[i - 1] == opp:
            group, libs = self.group_and_libs(x - 1, y)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))
        # 东
        if x < sz_m1 and grid[i + 1] == opp:
            group, libs = self.group_and_libs(x + 1, y)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))

        # 自杀检测
        _, own_libs = self.group_and_libs(x, y)
        if len(own_libs) == 0:
            # 回滚
            for gx, gy, c in reversed(captured):
                grid[gy * size + gx] = c
            grid[i] = EMPTY
            return None

        # 简单 ko：当且仅当"自方为单子且仅提一子"时检查
        own_is_single = not (
            (y > 0 and grid[i - size] == color) or
            (y < sz_m1 and grid[i + size] == color) or
            (x > 0 and grid[i - 1] == color) or
            (x < sz_m1 and grid[i + 1] == color)
        )
        if len(captured) == 1 and own_is_single:
            if prev_lc == i:
                # 打劫禁着 → 回滚
                for gx, gy, c in reversed(captured):
                    grid[gy * size + gx] = c
                grid[i] = EMPTY
                return None
            cx, cy, _ = captured[0]
            self.last_capture = cy * size + cx
        else:
            self.last_capture = -1

        return UndoInfo(x, y, color, captured, prev_lc)

    def undo(self, u: UndoInfo) -> None:
        """撤销 play_undoable 的修改。"""
        size = self.size
        grid = self.grid
        grid[u.y * size + u.x] = EMPTY
        captured = u.captured
        for k in range(len(captured) - 1, -1, -1):
            gx, gy, c = captured[k]
            grid[gy * size + gx] = c
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
