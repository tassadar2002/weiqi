// ============================================================
// 围棋死活与对杀 — 10x10 规则引擎
// 支持撤销式落子 + Zobrist 增量哈希
// ============================================================

const B = 1;   // 黑
const W = -1;  // 白
const E = 0;   // 空
const BOARD_SIZE = 10;

// Zobrist 表：每个 (位置, 颜色) 对应一对 32 位随机数
// 存成 Uint32Array 便于快速位运算
const ZOBRIST_HI = new Uint32Array(BOARD_SIZE * BOARD_SIZE * 2);
const ZOBRIST_LO = new Uint32Array(BOARD_SIZE * BOARD_SIZE * 2);
(function initZobrist() {
  // 使用简单 LCG 保证初始化确定性（跨会话一致）
  let s = 0x12345678;
  function rand32() {
    s = (Math.imul(s, 1664525) + 1013904223) | 0;
    return s >>> 0;
  }
  for (let i = 0; i < ZOBRIST_HI.length; i++) {
    ZOBRIST_HI[i] = rand32();
    ZOBRIST_LO[i] = rand32();
  }
})();

// (索引 i, 颜色 c) → zobrist 表的扁平索引
function _zi(i, c) {
  return i * 2 + (c === B ? 0 : 1);
}

// ---- groupAndLibs 快速路径所需的模块级 scratch（避免每次 new Set）----
const _GAL_VISITED = new Int32Array(BOARD_SIZE * BOARD_SIZE);
const _GAL_LIB_MARK = new Int32Array(BOARD_SIZE * BOARD_SIZE);
let _galEpoch = 0;

function _galBumpEpoch() {
  _galEpoch++;
  if (_galEpoch >= 0x7fffffff) {
    _GAL_VISITED.fill(0);
    _GAL_LIB_MARK.fill(0);
    _galEpoch = 1;
  }
  return _galEpoch;
}

class SimBoard {
  constructor(size = BOARD_SIZE) {
    this.size = size;
    this.grid = new Int8Array(size * size);
    this.lastCapture = -1;
    // 增量维护的 Zobrist 哈希（两个 32 位半）
    this.zhashHi = 0;
    this.zhashLo = 0;
  }

  clone() {
    const sb = new SimBoard(this.size);
    sb.grid.set(this.grid);
    sb.lastCapture = this.lastCapture;
    sb.zhashHi = this.zhashHi;
    sb.zhashLo = this.zhashLo;
    return sb;
  }

  get(x, y) { return this.grid[y * this.size + x]; }

  // 带 Zobrist 维护的 set（仅供布局阶段 + 内部使用）
  set(x, y, v) {
    const i = y * this.size + x;
    const old = this.grid[i];
    if (old === v) return;
    if (old !== E) {
      const zi = _zi(i, old);
      this.zhashHi ^= ZOBRIST_HI[zi];
      this.zhashLo ^= ZOBRIST_LO[zi];
    }
    if (v !== E) {
      const zi = _zi(i, v);
      this.zhashHi ^= ZOBRIST_HI[zi];
      this.zhashLo ^= ZOBRIST_LO[zi];
    }
    this.grid[i] = v;
  }

  inBounds(x, y) { return x >= 0 && x < this.size && y >= 0 && y < this.size; }

  adj(x, y) {
    const a = [];
    if (x > 0) a.push([x - 1, y]);
    if (x < this.size - 1) a.push([x + 1, y]);
    if (y > 0) a.push([x, y - 1]);
    if (y < this.size - 1) a.push([x, y + 1]);
    return a;
  }

  // 找连通块和气数 — 使用 epoch visited + 扁平索引
  // wantGroup: 是否构建 group 数组（[[x,y],...]）和 groupMask Uint8Array
  // 在 play() 的 capture 探测中，绝大多数调用 libs > 0，无需 group 详情
  // 返回 { group, libs, libArray, groupMask }（group 与 groupMask 仅在 wantGroup=true 时有效）
  groupAndLibs(x, y, wantGroup = true) {
    const size = this.size;
    const i0 = y * size + x;
    const grid = this.grid;
    const color = grid[i0];
    if (!color) {
      return { group: [], libs: 0, libArray: [], groupMask: null };
    }
    const epoch = _galBumpEpoch();
    const group = wantGroup ? [] : null;
    const libArray = [];
    const groupMask = wantGroup ? new Uint8Array(size * size) : null;
    const stack = [i0];
    _GAL_VISITED[i0] = epoch;
    if (groupMask) groupMask[i0] = 1;
    while (stack.length) {
      const pos = stack.pop();
      const cx = pos % size;
      const cy = (pos - cx) / size;
      if (group) group.push([cx, cy]);
      // 展开 4 邻
      // 北
      if (cy > 0) {
        const ni = pos - size;
        if (_GAL_VISITED[ni] !== epoch) {
          const s = grid[ni];
          if (s === color) {
            _GAL_VISITED[ni] = epoch;
            if (groupMask) groupMask[ni] = 1;
            stack.push(ni);
          } else if (s === E) {
            if (_GAL_LIB_MARK[ni] !== epoch) {
              _GAL_LIB_MARK[ni] = epoch;
              libArray.push(ni);
            }
          }
        }
      }
      // 南
      if (cy < size - 1) {
        const ni = pos + size;
        if (_GAL_VISITED[ni] !== epoch) {
          const s = grid[ni];
          if (s === color) {
            _GAL_VISITED[ni] = epoch;
            if (groupMask) groupMask[ni] = 1;
            stack.push(ni);
          } else if (s === E) {
            if (_GAL_LIB_MARK[ni] !== epoch) {
              _GAL_LIB_MARK[ni] = epoch;
              libArray.push(ni);
            }
          }
        }
      }
      // 西
      if (cx > 0) {
        const ni = pos - 1;
        if (_GAL_VISITED[ni] !== epoch) {
          const s = grid[ni];
          if (s === color) {
            _GAL_VISITED[ni] = epoch;
            if (groupMask) groupMask[ni] = 1;
            stack.push(ni);
          } else if (s === E) {
            if (_GAL_LIB_MARK[ni] !== epoch) {
              _GAL_LIB_MARK[ni] = epoch;
              libArray.push(ni);
            }
          }
        }
      }
      // 东
      if (cx < size - 1) {
        const ni = pos + 1;
        if (_GAL_VISITED[ni] !== epoch) {
          const s = grid[ni];
          if (s === color) {
            _GAL_VISITED[ni] = epoch;
            if (groupMask) groupMask[ni] = 1;
            stack.push(ni);
          } else if (s === E) {
            if (_GAL_LIB_MARK[ni] !== epoch) {
              _GAL_LIB_MARK[ni] = epoch;
              libArray.push(ni);
            }
          }
        }
      }
    }
    return { group, libs: libArray.length, libArray, groupMask };
  }

  // 撤销式落子：返回 undo 对象（或 null 表示非法）
  // undo 对象字段：
  //   x, y, color: 落子位置与颜色
  //   captured:   被提子坐标数组 [[x, y, color], ...]（顺序稳定）
  //   prevLastCapture: 落子前的 lastCapture 值
  playUndoable(x, y, color) {
    if (!this.inBounds(x, y) || this.get(x, y) !== E) return null;

    const prevLastCapture = this.lastCapture;
    this.set(x, y, color);  // set 内部已更新 Zobrist
    const opp = -color;
    const captured = [];

    // 尝试提对方相邻无气群（先用 wantGroup=false 快速判断 libs）
    const neighbors = this.adj(x, y);
    for (const [ax, ay] of neighbors) {
      if (this.get(ax, ay) === opp) {
        const probe = this.groupAndLibs(ax, ay, false);
        if (probe.libs === 0) {
          // 重做一次以拿到 group 详情进行实际提子
          const full = this.groupAndLibs(ax, ay, true);
          for (const [gx, gy] of full.group) {
            captured.push([gx, gy, opp]);
            this.set(gx, gy, E);
          }
        }
      }
    }

    // 自杀检查（不需要 group 详情）
    const own = this.groupAndLibs(x, y, false);
    if (own.libs === 0) {
      // 回滚：恢复被提的对方子，移除自己
      for (let i = captured.length - 1; i >= 0; i--) {
        const [gx, gy, c] = captured[i];
        this.set(gx, gy, c);
      }
      this.set(x, y, E);
      return null;
    }

    // 简单 ko 检测：自方为单子（无相邻己方棋）且仅提一子
    let ownIsSingle = true;
    const sz = this.size;
    if (y > 0 && this.grid[(y - 1) * sz + x] === color) ownIsSingle = false;
    if (ownIsSingle && y < sz - 1 && this.grid[(y + 1) * sz + x] === color) ownIsSingle = false;
    if (ownIsSingle && x > 0 && this.grid[y * sz + (x - 1)] === color) ownIsSingle = false;
    if (ownIsSingle && x < sz - 1 && this.grid[y * sz + (x + 1)] === color) ownIsSingle = false;

    if (captured.length === 1 && ownIsSingle) {
      const thisKey = y * this.size + x;
      if (prevLastCapture === thisKey) {
        // 打劫禁着 → 回滚
        for (let i = captured.length - 1; i >= 0; i--) {
          const [gx, gy, c] = captured[i];
          this.set(gx, gy, c);
        }
        this.set(x, y, E);
        return null;
      }
      this.lastCapture = captured[0][1] * this.size + captured[0][0];
    } else {
      this.lastCapture = -1;
    }

    return { x, y, color, captured, prevLastCapture };
  }

  // 撤销前一次 playUndoable
  undo(u) {
    if (!u) return;
    // 移除自己
    this.set(u.x, u.y, E);
    // 恢复被提（逆序）
    for (let i = u.captured.length - 1; i >= 0; i--) {
      const [gx, gy, c] = u.captured[i];
      this.set(gx, gy, c);
    }
    this.lastCapture = u.prevLastCapture;
  }

  // 兼容旧接口：返回提子数或 -1
  play(x, y, color) {
    const u = this.playUndoable(x, y, color);
    return u === null ? -1 : u.captured.length;
  }

  count(color) {
    let n = 0;
    for (let i = 0; i < this.grid.length; i++) if (this.grid[i] === color) n++;
    return n;
  }

  // 所有合法落子点（使用 play/undo 避免克隆）
  legalMoves(color) {
    const moves = [];
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        if (this.get(x, y) !== E) continue;
        const u = this.playUndoable(x, y, color);
        if (u !== null) {
          moves.push([x, y]);
          this.undo(u);
        }
      }
    }
    return moves;
  }

  // 限定于 regionMask 内的合法落子点
  legalMovesInRegion(color, regionMask) {
    const moves = [];
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        const idx = y * this.size + x;
        if (!regionMask[idx]) continue;
        if (this.get(x, y) !== E) continue;
        const u = this.playUndoable(x, y, color);
        if (u !== null) {
          moves.push([x, y]);
          this.undo(u);
        }
      }
    }
    return moves;
  }

  // 局面哈希：Zobrist 增量值（两个 32 位半拼接）
  hash() {
    // 拼成字符串作为 Map key（避免 Number 精度冲突）
    return this.zhashHi.toString(36) + ':' + this.zhashLo.toString(36);
  }
}
