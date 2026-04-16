// 后端 API 客户端
const API = {
  // 习题 CRUD
  async listProblems() { return (await fetch('/api/problems')).json(); },
  async getProblem(id) { return (await fetch(`/api/problems/${id}`)).json(); },
  async createProblem(name) { return postJson('/api/problems', {name}); },
  async updateProblem(id, fields) {
    const r = await fetch(`/api/problems/${id}`, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(fields),
    });
    return r.json();
  },
  async deleteProblem(id) {
    const r = await fetch(`/api/problems/${id}`, {method: 'DELETE'});
    return r.json();
  },

  // 棋盘
  async play(boardArr, lastCapture, x, y, color, opts = {}) {
    return postJson('/api/play', {
      board: boardArr, last_capture: lastCapture, x, y, color,
      kill_targets: opts.killTargets || [],
      defend_targets: opts.defendTargets || [],
    });
  },
  async validateTarget(boardArr, region, x, y) {
    return postJson('/api/validate_target', {board: boardArr, region, x, y});
  },
  async legalMoves(boardArr, lastCapture, region, color) {
    return postJson('/api/legal_moves', {board: boardArr, last_capture: lastCapture, region, color});
  },

  // 求解
  async solve(boardArr, lastCapture, region, target, turn, opts = {}) {
    return postJson('/api/solve', {
      board: boardArr, last_capture: lastCapture, region, target, turn,
      max_time_ms: opts.maxTimeMs || 60000,
      max_nodes: opts.maxNodes || 5000000,
      precompute_job_id: opts.jobId || null,
    });
  },

};

async function postJson(url, data) {
  const r = await fetch(url, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(data),
  });
  return r.json();
}
