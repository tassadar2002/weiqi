// ============================================================
// 目标与攻防方选择（仅手动）
// selectTarget 已移除。仅保留 makeTargetFromStone 用于用户点选目标。
// ============================================================

// 统计群在区域内的子数
function countStonesInRegion(g, regionMask, size) {
  let n = 0;
  for (const [x, y] of g.group) {
    if (regionMask[y * size + x]) n++;
  }
  return n;
}

// 根据用户点击的棋子构造 targetInfo（手动指定）
// 不做任何启发式，只做合法性检查
// 返回 { ...targetInfo, userPicked: true } 或 { error: 字符串 }
function makeTargetFromStone(board, regionMask, x, y) {
  const color = board.get(x, y);
  if (color === E) return { error: '请点击一个棋子（而不是空点）' };

  const info = board.groupAndLibs(x, y);
  if (info.libs === 0) return { error: '该群已无气（理论上已死）' };

  // 直接用 groupAndLibs 返回的 groupMask/libArray 做真眼判定
  const eyes = countGroupRealEyes(board, {
    color,
    groupMask: info.groupMask,
    libArray: info.libArray,
  });
  if (eyes >= 2) return { error: '该群已有 2 真眼（已活），无需再解' };

  const size = board.size;
  const hasStoneInRegion = info.group.some(([gx, gy]) => regionMask[gy * size + gx]);
  if (!hasStoneInRegion) {
    return { error: '该群没有子在落子区域内' };
  }

  return {
    targetCoord: [x, y],
    defenderColor: color,
    attackerColor: -color,
    targetLibs: info.libs,
    targetStones: info.group.length,
    targetEyes: eyes,
    candidates: [{
      color,
      pos: [x, y],
      stones: info.group.length,
      stonesInRegion: countStonesInRegion({ group: info.group }, regionMask, size),
      libs: info.libs,
      eyes,
    }],
    userPicked: true,
  };
}
