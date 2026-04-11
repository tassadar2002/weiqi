// ============================================================
// 应用控制器：布局 → 设置落子点 → 解题（df-pn）
// ============================================================

const SOLVE_OPTIONS = {
  maxTimeMs: 120000,   // 单步最长 2 分钟
  maxNodes: 10000000,  // 1000 万节点
  maxDepth: 60,
};

class App {
  constructor() {
    this.board = new SimBoard(BOARD_SIZE);
    this.regionMask = createDefaultMask(BOARD_SIZE);
    this.canvas = document.getElementById('board-canvas');
    this.renderer = new BoardRenderer(this.canvas, BOARD_SIZE);

    this.mode = 'layout';              // 'layout' | 'region' | 'solve' | 'pick-target'
    this.placementColor = 'black';
    this.initialSnapshot = null;       // {board, regionMask}
    this.targetInfo = null;            // selectTarget 结果
    this.waitingForAI = false;
    this.autoplayTimer = null;
    this.autoplayColor = B;            // 自动对弈时下一个落子方
    this.moveHistory = [];             // [{x, y, color, number}]
    this.moveCounter = 0;              // 持久步数计数器
    this.decisionLog = [];             // 决策/落子记录

    this.loadInitialSetup();
    this.bindEvents();
    this.syncRendererMask();
    this.renderBoard();
  }

  loadInitialSetup() {
    const blacks = [
      [1,3],[2,3],[4,3],
      [3,4],
      [4,5],[5,5],
      [3,6],
      [3,7],
      [0,8],[1,8],[2,8],
      [1,9],
    ];
    const whites = [
      [1,4],
      [1,5],[2,5],
      [1,6],[5,6],
      [0,7],[7,7],
      [3,8],[3,9],
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

  // ===== 模式切换 =====

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
      this.board = this.initialSnapshot.board.clone();
      this.regionMask = cloneMask(this.initialSnapshot.regionMask);
      this.initialSnapshot = null;
    }
    this.targetInfo = null;
    this.renderer.targetCoord = null;

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

    const dot = document.getElementById('move-dot');
    const text = document.getElementById('move-text');
    dot.className = 'layout';
    text.textContent = '点击设置落子点';

    this.syncRendererMask();
    this.renderBoard();
  }

  enterSolveMode() {
    if (maskCellCount(this.regionMask) === 0) {
      this.showFeedback('incorrect', '落子点区域不能为空。');
      return;
    }

    // 保存快照
    this.initialSnapshot = {
      board: this.board.clone(),
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

    // 目标由用户手动指定（进入解题后强制先选目标）
    this.targetInfo = null;
    this.renderer.targetCoord = null;
    this.updateTargetLabel();

    document.getElementById('layout-controls').classList.add('hidden');
    document.getElementById('region-controls').classList.add('hidden');
    document.getElementById('play-controls').classList.remove('hidden');
    document.getElementById('decision-log').classList.remove('hidden');
    this.hideFeedback();

    document.getElementById('move-dot').className = '';
    this.syncRendererMask();
    this.renderBoard();

    // 自动进入选目标子模式
    this.enterPickTargetMode();
  }

  // ===== 决策日志 =====

  logEntry(entry) {
    this.decisionLog.push(entry);
    this.renderDecisionLog();
  }

  logTargetDecision() {
    const t = this.targetInfo;
    if (!t) return;
    const defStr = t.defenderColor === B ? '黑' : '白';
    const atkStr = t.attackerColor === B ? '黑' : '白';
    const [tx, ty] = t.targetCoord;
    const mark = t.userPicked ? ' <span class="cand" style="background:rgba(91,140,111,0.2)">用户指定</span>' : '';
    const candsHtml = (t.candidates || []).map((c, i) => {
      const cStr = c.color === B ? '黑' : '白';
      const marker = i === 0 ? '✓ ' : '';
      return `<span class="cand">${marker}${cStr}@(${c.pos[0]},${c.pos[1]}) ${c.stones}子/${c.libs}气/${c.eyes}眼</span>`;
    }).join('');
    this.logEntry({
      type: 'target',
      main: `选定目标：${defStr}@(${tx},${ty})`,
      meta: `${t.targetStones} 子 · ${t.targetLibs} 气 · ${t.targetEyes} 眼`,
      sub: `防方 ${defStr} · 攻方 ${atkStr}${mark}` + (candsHtml ? '<br>候选：' + candsHtml : ''),
    });
  }

  logMoveDecision(color, r) {
    const step = this.moveCounter;
    const side = color === B ? '黑' : '白';
    const role = color === this.targetInfo.attackerColor ? '攻' : '防';
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
      meta: `${resultStr} · pn=${pnStr} dn=${dnStr} · ${r.nodes.toLocaleString()} 节点 · ${(r.elapsedMs / 1000).toFixed(2)}s`,
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
    // 自动滚到底部
    list.scrollTop = list.scrollHeight;
  }

  escape(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  }

  // 记录一手到 moveHistory；使用持久计数器，幸存子保留原序号
  recordMove(x, y, color) {
    // 清理：已被提子的历史条目 + 同点旧条目（被当前手覆盖）
    for (let i = this.moveHistory.length - 1; i >= 0; i--) {
      const m = this.moveHistory[i];
      const stale = (this.board.get(m.x, m.y) !== m.color) || (m.x === x && m.y === y);
      if (stale) this.moveHistory.splice(i, 1);
    }
    this.moveCounter++;
    this.moveHistory.push({ x, y, color, number: this.moveCounter });
    this.renderer.moveHistory = this.moveHistory;
  }

  // 统计落子区域内的空位数（两方的合法落子点上界）
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
    const bLegal = this.board.legalMovesInRegion(B, this.regionMask).length;
    const wLegal = this.board.legalMovesInRegion(W, this.regionMask).length;
    const statsLine = `<div style="margin-top:0.25rem;font-weight:400;color:var(--ink-medium);font-size:0.78rem;">` +
      `区域 ${regionTotal} 格 · 空位 ${empty} · 合法 黑${bLegal}/白${wLegal}</div>`;
    if (!t) {
      el.innerHTML = `<div style="color:var(--vermillion);">未选目标（请点选棋子）</div>` + statsLine;
      return;
    }
    const defStr = t.defenderColor === B ? '黑' : '白';
    const atkStr = t.attackerColor === B ? '黑' : '白';
    const [tx, ty] = t.targetCoord;
    el.innerHTML =
      `<div>目标：${defStr}@(${tx},${ty}) ${t.targetStones}子/${t.targetLibs}气 · 攻方：${atkStr}</div>` + statsLine;
  }

  // ===== 点击处理 =====

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

  onSolveClick(bx, by) {
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

    const captured = this.board.play(bx, by, B);
    if (captured < 0) {
      this.showFeedback('incorrect', '非法落子（自杀或打劫禁着）。');
      return;
    }
    this.recordMove(bx, by, B);
    this.logEntry({
      type: 'move',
      color: B,
      main: `黑手动 (${bx},${by})`,
      step: this.moveCounter,
      tag: '用户', tagClass: 'tag-probe',
      meta: `用户手动落子`,
    });
    this.autoplayColor = W;
    this.hideFeedback();

    this.renderer.lastMove = [bx, by, 'black'];
    this.renderer.ghostStone = null;
    this.renderBoard();

    if (this.checkPostMoveTerminal()) return;

    this.waitingForAI = true;
    document.getElementById('move-text').textContent = '白棋思考中...';
    setTimeout(() => this.computeReply(W), 50);
  }

  // 走完一手后的终局检测；返回 true 表示已终局
  checkPostMoveTerminal() {
    const [tx, ty] = this.targetInfo.targetCoord;
    if (this.board.get(tx, ty) !== this.targetInfo.defenderColor) {
      this.showTerminal('TARGET_CAPTURED');
      return true;
    }
    const tgt = getTargetGroup(this.board, this.targetInfo.targetCoord);
    if (tgt) {
      const eyes = countGroupRealEyes(this.board, tgt);
      if (eyes >= 2) {
        this.showTerminal('TARGET_ALIVE');
        return true;
      }
    }
    return false;
  }

  // 统一的一方落子：跑 df-pn 再应用结果
  // 只在棋盘实际终局（提子 / 双眼）或真无合法着法时停止
  computeReply(color) {
    const r = solveDfpn(this.board, this.regionMask, this.targetInfo, color, SOLVE_OPTIONS);
    this.displaySolveMeta(r);

    if (!r.move) {
      // 真正无合法着法：判定当前方直接输
      this.waitingForAI = false;
      this.stopAutoplay();
      if (color === this.targetInfo.attackerColor) {
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
      // 防方确定安全（pass 也必胜）→ 已活，停止
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('correct', '防方已安全，无需落子（pass）。');
      this.logMoveDecision(color, r);
      this.logTerminal('TARGET_ALIVE');
      document.getElementById('move-text').textContent = '对局结束';
      return;
    }

    const cap = this.board.play(r.move.x, r.move.y, color);
    if (cap < 0) {
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('incorrect', `落子(${r.move.x},${r.move.y})非法。`);
      return;
    }
    this.recordMove(r.move.x, r.move.y, color);
    this.logMoveDecision(color, r);

    this.renderer.lastMove = [r.move.x, r.move.y, color === B ? 'black' : 'white'];
    this.renderBoard();
    const certaintyTag = r.move.certain ? '必胜' : (r.result === 'UNPROVEN' ? '试探' : '顽抗');
    console.log(`[app] ${color === B ? '黑' : '白'}棋落子: (${r.move.x}, ${r.move.y}) [${certaintyTag}]`);

    this.waitingForAI = false;
    this.autoplayColor = -color;

    // 棋盘实际终局检查
    if (this.checkPostMoveTerminal()) return;

    document.getElementById('move-text').textContent =
      '轮到' + (this.autoplayColor === B ? '黑' : '白') + '棋';
  }

  displaySolveMeta(r) {
    let label = '';
    if (r.result === 'ATTACKER_WINS') label = '攻方必胜';
    else if (r.result === 'DEFENDER_WINS') label = '防方必胜';
    else label = '未证明';
    const meta = `${label} · ${r.nodes.toLocaleString()} 节点 · ${(r.elapsedMs / 1000).toFixed(1)}s`;
    this.updateTargetLabel();
    const el = document.getElementById('classify-label');
    el.innerHTML += `<div style="margin-top:0.25rem;font-weight:400;color:var(--jade-dark);font-size:0.78rem;">上一手：${meta}</div>`;
  }

  // ===== 手动点选目标 =====

  enterPickTargetMode() {
    if (this.mode !== 'solve') return;
    if (this.autoplayTimer) this.stopAutoplay();
    if (this.waitingForAI) return;
    this.mode = 'pick-target';
    this.canvas.style.cursor = 'crosshair';
    document.getElementById('move-text').textContent = '点击任意棋子设为目标';
    this.showFeedback('correct', '请点击你想作为目标的棋子（黑子=黑方防守，白子=白方防守）。点击空点取消。');
  }

  exitPickTargetMode() {
    this.mode = 'solve';
    this.canvas.style.cursor = '';
    this.hideFeedback();
    document.getElementById('move-text').textContent = this.targetInfo ? '轮到黑棋' : '未选目标';
  }

  onPickTargetClick(bx, by) {
    const stone = this.board.get(bx, by);
    if (stone === E) {
      // 点空点 → 取消
      this.exitPickTargetMode();
      return;
    }

    const result = makeTargetFromStone(this.board, this.regionMask, bx, by);
    if (result.error) {
      this.showFeedback('incorrect', result.error);
      return;
    }

    // 目标切换：恢复布局快照，清空棋局进度
    if (this.initialSnapshot) {
      this.board = this.initialSnapshot.board.clone();
      this.regionMask = cloneMask(this.initialSnapshot.regionMask);
    }
    this.targetInfo = result;
    this.renderer.targetCoord = result.targetCoord;
    this.moveHistory = [];
    this.moveCounter = 0;
    this.renderer.moveHistory = this.moveHistory;
    this.renderer.lastMove = null;
    this.autoplayColor = B;

    // 重新记录快照（目标已变，但棋盘回到初始）
    this.initialSnapshot = {
      board: this.board.clone(),
      regionMask: cloneMask(this.regionMask),
    };

    this.updateTargetLabel();
    this.decisionLog = [];
    this.logTargetDecision();
    this.renderBoard();
    this.exitPickTargetMode();
  }

  // ===== 自动对弈（最优解）=====

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
    this.autoplayTimer = setTimeout(() => {
      if (!this.autoplayTimer) return;
      this.autoplayStep();
      if (this.autoplayTimer) this.scheduleAutoplayStep(2000);
    }, delayMs);
  }

  autoplayStep() {
    if (this.waitingForAI) return;
    if (this.checkPostMoveTerminal()) {
      this.stopAutoplay();
      return;
    }

    const color = this.autoplayColor;
    document.getElementById('move-text').textContent =
      (color === B ? '黑' : '白') + '棋思考中...';
    this.waitingForAI = true;
    // 让 UI 立即刷新再搜索
    setTimeout(() => this.computeReply(color), 20);
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
