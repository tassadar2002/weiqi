// ============================================================
// 后端 API 客户端
// 所有方法均返回 Promise；调用方需用 await 处理
// ============================================================

const API = {

  // 落子（验证 + 应用）。支持多目标：传 killTargets / defendTargets 坐标列表
  async play(boardArr, lastCapture, x, y, color, opts = {}) {
    return await postJson('/api/play', {
      board: boardArr,
      last_capture: lastCapture,
      x, y, color,
      target_coord: opts.targetCoord || null,
      kill_targets: opts.killTargets || [],
      defend_targets: opts.defendTargets || [],
    });
  },

  // 验证单个棋子是否可作为目标（不区分杀/守）
  async validateTarget(boardArr, region, x, y) {
    return await postJson('/api/validate_target', {
      board: boardArr,
      region,
      x, y,
    });
  },

  // 由用户点击的棋子构造目标群
  async makeTarget(boardArr, region, x, y) {
    return await postJson('/api/make_target', {
      board: boardArr,
      region,
      x, y,
    });
  },

  // 查询目标群当前状态（活/死/气/眼）
  async inspectTarget(boardArr, lastCapture, targetCoord) {
    return await postJson('/api/inspect_target', {
      board: boardArr,
      last_capture: lastCapture,
      target_coord: targetCoord,
    });
  },

  // 枚举区域内合法走法
  async legalMoves(boardArr, lastCapture, region, color) {
    return await postJson('/api/legal_moves', {
      board: boardArr,
      last_capture: lastCapture,
      region,
      color,
    });
  },

  // 运行 df-pn 求解
  async solve(boardArr, lastCapture, region, target, turn, options = {}) {
    return await postJson('/api/solve', {
      board: boardArr,
      last_capture: lastCapture,
      region,
      target,
      turn,
      max_time_ms: options.maxTimeMs || 60000,
      max_nodes: options.maxNodes || 5000000,
      max_depth: options.maxDepth || 60,
    });
  },
};

async function postJson(url, data) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!r.ok) {
    throw new Error(`API ${url} → ${r.status} ${r.statusText}`);
  }
  return await r.json();
}
