"""
目标群构造（手动指定）

用户在 UI 上点击一颗棋子 → 调用 make_target_from_stone → 返回一个完整的
目标描述对象 (target_info)，包含攻防色、代表子、群信息等。

不做任何启发式选择，只做合法性检查（棋子存在 / 未死 / 未活 / 在区域内）。
"""

from typing import Dict, List, Tuple, Union

from board import Board, EMPTY
from eyes import count_real_eyes, get_target_group


def make_target_from_stone(board: Board, region_mask: List[int],
                           x: int, y: int) -> Dict[str, Union[str, int, list, bool]]:
    """
    构造一个 target_info 对象，或返回 {"error": ...}。

    返回字典字段：
        target_coord:    [x, y] - 用户点击的子作为代表子
        defender_color:  防方颜色（与点击的棋子同色）
        attacker_color:  攻方颜色
        target_libs:     当前气数
        target_stones:   当前子数
        target_eyes:     当前真眼数
        group:           目标群所有子坐标 [[x,y], ...]
        user_picked:     true（标识为用户手动指定）
    """
    color = board.get(x, y)
    if color == EMPTY:
        return {"error": "请点击一个棋子（而不是空点）"}

    group, libs = board.group_and_libs(x, y)
    if len(libs) == 0:
        return {"error": "该群已无气（理论上已死）"}

    target = get_target_group(board, (x, y))
    eyes = count_real_eyes(board, target)
    if eyes >= 2:
        return {"error": "该群已有 2 真眼（已活），无需再解"}

    has_stone_in_region = any(
        region_mask[gy * board.size + gx] for gx, gy in group
    )
    if not has_stone_in_region:
        return {"error": "该群没有子在落子区域内"}

    return {
        "target_coord": [x, y],
        "defender_color": color,
        "attacker_color": -color,
        "target_libs": len(libs),
        "target_stones": len(group),
        "target_eyes": eyes,
        "group": [list(p) for p in group],
        "user_picked": True,
    }
