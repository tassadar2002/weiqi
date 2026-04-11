"""
严格真眼判定（绑定特定目标群）

一个空点 P 是 color 方的真眼，当且仅当：
  1. P 为空点
  2. P 的 4 个正交邻**全部属于指定的连通群**（不仅是同色）
  3. 对角条件：
     - P 在边/角（≤ 3 个正交邻在棋盘内）：所有存在的对角必须为 color 或墙
     - P 在内部（4 个正交邻都在）：4 对角中至少 3 个为 color

注意第 2 条："属于同一群"是关键。否则 4 个独立同色子围成的"伪眼"会被误判。
"""

from typing import List, Optional, Set, Tuple, TypedDict

from board import Board, EMPTY


DIAG_DIRS = [(-1, -1), (1, -1), (-1, 1), (1, 1)]


class TargetView(TypedDict):
    """供真眼检测使用的目标群描述。"""
    color: int
    group: List[Tuple[int, int]]
    group_set: Set[int]   # 群中所有子的扁平索引（用于 O(1) 成员判断）
    libs: Set[int]        # 气的扁平索引集合
    lib_count: int


def get_target_group(board: Board, target_coord: Tuple[int, int]) -> Optional[TargetView]:
    """根据代表子坐标取出当前的目标群信息；若该位置已为空则返回 None。"""
    tx, ty = target_coord
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
    """判断空点 (x,y) 是否为指定群的真眼。"""
    if board.get(x, y) != EMPTY:
        return False

    # 1. 4 个正交邻必须全部属于本群
    ortho = list(board.neighbors(x, y))
    for nx, ny in ortho:
        if (ny * board.size + nx) not in group_set:
            return False

    on_edge = len(ortho) < 4

    # 2. 对角检查
    diag_in_bounds = 0
    diag_same = 0
    for dx, dy in DIAG_DIRS:
        nx, ny = x + dx, y + dy
        if not board.in_bounds(nx, ny):
            continue
        diag_in_bounds += 1
        if board.get(nx, ny) == color:
            diag_same += 1

    if on_edge:
        # 边/角：所有存在的对角必须同色
        return diag_same == diag_in_bounds
    # 内部：4 对角中 ≥ 3 个同色
    return diag_same >= 3


def count_real_eyes(board: Board, target: Optional[TargetView]) -> int:
    """统计目标群的真眼数（饱和到 2，因为 2 真眼即为活）。"""
    if target is None:
        return 0
    color = target["color"]
    group_set = target["group_set"]
    libs = target["libs"]

    eyes = 0
    for k in libs:
        x = k % board.size
        y = k // board.size
        if is_eye_of_group(board, x, y, color, group_set):
            eyes += 1
            if eyes >= 2:
                return 2
    return eyes
