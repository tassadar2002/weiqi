"""
严格真眼判定

真眼条件：
  1. 空点
  2. 4 正交邻全属同一连通群
  3. 对角：边/角全同色；内部 ≥3 同色
"""

from typing import List, Optional, Set, Tuple

from board import Board, EMPTY

DIAG_DIRS = [(-1, -1), (1, -1), (-1, 1), (1, 1)]


def get_target_group(board: Board, coord: Tuple[int, int]) -> Optional[dict]:
    """取 coord 所在群信息。返回 {color, group, group_set, libs, lib_count} 或 None。"""
    tx, ty = coord
    color = board.get(tx, ty)
    if color == EMPTY:
        return None
    group, libs = board.group_and_libs(tx, ty)
    group_set = {gy * board.size + gx for gx, gy in group}
    return {
        "color": color,
        "group": group,
        "group_set": group_set,
        "libs": libs,
        "lib_count": len(libs),
    }


def is_eye_of_group(board: Board, x: int, y: int,
                    color: int, group_set: Set[int]) -> bool:
    size = board.size
    i = y * size + x
    if board.grid[i] != EMPTY:
        return False
    ortho_count = 0
    if y > 0:
        ortho_count += 1
        if (i - size) not in group_set: return False
    if y < size - 1:
        ortho_count += 1
        if (i + size) not in group_set: return False
    if x > 0:
        ortho_count += 1
        if (i - 1) not in group_set: return False
    if x < size - 1:
        ortho_count += 1
        if (i + 1) not in group_set: return False
    on_edge = ortho_count < 4
    diag_in = 0
    diag_same = 0
    for dx, dy in DIAG_DIRS:
        nx, ny = x + dx, y + dy
        if not board.in_bounds(nx, ny): continue
        diag_in += 1
        if board.get(nx, ny) == color: diag_same += 1
    if on_edge:
        return diag_same == diag_in
    return diag_same >= 3


def count_real_eyes(board: Board, target: Optional[dict]) -> int:
    if target is None:
        return 0
    color = target["color"]
    group_set = target["group_set"]
    libs = target["libs"]
    size = board.size
    eyes = 0
    for k in libs:
        x = k % size
        y = k // size
        if is_eye_of_group(board, x, y, color, group_set):
            eyes += 1
            if eyes >= 2:
                return 2
    return eyes
