// ============================================================
// 严格真眼判定（绑定特定目标群）
// ============================================================
//
// 一个空点 P 是"color 方的真眼"当且仅当：
//   1. P 为空点
//   2. P 的 4 个正交邻（存在的）全部属于指定 group（同一连通块）
//   3. 对角线条件：
//      - P 在边/角：所有对角邻（存在的）必须为 color
//      - P 在内部：4 对角中至少 3 个为 color
//
// 返回一个群内的真眼总数（饱和到 2，因为 2 就是活）。
// ============================================================

const DIAG_DIRS = [[-1, -1], [1, -1], [-1, 1], [1, 1]];

// 取得 targetCoord 所在群（及其坐标集合），不存在则返回 null
function getTargetGroup(board, targetCoord) {
  const [tx, ty] = targetCoord;
  const color = board.get(tx, ty);
  if (color === E) return null;
  const info = board.groupAndLibs(tx, ty);
  return {
    color,
    group: info.group,
    groupMask: info.groupMask,
    libs: info.libs,
    libArray: info.libArray,
  };
}

// 判断 (x,y) 空点是否为 target 群的真眼
// groupMask: Uint8Array，1 表示该索引属于目标群
function isEyeOfGroup(board, x, y, color, groupMask) {
  const size = board.size;
  const i = y * size + x;
  if (board.grid[i] !== E) return false;

  // 正交邻：全部必须属于本群
  // 展开判断以避免分配临时数组
  let orthoCount = 0;
  if (y > 0) {
    const ni = i - size;
    if (!groupMask[ni]) return false;
    orthoCount++;
  }
  if (y < size - 1) {
    const ni = i + size;
    if (!groupMask[ni]) return false;
    orthoCount++;
  }
  if (x > 0) {
    const ni = i - 1;
    if (!groupMask[ni]) return false;
    orthoCount++;
  }
  if (x < size - 1) {
    const ni = i + 1;
    if (!groupMask[ni]) return false;
    orthoCount++;
  }
  const onEdge = orthoCount < 4;

  // 对角检测
  let diagInBounds = 0;
  let diagSame = 0;
  for (let d = 0; d < 4; d++) {
    const nx = x + DIAG_DIRS[d][0];
    const ny = y + DIAG_DIRS[d][1];
    if (nx < 0 || nx >= size || ny < 0 || ny >= size) continue;
    diagInBounds++;
    if (board.grid[ny * size + nx] === color) diagSame++;
  }

  if (onEdge) return diagSame === diagInBounds;
  return diagSame >= 3;
}

// 统计指定群的真眼数（饱和到 2）
function countGroupRealEyes(board, target) {
  if (!target || !target.groupMask) return 0;
  const { color, groupMask, libArray } = target;
  const size = board.size;
  let eyes = 0;
  for (let i = 0; i < libArray.length; i++) {
    const k = libArray[i];
    const x = k % size;
    const y = (k - x) / size;
    if (isEyeOfGroup(board, x, y, color, groupMask)) {
      eyes++;
      if (eyes >= 2) return 2;
    }
  }
  return eyes;
}
