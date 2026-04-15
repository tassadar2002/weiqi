"""
围棋规则引擎 — 13×13 棋盘

支持：落子+提子、自杀检测、简单 ko、撤销式落子（play_undoable/undo）、群与气的洪水填充。
设计原则：可读性优先。
"""

from typing import Iterator, List, Optional, Set, Tuple

BLACK = 1
WHITE = -1
EMPTY = 0
BOARD_SIZE = 13


class UndoInfo:
    """落子的撤销句柄。"""
    __slots__ = ("x", "y", "color", "captured", "prev_last_capture")

    def __init__(self, x: int, y: int, color: int,
                 captured: List[Tuple[int, int, int]],
                 prev_last_capture: int):
        self.x = x
        self.y = y
        self.color = color
        self.captured = captured          # [(x, y, color), ...]
        self.prev_last_capture = prev_last_capture


class Board:
    """13×13 棋盘。grid 用一维 list，索引 = y * size + x。"""
    __slots__ = ("size", "grid", "last_capture")

    def __init__(self, size: int = BOARD_SIZE):
        self.size = size
        self.grid: List[int] = [EMPTY] * (size * size)
        self.last_capture: int = -1

    def clone(self) -> "Board":
        b = Board(self.size)
        b.grid = list(self.grid)
        b.last_capture = self.last_capture
        return b

    def get(self, x: int, y: int) -> int:
        return self.grid[y * self.size + x]

    def set(self, x: int, y: int, v: int) -> None:
        self.grid[y * self.size + x] = v

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def neighbors(self, x: int, y: int) -> Iterator[Tuple[int, int]]:
        if x > 0: yield x - 1, y
        if x < self.size - 1: yield x + 1, y
        if y > 0: yield x, y - 1
        if y < self.size - 1: yield x, y + 1

    def count(self, color: int) -> int:
        return sum(1 for c in self.grid if c == color)

    # ---- 群与气 ----

    def group_and_libs(self, x: int, y: int) -> Tuple[List[Tuple[int, int]], Set[int]]:
        """洪水填充。返回 (group 坐标列表, libs 扁平索引集合)。"""
        size = self.size
        grid = self.grid
        i0 = y * size + x
        color = grid[i0]
        if color == EMPTY:
            return [], set()

        visited = bytearray(size * size)
        group: List[Tuple[int, int]] = []
        libs: Set[int] = set()
        stack = [i0]
        visited[i0] = 1
        sz_m1 = size - 1

        while stack:
            pos = stack.pop()
            cy, cx = divmod(pos, size)
            group.append((cx, cy))
            if cy > 0:
                ni = pos - size
                if not visited[ni]:
                    visited[ni] = 1
                    s = grid[ni]
                    if s == color: stack.append(ni)
                    elif s == EMPTY: libs.add(ni)
            if cy < sz_m1:
                ni = pos + size
                if not visited[ni]:
                    visited[ni] = 1
                    s = grid[ni]
                    if s == color: stack.append(ni)
                    elif s == EMPTY: libs.add(ni)
            if cx > 0:
                ni = pos - 1
                if not visited[ni]:
                    visited[ni] = 1
                    s = grid[ni]
                    if s == color: stack.append(ni)
                    elif s == EMPTY: libs.add(ni)
            if cx < sz_m1:
                ni = pos + 1
                if not visited[ni]:
                    visited[ni] = 1
                    s = grid[ni]
                    if s == color: stack.append(ni)
                    elif s == EMPTY: libs.add(ni)

        return group, libs

    # ---- 撤销式落子 ----

    def play_undoable(self, x: int, y: int, color: int) -> Optional[UndoInfo]:
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

        # 4 邻提子检测
        if y > 0 and grid[i - size] == opp:
            group, libs = self.group_and_libs(x, y - 1)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))
        if y < sz_m1 and grid[i + size] == opp:
            group, libs = self.group_and_libs(x, y + 1)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))
        if x > 0 and grid[i - 1] == opp:
            group, libs = self.group_and_libs(x - 1, y)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))
        if x < sz_m1 and grid[i + 1] == opp:
            group, libs = self.group_and_libs(x + 1, y)
            if len(libs) == 0:
                for gx, gy in group:
                    grid[gy * size + gx] = EMPTY
                    captured.append((gx, gy, opp))

        # 自杀检测
        _, own_libs = self.group_and_libs(x, y)
        if len(own_libs) == 0:
            for gx, gy, c in reversed(captured):
                grid[gy * size + gx] = c
            grid[i] = EMPTY
            return None

        # 简单 ko
        own_is_single = not (
            (y > 0 and grid[i - size] == color) or
            (y < sz_m1 and grid[i + size] == color) or
            (x > 0 and grid[i - 1] == color) or
            (x < sz_m1 and grid[i + 1] == color)
        )
        if len(captured) == 1 and own_is_single:
            if prev_lc == i:
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
        size = self.size
        grid = self.grid
        grid[u.y * size + u.x] = EMPTY
        for gx, gy, c in reversed(u.captured):
            grid[gy * size + gx] = c
        self.last_capture = u.prev_last_capture

    def play(self, x: int, y: int, color: int) -> Optional[int]:
        u = self.play_undoable(x, y, color)
        return None if u is None else len(u.captured)

    # ---- 合法走法 ----

    def legal_moves_in_region(self, color: int, region_mask: List[int]) -> List[Tuple[int, int]]:
        moves: List[Tuple[int, int]] = []
        size = self.size
        for y in range(size):
            for x in range(size):
                idx = y * size + x
                if not region_mask[idx]: continue
                if self.grid[idx] != EMPTY: continue
                u = self.play_undoable(x, y, color)
                if u is not None:
                    moves.append((x, y))
                    self.undo(u)
        return moves

    # ---- 哈希 ----

    def hash(self) -> str:
        return "".join(chr(48 + g + 1) for g in self.grid)
