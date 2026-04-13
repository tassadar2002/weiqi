// ============================================================
// 应用控制器（前后端分离版）
//
// 所有 Go 规则与 df-pn 求解都在 Python 后端 (/api/*)。前端只负责：
//   - 渲染棋盘
//   - 收集用户事件
//   - 维护展示用的本地状态（落子记录、决策日志）
//
// 与后端的所有交互都是异步的，通过 api.js 中的 API.* 方法。
// ============================================================

const SOLVE_OPTIONS = {
  maxTimeMs: 120000,
  maxNodes: 10000000,
  maxDepth: 60,
};

class App {
  constructor() {
    this.board = new ClientBoard(BOARD_SIZE);
    this.regionMask = createDefaultMask(BOARD_SIZE);
    this.canvas = document.getElementById('board-canvas');
    this.renderer = new BoardRenderer(this.canvas, BOARD_SIZE);

    this.mode = 'layout';   // 'layout' | 'region' | 'solve' | 'pick-target'
    this.placementColor = 'black';
    this.initialSnapshot = null;
    this.targetInfo = null;
    this.waitingForAI = false;
    this.autoplayTimer = null;
    this.autoplayColor = B;
    this.moveHistory = [];
    this.moveCounter = 0;
    this.decisionLog = [];
    // 多目标
    this.killTargets = [];    // [{coord, color, group, libs, stones, eyes}, ...]
    this.defendTargets = [];  // 同上

    this.loadInitialSetup();
    this.bindEvents();
    this.syncRendererMask();
    this.renderBoard();
  }

  loadInitialSetup() {
    const blacks = [
      [0,8],
      [1,5],[1,6],[1,7],
      [2,6],
      [3,6],
      [4,7],
      [5,7],
      [6,5],[6,7],
      [8,6],[8,8],
    ];
    const whites = [
      [1,2],[1,4],
      [1,8],[1,9],
      [2,5],[2,7],
      [3,5],[3,7],
      [4,5],[4,6],
    ];
    for (const [x, y] of blacks) this.board.set(x, y, B);
    for (const [x, y] of whites) this.board.set(x, y, W);
  }

  syncRendererMask() {
    this.renderer.regionMask = this.regionMask;
    this.renderer.showMask = (this.mode !== 'layout');
  }

  bindEvents() {
    this.canvas.addEventListener('click', (e) => this.onCanvasClick(e));
    this.canvas.addEventListener('mousemove', (e) => this.onCanvasMouseMove(e));
    this.canvas.addEventListener('mouseleave', () => {
      this.renderer.ghostStone = null;
      this.renderBoard();
    });

    document.getElementById('sel-black').addEventListener('click', () => this.setPlacementColor('black'));
    document.getElementById('sel-white').addEventListener('click', () => this.setPlacementColor('white'));
    document.getElementById('sel-erase').addEventListener('click', () => this.setPlacementColor('erase'));

    document.getElementById('btn-to-region').addEventListener('click', () => this.enterRegionMode());
    document.getElementById('btn-default-region').addEventListener('click', () => {
      this.regionMask = createDefaultMask(BOARD_SIZE);
      this.syncRendererMask();
      this.renderBoard();
    });
    document.getElementById('btn-clear-region').addEventListener('click', () => {
      this.regionMask = createEmptyMask(BOARD_SIZE);
      this.syncRendererMask();
      this.renderBoard();
    });
    document.getElementById('btn-all-region').addEventListener('click', () => {
      this.regionMask = createFullMask(BOARD_SIZE);
      this.syncRendererMask();
      this.renderBoard();
    });
    document.getElementById('btn-to-solve').addEventListener('click', () => this.enterSolveMode());
    document.getElementById('btn-back-layout-from-region').addEventListener('click', () => this.enterLayoutMode());
    document.getElementById('btn-reset').addEventListener('click', () => this.enterLayoutMode());
    document.getElementById('btn-autoplay').addEventListener('click', () => this.toggleAutoplay());
    document.getElementById('btn-pick-target').addEventListener('click', () => this.enterPickTargetMode());
    document.getElementById('btn-confirm-target').addEventListener('click', () => this.confirmMultiTarget());

    window.addEventListener('resize', () => {
      this.renderer.resize();
      this.renderBoard();
    });
  }

  renderBoard() {
    this.renderer.render(this.board);
  }

  setPlacementColor(color) {
    this.placementColor = color;
    document.querySelectorAll('#color-selector .tool-btn').forEach(btn => btn.classList.remove('active-tool'));
    const idMap = { black: 'sel-black', white: 'sel-white', erase: 'sel-erase' };
    document.getElementById(idMap[color]).classList.add('active-tool');

    const dot = document.getElementById('move-dot');
    const text = document.getElementById('move-text');
    dot.className = '';
    if (color === 'black') {
      text.textContent = '放置黑子';
    } else if (color === 'white') {
      dot.className = 'white';
      text.textContent = '放置白子';
    } else {
      dot.className = 'layout';
      text.textContent = '擦除模式';
    }
  }

  // ============================================================
  // 模式切换
  // ============================================================

  enterLayoutMode() {
    this.stopAutoplay();
    this.mode = 'layout';
    this.renderer.lastMove = null;
    this.renderer.ghostStone = null;
    this.moveHistory = [];
    this.moveCounter = 0;
    this.renderer.moveHistory = this.moveHistory;
    this.decisionLog = [];
    this.renderDecisionLog();
    this.waitingForAI = false;

    if (this.initialSnapshot) {
      this.board.replaceFromArray(this.initialSnapshot.boardArr, -1);
      this.regionMask = cloneMask(this.initialSnapshot.regionMask);
      this.initialSnapshot = null;
    }
    this.targetInfo = null;
    this.killTargets = [];
    this.defendTargets = [];
    this.renderer.targetCoord = null;
    this.renderer.targetGroupCoords = null;
    this.renderer.targetColor = null;
    this.renderer.killGroups = [];
    this.renderer.defendGroups = [];

    document.getElementById('layout-controls').classList.remove('hidden');
    document.getElementById('region-controls').classList.add('hidden');
    document.getElementById('play-controls').classList.add('hidden');
    document.getElementById('decision-log').classList.add('hidden');
    this.hideFeedback();
    this.setPlacementColor(this.placementColor);
    this.syncRendererMask();
    this.renderBoard();
  }

  enterRegionMode() {
    if (this.board.count(B) === 0 || this.board.count(W) === 0) {
      this.showFeedback('incorrect', '请先在棋盘上摆放黑子和白子。');
      return;
    }
    this.mode = 'region';

    document.getElementById('layout-controls').classList.add('hidden');
    document.getElementById('region-controls').classList.remove('hidden');
    document.getElementById('play-controls').classList.add('hidden');
    this.hideFeedback();

    document.getElementById('move-dot').className = 'layout';
    document.getElementById('move-text').textContent = '点击设置落子点';

    this.syncRendererMask();
    this.renderBoard();
  }

  enterSolveMode() {
    if (maskCellCount(this.regionMask) === 0) {
      this.showFeedback('incorrect', '落子点区域不能为空。');
      return;
    }

    this.initialSnapshot = {
      boardArr: this.board.toArray(),
      regionMask: cloneMask(this.regionMask),
    };
    this.mode = 'solve';
    this.renderer.lastMove = null;
    this.renderer.ghostStone = null;
    this.autoplayColor = B;
    this.moveHistory = [];
    this.moveCounter = 0;
    this.renderer.moveHistory = this.moveHistory;
    this.decisionLog = [];
    document.getElementById('btn-autoplay').textContent = '最优解';

    this.targetInfo = null;
    this.renderer.targetCoord = null;
    this.renderer.targetGroupCoords = null;
    this.renderer.targetColor = null;
    this.updateTargetLabel();

    document.getElementById('layout-controls').classList.add('hidden');
    document.getElementById('region-controls').classList.add('hidden');
    document.getElementById('play-controls').classList.remove('hidden');
    document.getElementById('decision-log').classList.remove('hidden');
    this.hideFeedback();

    document.getElementById('move-dot').className = '';
    this.syncRendererMask();
    this.renderBoard();

    // 强制进入"点选目标"子模式
    this.enterPickTargetMode();
  }

  // ============================================================
  // 决策日志
  // ============================================================

  logEntry(entry) {
    this.decisionLog.push(entry);
    this.renderDecisionLog();
  }

  logTargetDecision() {
    if (this.killTargets.length === 0 && this.defendTargets.length === 0) return;

    const killDesc = this.killTargets.map(t =>
      `<span class="cand" style="border-color:#c73e3a;">杀 (${t.coord[0]},${t.coord[1]}) ${t.stones}子/${t.libs}气</span>`
    ).join('');
    const defDesc = this.defendTargets.map(t =>
      `<span class="cand" style="border-color:#3a6ec7;">守 (${t.coord[0]},${t.coord[1]}) ${t.stones}子/${t.libs}气</span>`
    ).join('');

    this.logEntry({
      type: 'target',
      main: `设定复合目标`,
      meta: `杀目标 ${this.killTargets.length} 个 · 守目标 ${this.defendTargets.length} 个`,
      sub: (killDesc || '') + (defDesc || ''),
    });
  }

  logMoveDecision(color, r) {
    const step = this.moveCounter;
    const side = color === B ? '黑' : '白';
    const role = color === this.targetInfo.attacker_color ? '攻' : '防';
    let tag, tagClass;
    if (r.move && r.move.certain) {
      tag = '必胜';
      tagClass = 'tag-win';
    } else if (r.result === 'UNPROVEN') {
      tag = '试探';
      tagClass = 'tag-probe';
    } else {
      tag = '顽抗';
      tagClass = 'tag-resist';
    }
    const posStr = r.move.pass ? 'PASS' : `(${r.move.x},${r.move.y})`;
    const resultStr = r.result === 'ATTACKER_WINS' ? '攻方必胜'
      : r.result === 'DEFENDER_WINS' ? '防方必胜'
      : '未证明';
    const pnStr = r.pn >= 1e9 ? '∞' : r.pn;
    const dnStr = r.dn >= 1e9 ? '∞' : r.dn;

    this.logEntry({
      type: 'move',
      color,
      main: `${side}${role} ${posStr}`,
      step,
      tag, tagClass,
      meta: `${resultStr} · pn=${pnStr} dn=${dnStr} · ${r.nodes.toLocaleString()} 节点 · ${(r.elapsed_ms / 1000).toFixed(2)}s`,
      sub: r.move.certain ? '按证明树推进' : '走顽强抵抗着',
    });
  }

  logTerminal(kind) {
    const text = kind === 'TARGET_CAPTURED' ? '目标被提子 → 攻方胜'
      : kind === 'TARGET_ALIVE' ? '目标已做出双眼 → 防方胜'
      : kind === 'ATK_NO_MOVE' ? '攻方无合法着法 → 防方胜'
      : kind === 'DEF_NO_MOVE' ? '防方无合法着法 → 攻方胜'
      : '对局结束';
    this.logEntry({ type: 'terminal', main: text });
  }

  renderDecisionLog() {
    const list = document.getElementById('log-list');
    if (!list) return;
    list.innerHTML = this.decisionLog.map(e => {
      if (e.type === 'target') {
        return `<div class="log-entry entry-target">
          <div class="log-main">${this.escape(e.main)}</div>
          <div class="log-meta">${this.escape(e.meta)}</div>
          <div class="log-sub">${e.sub || ''}</div>
        </div>`;
      }
      if (e.type === 'move') {
        const cls = e.color === B ? 'entry-move-black' : 'entry-move-white';
        const step = e.step != null ? `<span class="log-step">#${e.step}</span>` : '';
        const tag = e.tag ? `<span class="log-tag ${e.tagClass}">${e.tag}</span>` : '';
        return `<div class="log-entry ${cls}">
          <div class="log-main">${step}${this.escape(e.main)}${tag}</div>
          <div class="log-meta">${this.escape(e.meta)}</div>
          ${e.sub ? `<div class="log-sub">${this.escape(e.sub)}</div>` : ''}
        </div>`;
      }
      if (e.type === 'terminal') {
        return `<div class="log-entry entry-terminal">
          <div class="log-main">${this.escape(e.main)}</div>
        </div>`;
      }
      return '';
    }).join('');
    list.scrollTop = list.scrollHeight;
  }

  escape(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  }

  // ============================================================
  // 落子历史与目标标签
  // ============================================================

  recordMove(x, y, color) {
    // 清理已被提子的旧记录 + 同位置覆盖
    for (let i = this.moveHistory.length - 1; i >= 0; i--) {
      const m = this.moveHistory[i];
      const stale = (this.board.get(m.x, m.y) !== m.color) || (m.x === x && m.y === y);
      if (stale) this.moveHistory.splice(i, 1);
    }
    this.moveCounter++;
    this.moveHistory.push({ x, y, color, number: this.moveCounter });
    this.renderer.moveHistory = this.moveHistory;
  }

  countRegionEmpty() {
    let n = 0;
    for (let i = 0; i < this.regionMask.length; i++) {
      if (!this.regionMask[i]) continue;
      if (this.board.grid[i] === E) n++;
    }
    return n;
  }

  updateTargetLabel() {
    const el = document.getElementById('classify-label');
    const regionTotal = maskCellCount(this.regionMask);
    const empty = this.countRegionEmpty();
    const statsLine = `<div style="margin-top:0.25rem;font-weight:400;color:var(--ink-medium);font-size:0.78rem;">` +
      `区域 ${regionTotal} 格 · 空位 ${empty}</div>`;

    const nk = this.killTargets.length;
    const nd = this.defendTargets.length;
    if (nk === 0 && nd === 0) {
      el.innerHTML = `<div style="color:var(--vermillion);">未选目标（请点选棋子）</div>` + statsLine;
      return;
    }
    const parts = [];
    if (nk > 0) {
      const coords = this.killTargets.map(t => `(${t.coord[0]},${t.coord[1]})`).join(' ');
      parts.push(`<span style="color:#c73e3a;">杀${nk}群 ${coords}</span>`);
    }
    if (nd > 0) {
      const coords = this.defendTargets.map(t => `(${t.coord[0]},${t.coord[1]})`).join(' ');
      parts.push(`<span style="color:#3a6ec7;">守${nd}群 ${coords}</span>`);
    }
    el.innerHTML = `<div>${parts.join(' · ')}</div>` + statsLine;
  }

  displaySolveMeta(r) {
    let label = '';
    if (r.result === 'ATTACKER_WINS') label = '攻方必胜';
    else if (r.result === 'DEFENDER_WINS') label = '防方必胜';
    else label = '未证明';
    const meta = `${label} · ${r.nodes.toLocaleString()} 节点 · ${(r.elapsed_ms / 1000).toFixed(1)}s`;
    this.updateTargetLabel();
    const el = document.getElementById('classify-label');
    el.innerHTML += `<div style="margin-top:0.25rem;font-weight:400;color:var(--jade-dark);font-size:0.78rem;">上一手：${meta}</div>`;
  }

  // ============================================================
  // 点击事件分发
  // ============================================================

  onCanvasClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const coord = this.renderer.pixelToBoard(px, py);
    if (!coord) return;
    const [bx, by] = coord;

    if (this.mode === 'layout') {
      this.onLayoutClick(bx, by);
    } else if (this.mode === 'region') {
      this.onRegionClick(bx, by);
    } else if (this.mode === 'pick-target') {
      this.onPickTargetClick(bx, by);
    } else {
      this.onSolveClick(bx, by);
    }
  }

  onLayoutClick(bx, by) {
    const current = this.board.get(bx, by);
    if (this.placementColor === 'erase') {
      this.board.set(bx, by, E);
    } else {
      const color = this.placementColor === 'black' ? B : W;
      if (current === color) {
        this.board.set(bx, by, E);
      } else {
        this.board.set(bx, by, color);
      }
    }
    this.renderBoard();
  }

  onRegionClick(bx, by) {
    toggleMaskCell(this.regionMask, bx, by, BOARD_SIZE);
    this.renderBoard();
  }

  // ============================================================
  // 解题阶段：手动落子
  // ============================================================

  async onSolveClick(bx, by) {
    if (this.waitingForAI) return;
    if (this.autoplayTimer) return;
    if (!this.targetInfo) {
      this.showFeedback('incorrect', '请先点选目标棋子。');
      return;
    }
    if (!this.regionMask[by * BOARD_SIZE + bx]) {
      this.showFeedback('incorrect', '该点不在落子点范围内。');
      return;
    }
    if (this.board.get(bx, by) !== E) return;

    this.waitingForAI = true;
    let r;
    try {
      r = await API.play(
        this.board.toArray(),
        this.board.lastCapture,
        bx, by, B,
        {
          targetCoord: this.targetInfo ? this.targetInfo.target_coord : null,
          killTargets: this.killTargets.map(t => t.coord),
          defendTargets: this.defendTargets.map(t => t.coord),
        },
      );
    } catch (err) {
      this.waitingForAI = false;
      this.showFeedback('incorrect', '后端通信失败：' + err.message);
      return;
    }
    this.waitingForAI = false;

    if (!r.ok) {
      this.showFeedback('incorrect', r.error || '非法落子');
      return;
    }
    this.applyPlayResult(bx, by, B, r);
    this.logEntry({
      type: 'move',
      color: B,
      main: `黑手动 (${bx},${by})`,
      step: this.moveCounter,
      tag: '用户', tagClass: 'tag-probe',
      meta: '用户手动落子',
    });
    this.autoplayColor = W;

    if (this.checkPostMoveTerminal(r)) return;

    document.getElementById('move-text').textContent = '白棋思考中...';
    this.waitingForAI = true;
    setTimeout(() => this.computeReply(W), 50);
  }

  // 把后端 /api/play 的成功响应应用到本地状态
  applyPlayResult(x, y, color, r) {
    this.board.replaceFromArray(r.new_board, r.last_capture);
    this.recordMove(x, y, color);
    this.renderer.lastMove = [x, y, color === B ? 'black' : 'white'];
    // 更新多目标高亮
    if (r.multi_status) {
      this._updateMultiHighlightFromStatus(r.multi_status);
    } else if (r.target_status) {
      this.renderer.targetGroupCoords = r.target_status.group;
    }
    this.hideFeedback();
    this.renderBoard();
  }

  _updateMultiHighlightFromStatus(ms) {
    // 用后端返回的最新群坐标更新渲染
    if (ms.kill_statuses) {
      for (let i = 0; i < this.killTargets.length && i < ms.kill_statuses.length; i++) {
        const s = ms.kill_statuses[i];
        if (s && s.group) this.killTargets[i].group = s.group;
      }
    }
    if (ms.defend_statuses) {
      for (let i = 0; i < this.defendTargets.length && i < ms.defend_statuses.length; i++) {
        const s = ms.defend_statuses[i];
        if (s && s.group) this.defendTargets[i].group = s.group;
      }
    }
    this.syncMultiTargetHighlight();
  }

  // 由 multi_status 或 target_status 判断是否到达棋盘终局
  checkPostMoveTerminal(r) {
    // 优先使用多目标状态
    if (r.multi_status) {
      if (r.multi_status.terminal === 'ATTACKER_WINS') {
        this.showTerminal('TARGET_CAPTURED');
        return true;
      }
      if (r.multi_status.terminal === 'DEFENDER_WINS') {
        if (r.multi_status.any_defend_captured) {
          this.showTerminal('DEFEND_LOST');
        } else {
          this.showTerminal('TARGET_ALIVE');
        }
        return true;
      }
      return false;
    }
    // 旧单目标兼容
    if (!r.target_status) return false;
    if (r.target_status.captured) {
      this.showTerminal('TARGET_CAPTURED');
      return true;
    }
    if (r.target_status.alive) {
      this.showTerminal('TARGET_ALIVE');
      return true;
    }
    return false;
  }

  // ============================================================
  // 解题阶段：调 solver 应手
  // ============================================================

  async computeReply(color) {
    let r;
    try {
      r = await API.solve(
        this.board.toArray(),
        this.board.lastCapture,
        Array.from(this.regionMask),
        this.targetInfo,
        color,
        SOLVE_OPTIONS,
      );
    } catch (err) {
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('incorrect', '后端通信失败：' + err.message);
      return;
    }
    this.displaySolveMeta(r);

    if (!r.move) {
      this.waitingForAI = false;
      this.stopAutoplay();
      if (color === this.targetInfo.attacker_color) {
        this.showFeedback('incorrect', '攻方无合法着法，防方胜。');
        this.logTerminal('ATK_NO_MOVE');
      } else {
        this.showFeedback('correct', '防方无合法着法，攻方胜。');
        this.logTerminal('DEF_NO_MOVE');
      }
      document.getElementById('move-text').textContent = '对局结束';
      return;
    }

    if (r.move.pass) {
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('correct', '防方已安全，无需落子（pass）。');
      this.logMoveDecision(color, r);
      this.logTerminal('TARGET_ALIVE');
      document.getElementById('move-text').textContent = '对局结束';
      return;
    }

    // 实际把这一手发给后端 /api/play 应用
    let pr;
    try {
      pr = await API.play(
        this.board.toArray(),
        this.board.lastCapture,
        r.move.x, r.move.y, color,
        {
          targetCoord: this.targetInfo ? this.targetInfo.target_coord : null,
          killTargets: this.killTargets.map(t => t.coord),
          defendTargets: this.defendTargets.map(t => t.coord),
        },
      );
    } catch (err) {
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('incorrect', '后端通信失败：' + err.message);
      return;
    }
    if (!pr.ok) {
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('incorrect', `落子(${r.move.x},${r.move.y})非法：${pr.error}`);
      return;
    }
    this.applyPlayResult(r.move.x, r.move.y, color, pr);
    this.logMoveDecision(color, r);

    this.waitingForAI = false;
    this.autoplayColor = -color;

    if (this.checkPostMoveTerminal(pr)) return;

    document.getElementById('move-text').textContent =
      '轮到' + (this.autoplayColor === B ? '黑' : '白') + '棋';
  }

  // ============================================================
  // 多目标选择（点白子=杀目标，点黑子=守目标，再点取消）
  // ============================================================

  enterPickTargetMode() {
    if (this.mode !== 'solve' && this.mode !== 'pick-target') return;
    if (this.autoplayTimer) this.stopAutoplay();
    if (this.waitingForAI) return;
    this.mode = 'pick-target';
    // 不清空已选——允许追加/删除
    this.canvas.style.cursor = 'crosshair';
    document.getElementById('btn-confirm-target').classList.remove('hidden');
    document.getElementById('move-text').textContent = '点击棋子设定目标';
    this.showFeedback('correct',
      '点白子 → 标为杀目标(红)；点黑子 → 标为守目标(蓝)；再点已选的 → 取消。点空点结束。');
    this.syncMultiTargetHighlight();
    this.renderBoard();
  }

  exitPickTargetMode() {
    this.mode = 'solve';
    this.canvas.style.cursor = '';
    document.getElementById('btn-confirm-target').classList.add('hidden');
    this.hideFeedback();
    const hasTarget = this.killTargets.length > 0 || this.defendTargets.length > 0;
    document.getElementById('move-text').textContent = hasTarget ? '轮到黑棋' : '未选目标';
  }

  // 同步 renderer 的多目标高亮
  syncMultiTargetHighlight() {
    this.renderer.killGroups = this.killTargets.map(t => ({ coords: t.group, coord: t.coord }));
    this.renderer.defendGroups = this.defendTargets.map(t => ({ coords: t.group, coord: t.coord }));
    // 清除旧的单目标高亮
    this.renderer.targetCoord = null;
    this.renderer.targetGroupCoords = null;
  }

  // 判断坐标是否已在某个目标列表中（按群判断，不重复添加同群的不同子）
  _findTargetIndex(list, bx, by) {
    for (let i = 0; i < list.length; i++) {
      for (const [gx, gy] of list[i].group) {
        if (gx === bx && gy === by) return i;
      }
    }
    return -1;
  }

  async onPickTargetClick(bx, by) {
    const stone = this.board.get(bx, by);
    if (stone === E) {
      // 点空点 → 退出选择模式
      this.exitPickTargetMode();
      return;
    }

    // 检查是否已在某个目标列表中 → 取消
    const ki = this._findTargetIndex(this.killTargets, bx, by);
    if (ki >= 0) {
      this.killTargets.splice(ki, 1);
      this.syncMultiTargetHighlight();
      this.updateTargetLabel();
      this.renderBoard();
      return;
    }
    const di = this._findTargetIndex(this.defendTargets, bx, by);
    if (di >= 0) {
      this.defendTargets.splice(di, 1);
      this.syncMultiTargetHighlight();
      this.updateTargetLabel();
      this.renderBoard();
      return;
    }

    // 新增：向后端验证
    let info;
    try {
      info = await API.validateTarget(
        this.board.toArray(),
        Array.from(this.regionMask),
        bx, by,
      );
    } catch (err) {
      this.showFeedback('incorrect', '后端通信失败：' + err.message);
      return;
    }
    if (info.error) {
      this.showFeedback('incorrect', info.error);
      return;
    }

    // 按颜色分配：白子→杀目标，黑子→守目标
    if (info.color === W) {
      this.killTargets.push(info);
    } else {
      this.defendTargets.push(info);
    }
    this.syncMultiTargetHighlight();
    this.updateTargetLabel();
    this.renderBoard();

    const role = info.color === W ? '杀目标(红)' : '守目标(蓝)';
    this.showFeedback('correct',
      `已添加 ${role}：(${bx},${by}) ${info.stones}子/${info.libs}气。继续点击或点空点结束。`);
  }

  // "确认目标" 按钮
  confirmMultiTarget() {
    if (this.killTargets.length === 0 && this.defendTargets.length === 0) {
      this.showFeedback('incorrect', '至少需要指定一个目标。');
      return;
    }
    // 构造复合 targetInfo（供 solve 使用）
    this.targetInfo = {
      attacker_color: B,  // 黑棋是攻方
      kill_targets_coords: this.killTargets.map(t => t.coord),
      defend_targets_coords: this.defendTargets.map(t => t.coord),
      // 旧接口兼容
      target_coord: this.killTargets.length > 0 ? this.killTargets[0].coord : (
        this.defendTargets.length > 0 ? this.defendTargets[0].coord : null
      ),
    };

    // 恢复初始局面
    if (this.initialSnapshot) {
      this.board.replaceFromArray(this.initialSnapshot.boardArr, -1);
      this.regionMask = cloneMask(this.initialSnapshot.regionMask);
    }
    this.moveHistory = [];
    this.moveCounter = 0;
    this.renderer.moveHistory = this.moveHistory;
    this.renderer.lastMove = null;
    this.autoplayColor = B;
    this.initialSnapshot = {
      boardArr: this.board.toArray(),
      regionMask: cloneMask(this.regionMask),
    };

    this.updateTargetLabel();
    this.decisionLog = [];
    this.logTargetDecision();
    this.syncMultiTargetHighlight();
    this.renderBoard();
    this.exitPickTargetMode();
  }

  // ============================================================
  // 自动对弈
  // ============================================================

  toggleAutoplay() {
    if (this.autoplayTimer) {
      this.stopAutoplay();
    } else {
      this.startAutoplay();
    }
  }

  startAutoplay() {
    if (this.mode !== 'solve') return;
    if (this.waitingForAI) return;
    if (this.killTargets.length === 0 && this.defendTargets.length === 0) {
      this.showFeedback('incorrect', '请先设定目标棋子。');
      return;
    }
    if (!this.targetInfo) {
      this.showFeedback('incorrect', '请先点击"确认目标"。');
      return;
    }
    document.getElementById('btn-autoplay').textContent = '停止自动';
    this.hideFeedback();
    this.autoplayTimer = true;
    this.scheduleAutoplayStep(0);
  }

  stopAutoplay() {
    if (this.autoplayTimer) {
      if (typeof this.autoplayTimer === 'number') clearTimeout(this.autoplayTimer);
      this.autoplayTimer = null;
    }
    const btn = document.getElementById('btn-autoplay');
    if (btn) btn.textContent = '最优解';
  }

  scheduleAutoplayStep(delayMs) {
    if (!this.autoplayTimer) return;
    this.autoplayTimer = setTimeout(async () => {
      if (!this.autoplayTimer) return;
      await this.autoplayStep();
      if (this.autoplayTimer) this.scheduleAutoplayStep(2000);
    }, delayMs);
  }

  async autoplayStep() {
    if (this.waitingForAI) return;
    const color = this.autoplayColor;
    document.getElementById('move-text').textContent =
      (color === B ? '黑' : '白') + '棋思考中...';
    this.waitingForAI = true;
    await this.computeReply(color);
  }

  showTerminal(type) {
    if (type === 'TARGET_CAPTURED') {
      this.showFeedback('correct', '所有杀目标已被提子，攻方胜！');
    } else if (type === 'TARGET_ALIVE') {
      this.showFeedback('incorrect', '杀目标做活（2 真眼），防方胜。');
    } else if (type === 'DEFEND_LOST') {
      this.showFeedback('incorrect', '守目标被提子，攻方保护失败，防方胜。');
    }
    this.logTerminal(type);
    document.getElementById('move-text').textContent = '对局结束';
    this.stopAutoplay();
  }

  // ============================================================
  // 鼠标 hover 提示
  // ============================================================

  onCanvasMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const coord = this.renderer.pixelToBoard(px, py);

    if (this.mode === 'layout') {
      if (coord && this.placementColor !== 'erase' && this.board.get(coord[0], coord[1]) === E) {
        this.renderer.ghostStone = [coord[0], coord[1], this.placementColor];
      } else {
        this.renderer.ghostStone = null;
      }
    } else if (this.mode === 'region' || this.mode === 'pick-target') {
      this.renderer.ghostStone = null;
    } else {
      if (this.waitingForAI || this.autoplayTimer) {
        this.renderer.ghostStone = null;
      } else if (coord && this.board.get(coord[0], coord[1]) === E
                 && this.regionMask[coord[1] * BOARD_SIZE + coord[0]]) {
        this.renderer.ghostStone = [coord[0], coord[1], 'black'];
      } else {
        this.renderer.ghostStone = null;
      }
    }
    this.renderBoard();
  }

  showFeedback(type, message) {
    const el = document.getElementById('feedback');
    const icon = document.getElementById('feedback-icon');
    const msg = document.getElementById('feedback-message');
    el.className = 'card feedback-card ' + type;
    icon.textContent = type === 'correct' ? '\u2714' : '\u2718';
    msg.textContent = message;
  }

  hideFeedback() {
    document.getElementById('feedback').className = 'card feedback-card hidden';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  new App();
});
