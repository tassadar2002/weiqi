// ============================================================
// df-pn (Depth-First Proof Number) 死活 / 对杀求解器
// ============================================================
//
// 全局语义：
//   "proof"  = 攻方胜（target 必死）
//   "disproof" = 防方胜（target 必活）
//   pn  = 证明攻方胜所需的最小代价
//   dn  = 证明防方胜所需的最小代价
//
// 终止条件（精确，无启发式）：
//   1. target 被提子（代表子所在位置不再是 target color） → 攻方胜 (pn=0, dn=INF)
//   2. target 所在群有 ≥ 2 个真眼 → 防方胜 (pn=INF, dn=0)
//
// 节点类型：
//   攻方行棋 → OR 节点：pn = min child.pn, dn = sum child.dn
//   防方行棋 → AND 节点：pn = sum child.pn, dn = min child.dn
//
// 防方允许 pass（处理 seki / 双活）；攻方不许 pass（无法杀 = 输）
//
// 未能在预算内证明时，根节点返回 UNPROVEN（严格模式，不给错误答案）
// ============================================================

const DFPN_INF = 1000000000;
const DFPN_UNIT_INIT = 1;  // 未知叶子的初始 pn/dn
const DFPN_TIMEOUT = Symbol('dfpn-timeout');

// 求解状态（模块级）
let _tt = null;
let _nodes = 0;
let _t0 = 0;
let _maxNodes = 5000000;
let _maxTimeMs = 60000;
let _maxDepth = 60;
let _target = null;
let _attackerColor = 0;
let _regionMask = null;
let _rootTargetLibs = null;  // Set of lib keys of target group at root，用于棋形破平

function _checkLimits() {
  if (_nodes >= _maxNodes) throw DFPN_TIMEOUT;
  if ((_nodes & 4095) === 0) {
    if (performance.now() - _t0 > _maxTimeMs) throw DFPN_TIMEOUT;
  }
}

// TT key：局面 + 执棋方 + 劫状态
function _key(board, turn) {
  return board.hash() + '|' + turn + '|' + board.lastCapture;
}

function _ttGet(board, turn) {
  const e = _tt.get(_key(board, turn));
  if (e) return e;
  return { pn: DFPN_UNIT_INIT, dn: DFPN_UNIT_INIT };
}

function _ttSet(board, turn, pn, dn) {
  _tt.set(_key(board, turn), { pn, dn });
}

// 终止判断 — 高性能版：单次扫描完成 flood fill + 真眼判定
// 返回 'ATK' | 'DEF' | null
const _TERMLIB = new Int32Array(BOARD_SIZE * BOARD_SIZE);
const _TERMSTACK = new Int32Array(BOARD_SIZE * BOARD_SIZE);
const _TERMVIS = new Int32Array(BOARD_SIZE * BOARD_SIZE);
let _termEpoch = 0;
function _termBump() {
  _termEpoch++;
  if (_termEpoch >= 0x7fffffff) { _TERMVIS.fill(0); _termEpoch = 1; }
  return _termEpoch;
}

function _terminal(board) {
  const size = board.size;
  const grid = board.grid;
  const tx = _target[0];
  const ty = _target[1];
  const i0 = ty * size + tx;
  const defenderColor = -_attackerColor;
  if (grid[i0] !== defenderColor) return 'ATK';

  const epoch = _termBump();
  // libs scratch: 用 _TERMLIB 数组作为简单 dedup（>= epoch 表示已加）
  let libCount = 0;
  let stackTop = 0;
  _TERMSTACK[stackTop++] = i0;
  _TERMVIS[i0] = epoch;
  const sizeM1 = size - 1;
  // libs 索引存在模块级 array（最多 100 项）
  const libIdx = _libIdxArr;

  while (stackTop > 0) {
    const pos = _TERMSTACK[--stackTop];
    const cx = pos % size;
    const cy = (pos - cx) / size;
    // 4 邻
    // 北
    if (cy > 0) {
      const ni = pos - size;
      if (_TERMVIS[ni] !== epoch) {
        const s = grid[ni];
        if (s === defenderColor) {
          _TERMVIS[ni] = epoch;
          _TERMSTACK[stackTop++] = ni;
        } else if (s === E && _TERMLIB[ni] !== epoch) {
          _TERMLIB[ni] = epoch;
          libIdx[libCount++] = ni;
        }
      }
    }
    // 南
    if (cy < sizeM1) {
      const ni = pos + size;
      if (_TERMVIS[ni] !== epoch) {
        const s = grid[ni];
        if (s === defenderColor) {
          _TERMVIS[ni] = epoch;
          _TERMSTACK[stackTop++] = ni;
        } else if (s === E && _TERMLIB[ni] !== epoch) {
          _TERMLIB[ni] = epoch;
          libIdx[libCount++] = ni;
        }
      }
    }
    // 西
    if (cx > 0) {
      const ni = pos - 1;
      if (_TERMVIS[ni] !== epoch) {
        const s = grid[ni];
        if (s === defenderColor) {
          _TERMVIS[ni] = epoch;
          _TERMSTACK[stackTop++] = ni;
        } else if (s === E && _TERMLIB[ni] !== epoch) {
          _TERMLIB[ni] = epoch;
          libIdx[libCount++] = ni;
        }
      }
    }
    // 东
    if (cx < sizeM1) {
      const ni = pos + 1;
      if (_TERMVIS[ni] !== epoch) {
        const s = grid[ni];
        if (s === defenderColor) {
          _TERMVIS[ni] = epoch;
          _TERMSTACK[stackTop++] = ni;
        } else if (s === E && _TERMLIB[ni] !== epoch) {
          _TERMLIB[ni] = epoch;
          libIdx[libCount++] = ni;
        }
      }
    }
  }

  if (libCount === 0) return 'ATK';

  // 真眼判定：用 _TERMVIS[i] === epoch 表示"属于目标群"
  let eyes = 0;
  for (let li = 0; li < libCount; li++) {
    const k = libIdx[li];
    const x = k % size;
    const y = (k - x) / size;

    let isEye = true;
    let orthoIn = 0;
    if (y > 0) {
      orthoIn++;
      if (_TERMVIS[k - size] !== epoch) { isEye = false; }
    }
    if (isEye && y < sizeM1) {
      orthoIn++;
      if (_TERMVIS[k + size] !== epoch) { isEye = false; }
    }
    if (isEye && x > 0) {
      orthoIn++;
      if (_TERMVIS[k - 1] !== epoch) { isEye = false; }
    }
    if (isEye && x < sizeM1) {
      orthoIn++;
      if (_TERMVIS[k + 1] !== epoch) { isEye = false; }
    }
    if (!isEye) continue;

    // 对角检测
    let diagIn = 0, diagSame = 0;
    if (x > 0 && y > 0) {
      diagIn++;
      if (grid[(y - 1) * size + (x - 1)] === defenderColor) diagSame++;
    }
    if (x < sizeM1 && y > 0) {
      diagIn++;
      if (grid[(y - 1) * size + (x + 1)] === defenderColor) diagSame++;
    }
    if (x > 0 && y < sizeM1) {
      diagIn++;
      if (grid[(y + 1) * size + (x - 1)] === defenderColor) diagSame++;
    }
    if (x < sizeM1 && y < sizeM1) {
      diagIn++;
      if (grid[(y + 1) * size + (x + 1)] === defenderColor) diagSame++;
    }

    const onEdge = orthoIn < 4;
    const isReal = onEdge ? (diagSame === diagIn) : (diagSame >= 3);
    if (isReal) {
      eyes++;
      if (eyes >= 2) return 'DEF';
    }
  }

  return null;
}
const _libIdxArr = new Int32Array(BOARD_SIZE * BOARD_SIZE);

// 生成子节点：[{move, vital}]，move=null 表 pass。按 vitalness 降序排序。
// 不再克隆棋盘——调用方用 _playKid / _undoKid 就地落子/撤销。
function _genChildren(board, turn, allowPass) {
  const moves = board.legalMovesInRegion(turn, _regionMask);
  const kids = [];
  for (const [x, y] of moves) {
    const v = _vitalness(board, x, y);
    kids.push({ move: [x, y], vital: v });
  }
  kids.sort((a, b) => b.vital - a.vital);
  if (allowPass) kids.push({ move: null, vital: -1 });
  return kids;
}

// 就地"播放"一个 kid 走法，返回 undo 句柄（pass 用特殊句柄）
function _playKid(board, kid, turn) {
  if (kid.move === null) {
    const u = { pass: true, prevLC: board.lastCapture };
    board.lastCapture = -1;
    return u;
  }
  return board.playUndoable(kid.move[0], kid.move[1], turn);
}

function _undoKid(board, u) {
  if (!u) return;
  if (u.pass) {
    board.lastCapture = u.prevLC;
  } else {
    board.undo(u);
  }
}

// df-pn 主递归
function _mid(board, turn, depth, thPn, thDn) {
  _nodes++;
  _checkLimits();

  // 路径深度上限 → 标记为未知
  if (depth > _maxDepth) {
    _ttSet(board, turn, DFPN_INF, DFPN_INF);
    return;
  }

  // 终止判断
  const term = _terminal(board);
  if (term === 'ATK') {
    _ttSet(board, turn, 0, DFPN_INF);
    return;
  }
  if (term === 'DEF') {
    _ttSet(board, turn, DFPN_INF, 0);
    return;
  }

  const isOR = (turn === _attackerColor);
  // 防方允许 pass；攻方不允许
  const kids = _genChildren(board, turn, !isOR);

  if (kids.length === 0) {
    // 无着可走：攻方输（无法杀）→ 防方胜
    // （防方不会走到这里，因为 pass 总是可用）
    _ttSet(board, turn, DFPN_INF, 0);
    return;
  }

  // 迭代探索
  while (true) {
    _nodes++;
    _checkLimits();

    // 聚合子节点的 pn/dn：就地 play → 读 TT → undo
    let pn, dn, bestIdx = -1, best2nd = DFPN_INF;
    let bestChildPn = DFPN_INF, bestChildDn = DFPN_INF;
    if (isOR) {
      pn = DFPN_INF; dn = 0;
      let bestPn = DFPN_INF;
      for (let i = 0; i < kids.length; i++) {
        const u = _playKid(board, kids[i], turn);
        if (!u) continue;
        const e = _ttGet(board, -turn);
        _undoKid(board, u);
        if (e.pn < bestPn) {
          best2nd = bestPn;
          bestPn = e.pn;
          bestIdx = i;
          bestChildPn = e.pn;
          bestChildDn = e.dn;
        } else if (e.pn < best2nd) {
          best2nd = e.pn;
        }
        if (e.pn < pn) pn = e.pn;
        dn = dn + e.dn;
        if (dn > DFPN_INF) dn = DFPN_INF;
      }
    } else {
      pn = 0; dn = DFPN_INF;
      let bestDn = DFPN_INF;
      for (let i = 0; i < kids.length; i++) {
        const u = _playKid(board, kids[i], turn);
        if (!u) continue;
        const e = _ttGet(board, -turn);
        _undoKid(board, u);
        if (e.dn < bestDn) {
          best2nd = bestDn;
          bestDn = e.dn;
          bestIdx = i;
          bestChildPn = e.pn;
          bestChildDn = e.dn;
        } else if (e.dn < best2nd) {
          best2nd = e.dn;
        }
        pn = pn + e.pn;
        if (pn > DFPN_INF) pn = DFPN_INF;
        if (e.dn < dn) dn = e.dn;
      }
    }

    _ttSet(board, turn, pn, dn);

    if (pn === 0 || dn === 0) return;
    if (pn >= thPn || dn >= thDn) return;

    // 递归入 best：就地 play → 递归 → undo
    const best = kids[bestIdx];
    let thPnChild, thDnChild;
    if (isOR) {
      thPnChild = Math.min(thPn, best2nd + 1);
      thDnChild = thDn - dn + bestChildDn;
    } else {
      thPnChild = thPn - pn + bestChildPn;
      thDnChild = Math.min(thDn, best2nd + 1);
    }
    const bu = _playKid(board, best, turn);
    _mid(board, -turn, depth + 1, thPnChild, thDnChild);
    _undoKid(board, bu);
  }
}

// 计算一个候选落子的"要点性"：相邻点中有多少属于根目标气
// vitalness 越高 = 该手同时紧到越多目标气 / 破坏越多潜在眼位
function _vitalness(board, x, y) {
  if (!_rootTargetLibs || _rootTargetLibs.size === 0) return 0;
  let v = 0;
  for (const [nx, ny] of board.adj(x, y)) {
    if (_rootTargetLibs.has(ny * board.size + nx)) v++;
  }
  return v;
}

// 从根的 TT 中提取最佳着法
// 优先级：
//   1. 必胜非 pass 着（先按 vitalness 降序，同 vitalness 取对手证明最小 = 最快获胜路径）
//   2. 必胜 pass（仅防方）
//   3. 无必胜时的顽强抵抗着（对手最难推进者，同 metric 按 vitalness 破平）
//   4. 任何合法着（兜底，保证游戏前进）
function _extractBestMove(board, turn) {
  const isOR = (turn === _attackerColor);
  const kids = _genChildren(board, turn, !isOR);

  let winMove = null;
  let winVital = -1;
  let winMetric = DFPN_INF + 1;
  let winPass = false;

  let resistMove = null;
  let resistMetric = -1;
  let resistVital = -1;
  let anyMove = null;

  for (const kid of kids) {
    if (kid.move !== null && anyMove === null) anyMove = kid.move;
    const u = _playKid(board, kid, turn);
    if (!u) continue;
    const e = _ttGet(board, -turn);
    _undoKid(board, u);
    const winsForUs = isOR ? (e.pn === 0) : (e.dn === 0);

    if (winsForUs) {
      if (kid.move === null) {
        winPass = true;
      } else {
        const v = kid.vital;
        const m = isOR ? e.dn : e.pn;
        if (v > winVital || (v === winVital && m < winMetric)) {
          winVital = v;
          winMetric = m;
          winMove = kid.move;
        }
      }
    } else {
      if (kid.move === null) continue;
      const r = isOR ? e.dn : e.pn;
      const v = kid.vital;
      if (r > resistMetric || (r === resistMetric && v > resistVital)) {
        resistMetric = r;
        resistVital = v;
        resistMove = kid.move;
      }
    }
  }

  if (winMove) return { x: winMove[0], y: winMove[1], certain: true };
  if (winPass) return { pass: true, certain: true };
  if (resistMove) return { x: resistMove[0], y: resistMove[1], certain: false };
  if (anyMove) return { x: anyMove[0], y: anyMove[1], certain: false };
  return null;
}

// 主入口
// options: { maxNodes, maxTimeMs, maxDepth }
// 返回 { result, move, nodes, elapsedMs, pn, dn }
//   result: 'ATTACKER_WINS' | 'DEFENDER_WINS' | 'UNPROVEN'
//   move:   { x, y } | { pass: true } | null
function solveDfpn(board, regionMask, targetInfo, currentTurn, options = {}) {
  _tt = new Map();
  _nodes = 0;
  _t0 = performance.now();
  _maxNodes = options.maxNodes || 5000000;
  _maxTimeMs = options.maxTimeMs || 60000;
  _maxDepth = options.maxDepth || 60;
  _target = targetInfo.targetCoord;
  _attackerColor = targetInfo.attackerColor;
  _regionMask = regionMask;

  // 计算根目标气集合（用于 _extractBestMove 的 vitalness 破平）
  const rootTgt = getTargetGroup(board, targetInfo.targetCoord);
  _rootTargetLibs = rootTgt ? new Set(rootTgt.libArray) : new Set();

  let timedOut = false;
  try {
    _mid(board, currentTurn, 0, DFPN_INF, DFPN_INF);
  } catch (e) {
    if (e === DFPN_TIMEOUT) timedOut = true;
    else throw e;
  }

  const root = _ttGet(board, currentTurn);

  // 穷举证明所有根子节点：即使第一手已证明，仍继续证明其他胜着，
  // 使 _extractBestMove 能按 vitalness 在多个必胜手中选出棋形要点
  if (!timedOut && (root.pn === 0 || root.dn === 0)) {
    try {
      const rootKids = _genChildren(board, currentTurn, currentTurn !== _attackerColor);
      for (const kid of rootKids) {
        if (_nodes > _maxNodes * 2) break;
        const u = _playKid(board, kid, currentTurn);
        if (!u) continue;
        const e = _ttGet(board, -currentTurn);
        if (e.pn === 0 || e.dn === 0) {
          _undoKid(board, u);
          continue;
        }
        _mid(board, -currentTurn, 1, DFPN_INF, DFPN_INF);
        _undoKid(board, u);
      }
    } catch (e) {
      if (e === DFPN_TIMEOUT) timedOut = true;
      else throw e;
    }
  }

  const elapsedMs = Math.round(performance.now() - _t0);

  let result;
  if (root.pn === 0) result = 'ATTACKER_WINS';
  else if (root.dn === 0) result = 'DEFENDER_WINS';
  else result = 'UNPROVEN';

  // 始终尝试提取一手：保证游戏能推进到棋盘实际终局
  const move = _extractBestMove(board, currentTurn);

  console.log(`[dfpn] ${result} pn=${root.pn} dn=${root.dn} nodes=${_nodes} time=${elapsedMs}ms timeout=${timedOut}`);
  return {
    result,
    move,
    nodes: _nodes,
    elapsedMs,
    pn: root.pn,
    dn: root.dn,
    timedOut,
  };
}
