// ============================================================
// 围棋习题集 · 前端控制器
// 两视图 SPA：列表视图 ↔ 详情视图
// 两阶段：设置阶段（编辑） / 解题阶段（练习）
// ============================================================

class App {
  constructor() {
    this.board = new ClientBoard(BOARD_SIZE);
    this.regionMask = createEmptyMask(BOARD_SIZE);
    this.renderer = null;
    // 当前阶段: 'setup' | 'solve'
    this.phase = null;
    // 设置阶段模式: 'layout' | 'region' | 'pick-target'
    // 解题阶段模式: 'play'
    this.mode = null;
    this.placementColor = 'black';
    this.problemId = null;
    this.problemData = null;
    this.targetInfo = null;
    this.killTargets = [];
    this.defendTargets = [];
    this.waitingForAI = false;
    this.autoplayTimer = null;
    this.autoplayColor = B;
    this.moveHistory = [];
    this.moveCounter = 0;
    this.decisionLog = [];
    this.precomputeJobId = null;
    // 解题前保存的初始棋盘（用于重置）
    this.initialBoard = null;
    this.initialLastCapture = -1;
    this.bindGlobalEvents();
    this.showListView();
  }

  bindGlobalEvents() {
    document.getElementById('btn-new-problem').addEventListener('click', () => this.createProblem());
    document.getElementById('btn-back-list').addEventListener('click', () => this.showListView());
    document.getElementById('btn-save').addEventListener('click', () => this.saveProblem());
    document.getElementById('btn-delete').addEventListener('click', () => this.deleteProblem());
    // 设置阶段 — 布局
    document.getElementById('sel-black').addEventListener('click', () => this.setPlacement('black'));
    document.getElementById('sel-white').addEventListener('click', () => this.setPlacement('white'));
    document.getElementById('sel-erase').addEventListener('click', () => this.setPlacement('erase'));
    document.getElementById('btn-to-region').addEventListener('click', () => this.enterRegionMode());
    // 设置阶段 — 区域
    document.getElementById('btn-clear-region').addEventListener('click', () => { this.regionMask = createEmptyMask(); this.syncMask(); this.render(); });
    document.getElementById('btn-all-region').addEventListener('click', () => { this.regionMask = createFullMask(); this.syncMask(); this.render(); });
    document.getElementById('btn-back-layout').addEventListener('click', () => this.enterLayoutMode());
    document.getElementById('btn-to-target').addEventListener('click', () => this.enterPickTarget());
    // 设置阶段 — 目标
    document.getElementById('btn-confirm-target').addEventListener('click', () => this.confirmTarget());
    document.getElementById('btn-back-region').addEventListener('click', () => this.enterRegionMode());
    // 解题阶段
    document.getElementById('btn-autoplay').addEventListener('click', () => this.toggleAutoplay());
    document.getElementById('btn-reset-solve').addEventListener('click', () => this.resetSolve());
  }

  // ============================================================
  // 列表视图
  // ============================================================

  async showListView() {
    this.stopAutoplay();
    this.stopPrecomputePoll();
    document.getElementById('list-view').classList.remove('hidden');
    document.getElementById('detail-view').classList.add('hidden');
    const r = await API.listProblems();
    const list = document.getElementById('problem-list');
    if (!r.problems || r.problems.length === 0) {
      list.innerHTML = '<div class="empty-hint">还没有习题，点击"+ 新建习题"开始</div>';
      return;
    }
    list.innerHTML = r.problems.map(p => {
      const st = p.precompute_status === 'done' ? '<span class="status-done">已预处理</span>'
        : p.precompute_status === 'running' ? '<span class="status-running">预处理中</span>'
        : '<span class="status-none">未预处理</span>';
      const hasTarget = (p.kill_count || 0) + (p.defend_count || 0) > 0;
      const canPlay = hasTarget && p.precompute_status === 'done';
      const playTip = !hasTarget ? '请先设定目标' : (p.precompute_status !== 'done' ? '请先完成预处理' : '');
      return `<div class="problem-card" data-id="${p.id}">
        <div class="problem-card-body">
          <div class="problem-name">${this.esc(p.name)}</div>
          <div class="problem-meta">${p.black_count}黑/${p.white_count}白 · 区域${p.region_count}格 · ${st}</div>
        </div>
        <div class="problem-actions">
          <button class="card-btn card-btn-edit" data-id="${p.id}">编辑</button>
          <button class="card-btn card-btn-play${canPlay ? '' : ' disabled'}" data-id="${p.id}" ${canPlay ? '' : 'disabled'} ${playTip ? `title="${playTip}"` : ''}>练习</button>
        </div>
      </div>`;
    }).join('');
    // 绑定按钮事件
    list.querySelectorAll('.card-btn-edit').forEach(el => {
      el.addEventListener('click', e => { e.stopPropagation(); this.editProblem(el.dataset.id); });
    });
    list.querySelectorAll('.card-btn-play:not(.disabled)').forEach(el => {
      el.addEventListener('click', e => { e.stopPropagation(); this.solveProblem(el.dataset.id); });
    });
  }

  async createProblem() {
    const r = await API.createProblem('未命名习题');
    await this.editProblem(r.id);
  }

  // ============================================================
  // 进入详情视图（通用加载）
  // ============================================================

  async _loadProblem(id) {
    const p = await API.getProblem(id);
    if (!p) { alert('习题不存在'); return null; }
    this.problemId = id;
    this.problemData = p;
    this.board.replaceFromArray(p.board_grid, -1);
    this.regionMask = new Uint8Array(p.region_mask);

    // 从 DB 恢复目标
    this.killTargets = [];
    for (const c of (p.kill_targets || [])) {
      const info = await API.validateTarget(this.board.toArray(), Array.from(this.regionMask), c[0], c[1]);
      if (!info.error) this.killTargets.push(info);
    }
    this.defendTargets = [];
    for (const c of (p.defend_targets || [])) {
      const info = await API.validateTarget(this.board.toArray(), Array.from(this.regionMask), c[0], c[1]);
      if (!info.error) this.defendTargets.push(info);
    }
    if (this.killTargets.length > 0 || this.defendTargets.length > 0) {
      this.targetInfo = {
        attacker_color: B,
        kill_targets_coords: this.killTargets.map(t => t.coord),
        defend_targets_coords: this.defendTargets.map(t => t.coord),
      };
    } else {
      this.targetInfo = null;
    }

    document.getElementById('problem-title').value = p.name;
    document.getElementById('list-view').classList.add('hidden');
    document.getElementById('detail-view').classList.remove('hidden');
    this._ensureRenderer();
    this.moveHistory = [];
    this.moveCounter = 0;
    this.decisionLog = [];
    this.precomputeJobId = p.precompute_job_id || null;
    return p;
  }

  _ensureRenderer() {
    const canvas = document.getElementById('board-canvas');
    if (!this.renderer) {
      this.renderer = new BoardRenderer(canvas, BOARD_SIZE);
      canvas.addEventListener('click', e => this.onCanvasClick(e));
      canvas.addEventListener('mousemove', e => this.onCanvasMouseMove(e));
      canvas.addEventListener('mouseleave', () => { this.renderer.ghostStone = null; this.render(); });
      window.addEventListener('resize', () => { this.renderer.resize(); this.render(); });
    } else {
      this.renderer.resize();
    }
  }

  // ============================================================
  // 设置阶段（编辑）
  // ============================================================

  async editProblem(id) {
    const p = await this._loadProblem(id);
    if (!p) return;
    this.phase = 'setup';
    this._updatePhaseBadge();
    document.getElementById('btn-save').classList.remove('hidden');
    document.getElementById('btn-delete').classList.remove('hidden');
    document.getElementById('problem-title').readOnly = false;
    this.enterLayoutMode();
  }

  enterLayoutMode() {
    this.stopAutoplay();
    this.mode = 'layout';
    this.renderer.lastMove = null;
    this.renderer.ghostStone = null;
    this.renderer.moveHistory = [];
    this.renderer.showMask = false;
    this.syncTargetHighlight();
    this._showSetupCard('layout-controls');
    document.getElementById('move-dot').className = 'layout';
    document.getElementById('move-text').textContent = '布局模式';
    this.hideFeedback();
    this.render();
  }

  enterRegionMode() {
    if (this.board.count(B) === 0 && this.board.count(W) === 0) {
      this.showFeedback('incorrect', '请先摆放棋子'); return;
    }
    this.mode = 'region';
    this.renderer.showMask = true;
    this.syncMask();
    this._showSetupCard('region-controls');
    document.getElementById('move-dot').className = 'layout';
    document.getElementById('move-text').textContent = '点击设置落子点';
    this.hideFeedback();
    this.render();
  }

  enterPickTarget() {
    if (maskCellCount(this.regionMask) === 0) {
      this.showFeedback('incorrect', '落子区域不能为空'); return;
    }
    this.mode = 'pick-target';
    this.renderer.showMask = true;
    this.syncMask();
    this.syncTargetHighlight();
    this._showSetupCard('target-controls');
    this.updateTargetLabel();
    this.canvas_cursor('crosshair');
    document.getElementById('move-dot').className = 'layout';
    document.getElementById('move-text').textContent = '点白子=杀(红)，点黑子=守(蓝)';
    this.hideFeedback();
    this.render();
  }

  _showSetupCard(id) {
    ['layout-controls', 'region-controls', 'target-controls',
     'solve-controls', 'decision-log'].forEach(
      c => document.getElementById(c).classList.add('hidden'));
    document.getElementById(id).classList.remove('hidden');
  }

  async onPickTargetClick(bx, by) {
    if (this.board.get(bx, by) === E) return;
    // 已选 → 取消
    const ki = this._findInList(this.killTargets, bx, by);
    if (ki >= 0) { this.killTargets.splice(ki, 1); this.syncTargetHighlight(); this.updateTargetLabel(); this.render(); return; }
    const di = this._findInList(this.defendTargets, bx, by);
    if (di >= 0) { this.defendTargets.splice(di, 1); this.syncTargetHighlight(); this.updateTargetLabel(); this.render(); return; }
    // 新增
    let info;
    try { info = await API.validateTarget(this.board.toArray(), Array.from(this.regionMask), bx, by); } catch(e) { this.showFeedback('incorrect', '通信失败'); return; }
    if (info.error) { this.showFeedback('incorrect', info.error); return; }
    if (info.color === W) this.killTargets.push(info);
    else this.defendTargets.push(info);
    this.syncTargetHighlight();
    this.updateTargetLabel();
    this.render();
    this.showFeedback('correct', `已添加 ${info.color===W?'杀(红)':'守(蓝)'}：(${bx},${by})`);
  }

  async confirmTarget() {
    if (this.killTargets.length === 0 && this.defendTargets.length === 0) {
      this.showFeedback('incorrect', '至少选一个目标'); return;
    }
    this.targetInfo = {
      attacker_color: B,
      kill_targets_coords: this.killTargets.map(t => t.coord),
      defend_targets_coords: this.defendTargets.map(t => t.coord),
    };
    this.canvas_cursor('');
    // 自动保存并返回列表
    await this.saveProblem();
    this.showFeedback('correct', '目标已确认，已保存');
    this.showListView();
  }

  // ============================================================
  // 解题阶段（练习）
  // ============================================================

  async solveProblem(id) {
    const p = await this._loadProblem(id);
    if (!p) return;
    if (!this.targetInfo) {
      this.showFeedback('incorrect', '请先编辑此题并设定目标');
      this.showListView();
      return;
    }
    if (p.precompute_status !== 'done') {
      this.showFeedback('incorrect', '请先完成预处理后再练习');
      this.showListView();
      return;
    }
    this.phase = 'solve';
    this._updatePhaseBadge();
    document.getElementById('btn-save').classList.add('hidden');
    document.getElementById('btn-delete').classList.add('hidden');
    document.getElementById('problem-title').readOnly = true;
    // 保存初始棋盘用于重置
    this.initialBoard = this.board.toArray();
    this.initialLastCapture = this.board.lastCapture;
    this.enterPlayMode();
  }

  enterPlayMode() {
    this.mode = 'play';
    this.autoplayColor = B;
    this.moveHistory = [];
    this.moveCounter = 0;
    this.renderer.moveHistory = [];
    this.renderer.lastMove = null;
    this.renderer.ghostStone = null;
    this.renderer.showMask = true;
    this.syncMask();
    this.syncTargetHighlight();
    this.decisionLog = [];
    this.logTarget();
    // 显示解题卡片
    ['layout-controls', 'region-controls', 'target-controls', 'precompute-controls'].forEach(
      c => document.getElementById(c).classList.add('hidden'));
    document.getElementById('solve-controls').classList.remove('hidden');
    document.getElementById('decision-log').classList.remove('hidden');
    document.getElementById('btn-autoplay').textContent = '最优解';
    this.updateSolveTargetLabel();
    document.getElementById('move-dot').className = '';
    document.getElementById('move-text').textContent = '轮到黑棋';
    this.hideFeedback();
    this.render();
  }

  resetSolve() {
    this.stopAutoplay();
    if (this.initialBoard) {
      this.board.replaceFromArray(this.initialBoard, this.initialLastCapture);
    }
    this.enterPlayMode();
  }

  updateSolveTargetLabel() {
    const el = document.getElementById('solve-target-label');
    const nk = this.killTargets.length, nd = this.defendTargets.length;
    const parts = [];
    if (nk > 0) parts.push(`<span style="color:#c73e3a">杀${nk}群</span>`);
    if (nd > 0) parts.push(`<span style="color:#3a6ec7">守${nd}群</span>`);
    let empty = 0;
    for (let i = 0; i < this.regionMask.length; i++) if (this.regionMask[i] && this.board.grid[i] === E) empty++;
    el.innerHTML = `${parts.join('·')} · 空位${empty}`;
  }

  _updatePhaseBadge() {
    const el = document.getElementById('phase-badge');
    if (this.phase === 'setup') {
      el.textContent = '设置';
      el.className = 'phase-badge phase-setup';
    } else {
      el.textContent = '练习';
      el.className = 'phase-badge phase-solve';
    }
  }

  // ============================================================
  // 保存 / 删除
  // ============================================================

  async saveProblem() {
    if (!this.problemId) return;
    const name = document.getElementById('problem-title').value || '未命名习题';
    await API.updateProblem(this.problemId, {
      name,
      board_grid: this.board.toArray(),
      region_mask: Array.from(this.regionMask),
      kill_targets: this.killTargets.map(t => t.coord),
      defend_targets: this.defendTargets.map(t => t.coord),
      attacker_color: B,
    });
    this.showFeedback('correct', '已保存');
  }

  async deleteProblem() {
    if (!this.problemId) return;
    if (!confirm('确定删除此习题？')) return;
    await API.deleteProblem(this.problemId);
    this.showListView();
  }

  // ============================================================
  // 落子 + 求解（解题阶段）
  // ============================================================

  async onPlayClick(bx, by) {
    if (this.waitingForAI || this.autoplayTimer) return;
    if (!this.regionMask[by * BOARD_SIZE + bx]) { this.showFeedback('incorrect', '不在落子区域内'); return; }
    if (this.board.get(bx, by) !== E) return;
    this.waitingForAI = true;
    const r = await API.play(this.board.toArray(), this.board.lastCapture, bx, by, B, {
      killTargets: this.killTargets.map(t => t.coord),
      defendTargets: this.defendTargets.map(t => t.coord),
    });
    this.waitingForAI = false;
    if (!r.ok) { this.showFeedback('incorrect', r.error || '非法落子'); return; }
    this.applyPlay(bx, by, B, r);
    this.logMove(B, bx, by, '用户', false);
    this.autoplayColor = W;
    if (this.checkTerminal(r)) return;
    document.getElementById('move-text').textContent = '白棋思考中...';
    this.waitingForAI = true;
    setTimeout(() => this.computeReply(W), 50);
  }

  async computeReply(color) {
    const r = await API.solve(
      this.board.toArray(), this.board.lastCapture,
      Array.from(this.regionMask), this.targetInfo, color,
      { cacheId: this.precomputeJobId, maxTimeMs: 300000, maxNodes: 50000000 },
    );
    if (!r.move) {
      this.waitingForAI = false;
      this.stopAutoplay();
      this.showFeedback('incorrect', r.result === 'UNPROVEN'
        ? `未证明（${r.nodes?.toLocaleString()}节点/${(r.elapsed_ms/1000).toFixed(1)}s）` : '无着可下');
      document.getElementById('move-text').textContent = '对局结束';
      return;
    }
    const pr = await API.play(
      this.board.toArray(), this.board.lastCapture,
      r.move.x, r.move.y, color,
      { killTargets: this.killTargets.map(t => t.coord), defendTargets: this.defendTargets.map(t => t.coord) },
    );
    if (!pr.ok) {
      this.waitingForAI = false; this.stopAutoplay();
      this.showFeedback('incorrect', `落子(${r.move.x},${r.move.y})非法：${pr.error}`); return;
    }
    this.applyPlay(r.move.x, r.move.y, color, pr);
    this.logMove(color, r.move.x, r.move.y, r.move.certain ? '必胜' : '顽抗', r.move.certain);
    this.waitingForAI = false;
    this.autoplayColor = -color;
    if (this.checkTerminal(pr)) return;
    document.getElementById('move-text').textContent = `轮到${this.autoplayColor===B?'黑':'白'}棋`;
  }

  applyPlay(x, y, color, r) {
    this.board.replaceFromArray(r.new_board, r.last_capture);
    this.recordMove(x, y, color);
    this.renderer.lastMove = [x, y, color === B ? 'black' : 'white'];
    if (r.multi_status) {
      (r.multi_status.kill_statuses || []).forEach((s, i) => {
        if (s && s.group && this.killTargets[i]) this.killTargets[i].group = s.group;
      });
      (r.multi_status.defend_statuses || []).forEach((s, i) => {
        if (s && s.group && this.defendTargets[i]) this.defendTargets[i].group = s.group;
      });
      this.syncTargetHighlight();
    }
    this.hideFeedback();
    this.render();
  }

  checkTerminal(r) {
    if (!r.multi_status) return false;
    const t = r.multi_status.terminal;
    if (t === 'ATTACKER_WINS') {
      this.showFeedback('correct', '所有杀目标被提子，攻方胜！');
      this.logTerminal('攻方胜'); this.stopAutoplay(); return true;
    }
    if (t === 'DEFENDER_WINS') {
      const msg = r.multi_status.defend_statuses?.some(s => s?.captured)
        ? '守目标被提，攻方保护失败' : '杀目标做活，防方胜';
      this.showFeedback('incorrect', msg);
      this.logTerminal('防方胜'); this.stopAutoplay(); return true;
    }
    return false;
  }

  recordMove(x, y, color) {
    for (let i = this.moveHistory.length - 1; i >= 0; i--) {
      const m = this.moveHistory[i];
      if ((this.board.get(m.x, m.y) !== m.color) || (m.x === x && m.y === y))
        this.moveHistory.splice(i, 1);
    }
    this.moveCounter++;
    this.moveHistory.push({x, y, color, number: this.moveCounter});
    this.renderer.moveHistory = this.moveHistory;
  }

  // ============================================================
  // 自动对弈
  // ============================================================

  toggleAutoplay() {
    if (this.autoplayTimer) { this.stopAutoplay(); return; }
    document.getElementById('btn-autoplay').textContent = '停止';
    this.autoplayTimer = true;
    this.scheduleStep(0);
  }

  stopAutoplay() {
    if (typeof this.autoplayTimer === 'number') clearTimeout(this.autoplayTimer);
    this.autoplayTimer = null;
    const btn = document.getElementById('btn-autoplay');
    if (btn) btn.textContent = '最优解';
  }

  scheduleStep(ms) {
    if (!this.autoplayTimer) return;
    this.autoplayTimer = setTimeout(async () => {
      if (!this.autoplayTimer) return;
      const color = this.autoplayColor;
      document.getElementById('move-text').textContent = `${color===B?'黑':'白'}棋思考中...`;
      this.waitingForAI = true;
      await this.computeReply(color);
      if (this.autoplayTimer) this.scheduleStep(2000);
    }, ms);
  }

  // ============================================================
  // 日志
  // ============================================================

  logTarget() {
    const kd = this.killTargets.map(t => `杀(${t.coord[0]},${t.coord[1]})`).join(' ');
    const dd = this.defendTargets.map(t => `守(${t.coord[0]},${t.coord[1]})`).join(' ');
    this.decisionLog.push({type:'target', main: `目标：${kd} ${dd}`});
    this.renderLog();
  }

  logMove(color, x, y, tag, certain) {
    this.decisionLog.push({
      type: 'move', color,
      main: `#${this.moveCounter} ${color===B?'黑':'白'} (${x},${y})`,
      tag, tagClass: certain ? 'tag-win' : 'tag-resist',
    });
    this.renderLog();
  }

  logTerminal(text) {
    this.decisionLog.push({type: 'terminal', main: text});
    this.renderLog();
  }

  renderLog() {
    const el = document.getElementById('log-list');
    if (!el) return;
    el.innerHTML = this.decisionLog.map(e => {
      if (e.type === 'target')
        return `<div class="log-entry entry-target"><div class="log-main">${this.esc(e.main)}</div></div>`;
      if (e.type === 'move') {
        const cls = e.color === B ? 'entry-move-black' : 'entry-move-white';
        const tag = e.tag ? `<span class="log-tag ${e.tagClass}">${e.tag}</span>` : '';
        return `<div class="log-entry ${cls}"><div class="log-main">${this.esc(e.main)}${tag}</div></div>`;
      }
      if (e.type === 'terminal')
        return `<div class="log-entry entry-terminal"><div class="log-main">${this.esc(e.main)}</div></div>`;
      return '';
    }).join('');
    el.scrollTop = el.scrollHeight;
  }

  // ============================================================
  // Canvas 事件
  // ============================================================

  onCanvasClick(e) {
    const rect = this.renderer.canvas.getBoundingClientRect();
    const coord = this.renderer.pixelToBoard(e.clientX - rect.left, e.clientY - rect.top);
    if (!coord) return;
    const [bx, by] = coord;
    if (this.mode === 'layout') this.onLayoutClick(bx, by);
    else if (this.mode === 'region') this.onRegionClick(bx, by);
    else if (this.mode === 'pick-target') this.onPickTargetClick(bx, by);
    else if (this.mode === 'play') this.onPlayClick(bx, by);
  }

  onLayoutClick(bx, by) {
    if (this.placementColor === 'erase') { this.board.set(bx, by, E); }
    else {
      const c = this.placementColor === 'black' ? B : W;
      this.board.set(bx, by, this.board.get(bx, by) === c ? E : c);
    }
    this.render();
  }

  onRegionClick(bx, by) {
    toggleMaskCell(this.regionMask, bx, by, BOARD_SIZE);
    this.render();
  }

  onCanvasMouseMove(e) {
    if (!this.renderer) return;
    const rect = this.renderer.canvas.getBoundingClientRect();
    const coord = this.renderer.pixelToBoard(e.clientX - rect.left, e.clientY - rect.top);
    if (this.mode === 'layout' && coord && this.placementColor !== 'erase' && this.board.get(coord[0], coord[1]) === E)
      this.renderer.ghostStone = [coord[0], coord[1], this.placementColor];
    else if (this.mode === 'play' && coord && !this.waitingForAI && !this.autoplayTimer
             && this.board.get(coord[0], coord[1]) === E && this.regionMask[coord[1]*BOARD_SIZE+coord[0]])
      this.renderer.ghostStone = [coord[0], coord[1], 'black'];
    else
      this.renderer.ghostStone = null;
    this.render();
  }

  // ============================================================
  // 目标 / 工具
  // ============================================================

  _findInList(list, x, y) {
    for (let i = 0; i < list.length; i++)
      for (const [gx, gy] of (list[i].group || []))
        if (gx === x && gy === y) return i;
    return -1;
  }

  syncTargetHighlight() {
    this.renderer.killGroups = this.killTargets.map(t => ({coords: t.group, coord: t.coord}));
    this.renderer.defendGroups = this.defendTargets.map(t => ({coords: t.group, coord: t.coord}));
  }

  updateTargetLabel() {
    const el = document.getElementById('target-label');
    const nk = this.killTargets.length, nd = this.defendTargets.length;
    const regionTotal = maskCellCount(this.regionMask);
    let empty = 0;
    for (let i = 0; i < this.regionMask.length; i++) if (this.regionMask[i] && this.board.grid[i] === E) empty++;
    const stats = `区域${regionTotal}格·空位${empty}`;
    if (nk === 0 && nd === 0) { el.innerHTML = `<span style="color:var(--vermillion)">未选目标</span> · ${stats}`; return; }
    const parts = [];
    if (nk > 0) parts.push(`<span style="color:#c73e3a">杀${nk}群</span>`);
    if (nd > 0) parts.push(`<span style="color:#3a6ec7">守${nd}群</span>`);
    el.innerHTML = `${parts.join('·')} · ${stats}`;
  }

  setPlacement(color) {
    this.placementColor = color;
    document.querySelectorAll('#color-selector .tool-btn').forEach(b => b.classList.remove('active-tool'));
    const map = {black:'sel-black', white:'sel-white', erase:'sel-erase'};
    document.getElementById(map[color]).classList.add('active-tool');
  }

  syncMask() { this.renderer.regionMask = this.regionMask; this.renderer.showMask = (this.mode !== 'layout'); }
  render() { if (this.renderer) this.renderer.render(this.board); }
  canvas_cursor(c) { document.getElementById('board-canvas').style.cursor = c; }
  esc(s) { return s == null ? '' : String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]); }

  showFeedback(type, msg) {
    const el = document.getElementById('feedback');
    document.getElementById('feedback-icon').textContent = type === 'correct' ? '\u2714' : '\u2718';
    document.getElementById('feedback-message').textContent = msg;
    el.className = 'card feedback-card ' + type;
  }

  hideFeedback() {
    document.getElementById('feedback').className = 'card feedback-card hidden';
  }
}

document.addEventListener('DOMContentLoaded', () => new App());
