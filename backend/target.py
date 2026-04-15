"""
目标群构造（手动指定）

validate_target_stone: 验证单个棋子是否可作为目标
"""

from typing import Dict, List, Union

from board import Board, EMPTY
from eyes import count_real_eyes, get_target_group


def validate_target_stone(board: Board, region_mask: List[int],
                          x: int, y: int) -> Dict[str, Union[str, int, list]]:
    """验证 (x,y) 是否可作为目标点。返回群信息或 {error: ...}。"""
    color = board.get(x, y)
    if color == EMPTY:
        return {"error": "请点击一个棋子（而不是空点）"}
    group, libs = board.group_and_libs(x, y)
    if len(libs) == 0:
        return {"error": "该群已无气"}
    has_stone_in_region = any(
        region_mask[gy * board.size + gx] for gx, gy in group
    )
    if not has_stone_in_region:
        return {"error": "该群没有子在落子区域内"}
    target = get_target_group(board, (x, y))
    eyes = count_real_eyes(board, target)
    return {
        "coord": [x, y],
        "color": color,
        "libs": len(libs),
        "stones": len(group),
        "eyes": eyes,
        "group": [list(p) for p in group],
    }
