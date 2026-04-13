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
    this.renderer.targetCoord = null;
    this.renderer.targetGroupCoords = null;
    this.renderer.targetColor = null;

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
    const t = this.targetInfo;
    if (!t) return;
    const defStr = t.defender_color === B ? '黑' : '白';
    const atkStr = t.attacker_color === B ? '黑' : '白';
    const [tx, ty] = t.target_coord;
    const mark = t.user_picked
      ? ' <span class="cand" style="background:rgba(91,140,111,0.2)">用户指定</span>'
      : '';
    this.logEntry({
      type: 'target',
      main: `选定目标：${defStr}@(${tx},${ty})`,
      meta: `${t.target_stones} 子 · ${t.target_libs} 气 · ${t.target_eyes} 眼`,
      sub: `防方 ${defStr} · 攻方 ${atkStr}${mark}`,
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
    const t = this.targetInfo;
    const el = document.getElementById('classify-label');
    const regionTotal = maskCellCount(this.regionMask);
    const empty = this.countRegionEmpty();
    const statsLine = `<div style="margin-top:0.25rem;font-weight:400;color:var(--ink-medium);font-size:0.78rem;">` +
      `区域 ${regionTotal} 格 · 空位 ${empty}</div>`;
    if (!t) {
      el.innerHTML = `<div style="color:var(--vermillion);">未选目标（请点选棋子）</div>` + statsLine;
      return;
    }
    const defStr = t.defender_color === B ? '黑' : '白';
    const atkStr = t.attacker_color === B ? '黑' : '白';
    const [tx, ty] = t.target_coord;
    el.innerHTML =
      `<div>目标：${defStr}@(${tx},${ty}) ${t.target_stones}子/${t.target_libs}气 · 攻方：${atkStr}</div>` + statsLine;
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
        this.targetInfo.target_coord,
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
    if (r.target_status) {
      this.renderer.targetGroupCoords = r.target_status.group;
    }
    this.hideFeedback();
    this.renderBoard();
  }

  // 由 r.target_status 判断是否到达棋盘终局；返回 true 表示终局已处理
  checkPostMoveTerminal(r) {
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
        this.targetInfo.target_coord,
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
  // 手动选目标
  // ============================================================

  enterPickTargetMode() {
    if (this.mode !== 'solve' && this.mode !== 'pick-target') return;
    if (this.autoplayTimer) this.stopAutoplay();
    if (this.waitingForAI) return;
    this.mode = 'pick-target';
    this.canvas.style.cursor = 'crosshair';
    document.getElementById('move-text').textContent = '点击任意棋子设为目标';
    this.showFeedback('correct',
      '请点击你想作为目标的棋子（黑子=黑方防守，白子=白方防守）。点击空点取消。');
  }

  exitPickTargetMode() {
    this.mode = 'solve';
    this.canvas.style.cursor = '';
    this.hideFeedback();
    document.getElementById('move-text').textContent = this.targetInfo ? '轮到黑棋' : '未选目标';
  }

  async onPickTargetClick(bx, by) {
    const stone = this.board.get(bx, by);
    if (stone === E) {
      this.exitPickTargetMode();
      return;
    }
    let result;
    try {
      result = await API.makeTarget(
        this.board.toArray(),
        Array.from(this.regionMask),
        bx, by,
      );
    } catch (err) {
      this.showFeedback('incorrect', '后端通信失败：' + err.message);
      return;
    }
    if (result.error) {
      this.showFeedback('incorrect', result.error);
      return;
    }

    // 切换目标 → 恢复初始局面（避免历史走法和新目标矛盾）
    if (this.initialSnapshot) {
      this.board.replaceFromArray(this.initialSnapshot.boardArr, -1);
      this.regionMask = cloneMask(this.initialSnapshot.regionMask);
    }
    this.targetInfo = result;
    this.renderer.targetCoord = result.target_coord;
    this.renderer.targetColor = result.defender_color;
    this.renderer.targetGroupCoords = result.target_status
      ? result.target_status.group
      : (result.group || []);
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
    if (!this.targetInfo) {
      this.showFeedback('incorrect', '请先点选目标棋子。');
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
      this.showFeedback('correct', '目标已被提子，攻方胜。');
    } else if (type === 'TARGET_ALIVE') {
      this.showFeedback('incorrect', '目标已做活（2 真眼），防方胜。');
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
