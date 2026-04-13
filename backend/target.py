"""
目标群构造（手动指定）

支持两种模式：
  1. 单目标：make_target_from_stone — 兼容旧接口
  2. 多目标：validate_target_stone — 验证单个点击的合法性（不限颜色语义）
              build_multi_target  — 把多个坐标组合成复合目标

复合目标的语义：
  - kill_targets:   攻方要杀掉的群（通常是对方颜色）
  - defend_targets: 攻方要保住的群（通常是己方颜色）
  - 攻方胜 = 所有 kill_targets 全部被提子
  - 防方胜 = 任一 kill_target 做出双眼 OR 任一 defend_target 被提子
"""

from typing import Dict, List, Optional, Tuple, Union

from board import Board, BLACK, WHITE, EMPTY
from eyes import count_real_eyes, get_target_group


# ============================================================
# 单点验证（前端每次点击时调用）
# ============================================================

def validate_target_stone(board: Board, region_mask: List[int],
                          x: int, y: int) -> Dict[str, Union[str, int, list, bool]]:
    """
    验证 (x,y) 是否可以作为目标点。不限制必须是"杀"还是"守"——那由前端决定。
    返回群信息或 {"error": ...}。
    """
    color = board.get(x, y)
    if color == EMPTY:
        return {"error": "请点击一个棋子（而不是空点）"}

    group, libs = board.group_and_libs(x, y)
    if len(libs) == 0:
        return {"error": "该群已无气（理论上已死）"}

    target = get_target_group(board, (x, y))
    eyes = count_real_eyes(board, target)

    has_stone_in_region = any(
        region_mask[gy * board.size + gx] for gx, gy in group
    )
    if not has_stone_in_region:
        return {"error": "该群没有子在落子区域内"}

    return {
        "coord": [x, y],
        "color": color,
        "libs": len(libs),
        "stones": len(group),
        "eyes": eyes,
        "group": [list(p) for p in group],
    }


# ============================================================
# 复合目标构造
# ============================================================

def build_multi_target(board: Board, region_mask: List[int],
                       kill_coords: List[Tuple[int, int]],
                       defend_coords: List[Tuple[int, int]],
                       attacker_color: int) -> Dict[str, Union[str, list, int]]:
    """
    把多组坐标组装成复合目标对象。每个坐标代表一个群（用该坐标作为代表子）。

    参数：
        kill_coords:    攻方要杀掉的群的代表子坐标列表
        defend_coords:  攻方要保住的群的代表子坐标列表
        attacker_color: 攻方颜色 (BLACK 或 WHITE)

    返回：
        {
            "kill_targets":    [{"coord", "color", "libs", "stones", "eyes", "group"}, ...],
            "defend_targets":  [{"coord", "color", "libs", "stones", "eyes", "group"}, ...],
            "attacker_color":  int,
        }
        或 {"error": ...}
    """
    if not kill_coords and not defend_coords:
        return {"error": "至少需要指定一个目标"}

    kill_targets = []
    for x, y in kill_coords:
        info = validate_target_stone(board, region_mask, x, y)
        if "error" in info:
            return {"error": f"杀目标 ({x},{y}) 无效：{info['error']}"}
        if info["eyes"] >= 2:
            return {"error": f"杀目标 ({x},{y}) 已有 2 真眼（已活），无法杀"}
        kill_targets.append(info)

    defend_targets = []
    for x, y in defend_coords:
        info = validate_target_stone(board, region_mask, x, y)
        if "error" in info:
            return {"error": f"守目标 ({x},{y}) 无效：{info['error']}"}
        defend_targets.append(info)

    return {
        "kill_targets": kill_targets,
        "defend_targets": defend_targets,
        "attacker_color": attacker_color,
    }


# ============================================================
# 兼容旧接口
# ============================================================

def make_target_from_stone(board: Board, region_mask: List[int],
                           x: int, y: int) -> dict:
    """旧接口：单目标，点击的子颜色即防方。"""
    info = validate_target_stone(board, region_mask, x, y)
    if "error" in info:
        return info
    if info["eyes"] >= 2:
        return {"error": "该群已有 2 真眼（已活），无需再解"}

    color = info["color"]
    return {
        "target_coord": [x, y],
        "defender_color": color,
        "attacker_color": -color,
        "target_libs": info["libs"],
        "target_stones": info["stones"],
        "target_eyes": info["eyes"],
        "group": info["group"],
        "user_picked": True,
        # 也产出多目标兼容格式
        "kill_targets": [info] if color != BLACK else [],
        "defend_targets": [info] if color == BLACK else [],
    }
