// ============================================================
// 棋盘渲染器（10×10）
// ============================================================

class BoardRenderer {
  constructor(canvas, boardSize) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.boardSize = boardSize;
    this.cellSize = 0;
    this.padding = 0;
    this.lastMove = null;     // [x, y, 'black'|'white']
    this.ghostStone = null;   // [x, y, 'black'|'white']
    this.showMask = false;
    this.regionMask = null;
    this.moveHistory = [];    // [{x, y, color: B|W, number}]
    this.targetCoord = null;  // [x, y]：目标群代表子坐标
    this.resize();
  }

  resize() {
    const container = this.canvas.parentElement;
    const maxW = Math.min(container.clientWidth, 600);
    const dpr = window.devicePixelRatio || 1;

    this.cellSize = Math.floor(maxW / (this.boardSize + 1));
    this.padding = this.cellSize;

    const canvasW = this.cellSize * (this.boardSize - 1) + this.padding * 2;
    const canvasH = canvasW;

    this.canvas.style.width = canvasW + 'px';
    this.canvas.style.height = canvasH + 'px';
    this.canvas.width = canvasW * dpr;
    this.canvas.height = canvasH * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.dpr = dpr;
    this.canvasW = canvasW;
    this.canvasH = canvasH;
  }

  boardToPixel(bx, by) {
    return [
      this.padding + bx * this.cellSize,
      this.padding + by * this.cellSize,
    ];
  }

  pixelToBoard(px, py) {
    const bx = Math.round((px - this.padding) / this.cellSize);
    const by = Math.round((py - this.padding) / this.cellSize);

    const [snapPx, snapPy] = this.boardToPixel(bx, by);
    const dist = Math.sqrt((px - snapPx) ** 2 + (py - snapPy) ** 2);
    if (dist > this.cellSize * 0.45) return null;
    if (bx < 0 || bx >= this.boardSize || by < 0 || by >= this.boardSize) return null;
    return [bx, by];
  }

  render(board) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvasW, this.canvasH);

    this.drawBoard(ctx);
    this.drawGrid(ctx);
    this.drawStarPoints(ctx);
    if (this.showMask && this.regionMask) {
      this.drawRegionMask(ctx, this.regionMask);
    }
    this.drawStones(ctx, board);
    this.drawTargetHighlight(ctx, board);
    this.drawMoveNumbers(ctx, board);

    if (this.ghostStone) {
      this.drawStone(ctx, this.ghostStone[0], this.ghostStone[1], this.ghostStone[2], 0.35);
    }
    if (this.lastMove && !this.hasNumberAt(this.lastMove[0], this.lastMove[1])) {
      this.drawLastMoveMarker(ctx, this.lastMove[0], this.lastMove[1], this.lastMove[2]);
    }
  }

  hasNumberAt(x, y) {
    for (const m of this.moveHistory) {
      if (m.x === x && m.y === y) return true;
    }
    return false;
  }

  // 高亮目标群：描红环 + 右上角三角标记
  drawTargetHighlight(ctx, board) {
    if (!this.targetCoord) return;
    const [tx, ty] = this.targetCoord;
    const color = board.get(tx, ty);
    if (color === E) return;  // 目标已被提
    const { group } = board.groupAndLibs(tx, ty);
    if (!group || group.length === 0) return;

    ctx.save();
    const r = this.cellSize * 0.44;
    // 半透明红色光晕 + 实线红环
    for (const [x, y] of group) {
      const [px, py] = this.boardToPixel(x, y);
      // 光晕
      const grad = ctx.createRadialGradient(px, py, r * 0.8, px, py, r * 1.5);
      grad.addColorStop(0, 'rgba(199, 62, 58, 0.35)');
      grad.addColorStop(1, 'rgba(199, 62, 58, 0)');
      ctx.fillStyle = grad;
      ctx.fillRect(px - r * 1.5, py - r * 1.5, r * 3, r * 3);
      // 实线红环
      ctx.beginPath();
      ctx.arc(px, py, r + 1.5, 0, Math.PI * 2);
      ctx.strokeStyle = '#c73e3a';
      ctx.lineWidth = 2.2;
      ctx.stroke();
    }
    // 代表子额外加一个三角标记（在右上角外侧，避开序号）
    const [rx, ry] = this.boardToPixel(tx, ty);
    const triSize = this.cellSize * 0.18;
    const tx0 = rx + r * 0.85;
    const ty0 = ry - r * 0.85;
    ctx.beginPath();
    ctx.moveTo(tx0, ty0 - triSize);
    ctx.lineTo(tx0 + triSize * 0.9, ty0 + triSize * 0.6);
    ctx.lineTo(tx0 - triSize * 0.9, ty0 + triSize * 0.6);
    ctx.closePath();
    ctx.fillStyle = '#c73e3a';
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  // 在历史棋子上绘制序号；若棋子已被提则跳过
  drawMoveNumbers(ctx, board) {
    if (!this.moveHistory || this.moveHistory.length === 0) return;
    ctx.save();
    const fontSize = Math.max(12, Math.floor(this.cellSize * 0.48));
    ctx.font = `900 ${fontSize}px system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = Math.max(2, fontSize * 0.18);
    for (const m of this.moveHistory) {
      const current = board.get(m.x, m.y);
      if (current !== m.color) continue;
      const [px, py] = this.boardToPixel(m.x, m.y);
      const text = String(m.number);
      if (m.color === B) {
        // 黑子上：白字 + 深色描边
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.85)';
        ctx.strokeText(text, px, py + 1);
        ctx.fillStyle = '#ffffff';
      } else {
        // 白子上：黑字 + 浅色描边
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.85)';
        ctx.strokeText(text, px, py + 1);
        ctx.fillStyle = '#000000';
      }
      ctx.fillText(text, px, py + 1);
    }
    ctx.restore();
  }

  drawBoard(ctx) {
    const grad = ctx.createLinearGradient(0, 0, this.canvasW, this.canvasH);
    grad.addColorStop(0, '#e2be6a');
    grad.addColorStop(0.3, '#d4ad55');
    grad.addColorStop(0.6, '#dbb85e');
    grad.addColorStop(1, '#cfa848');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, this.canvasW, this.canvasH);

    ctx.save();
    ctx.globalAlpha = 0.06;
    for (let i = 0; i < this.canvasH; i += 3) {
      ctx.beginPath();
      ctx.moveTo(0, i + Math.sin(i * 0.05) * 2);
      ctx.lineTo(this.canvasW, i + Math.sin(i * 0.05 + 1) * 2);
      ctx.strokeStyle = '#8B6914';
      ctx.lineWidth = 0.5;
      ctx.stroke();
    }
    ctx.restore();
  }

  drawGrid(ctx) {
    const n = this.boardSize;
    ctx.strokeStyle = 'rgba(40, 30, 10, 0.55)';
    ctx.lineWidth = 0.8;

    for (let i = 0; i < n; i++) {
      const [px, py1] = this.boardToPixel(i, 0);
      const [, py2] = this.boardToPixel(i, n - 1);
      ctx.beginPath(); ctx.moveTo(px, py1); ctx.lineTo(px, py2); ctx.stroke();
    }
    for (let j = 0; j < n; j++) {
      const [px1, py] = this.boardToPixel(0, j);
      const [px2] = this.boardToPixel(n - 1, j);
      ctx.beginPath(); ctx.moveTo(px1, py); ctx.lineTo(px2, py); ctx.stroke();
    }

    ctx.lineWidth = 1.5;
    const [lx, ty] = this.boardToPixel(0, 0);
    const [rx, by] = this.boardToPixel(n - 1, n - 1);
    ctx.strokeRect(lx, ty, rx - lx, by - ty);
  }

  drawStarPoints(ctx) {
    // 10×10 星位
    const stars = [[2, 2], [2, 7], [7, 2], [7, 7], [4, 4]];
    ctx.fillStyle = 'rgba(40, 30, 10, 0.7)';
    for (const [sx, sy] of stars) {
      const [px, py] = this.boardToPixel(sx, sy);
      ctx.beginPath();
      ctx.arc(px, py, this.cellSize * 0.1, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // 在非落子点上绘制半透明深色覆盖层
  drawRegionMask(ctx, mask) {
    const n = this.boardSize;
    ctx.save();
    ctx.fillStyle = 'rgba(30, 20, 5, 0.32)';
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if (mask[y * n + x]) continue;
        const [px, py] = this.boardToPixel(x, y);
        const half = this.cellSize * 0.48;
        ctx.fillRect(px - half, py - half, half * 2, half * 2);
      }
    }
    // 在可落子点上描绿点，醒目提示
    ctx.fillStyle = 'rgba(91, 140, 111, 0.35)';
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if (!mask[y * n + x]) continue;
        const [px, py] = this.boardToPixel(x, y);
        ctx.beginPath();
        ctx.arc(px, py, this.cellSize * 0.08, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  drawStones(ctx, board) {
    for (let y = 0; y < board.size; y++) {
      for (let x = 0; x < board.size; x++) {
        const c = board.get(x, y);
        if (c === B) this.drawStone(ctx, x, y, 'black', 1);
        else if (c === W) this.drawStone(ctx, x, y, 'white', 1);
      }
    }
  }

  drawStone(ctx, bx, by, color, alpha = 1) {
    const [px, py] = this.boardToPixel(bx, by);
    const r = this.cellSize * 0.44;

    ctx.save();
    ctx.globalAlpha = alpha;

    if (alpha > 0.5) {
      ctx.beginPath();
      ctx.arc(px + 1.5, py + 1.5, r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(0,0,0,0.2)';
      ctx.fill();
    }

    if (color === 'black') {
      const grad = ctx.createRadialGradient(px - r * 0.3, py - r * 0.3, r * 0.1, px, py, r);
      grad.addColorStop(0, '#555');
      grad.addColorStop(0.6, '#222');
      grad.addColorStop(1, '#111');
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(px - r * 0.25, py - r * 0.25, r * 0.18, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,255,255,0.15)';
      ctx.fill();
    } else {
      const grad = ctx.createRadialGradient(px - r * 0.3, py - r * 0.3, r * 0.1, px, py, r);
      grad.addColorStop(0, '#fff');
      grad.addColorStop(0.5, '#f5f2ea');
      grad.addColorStop(1, '#ddd8cc');
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.strokeStyle = 'rgba(120,110,100,0.3)';
      ctx.lineWidth = 0.5;
      ctx.stroke();
    }

    ctx.restore();
  }

  drawLastMoveMarker(ctx, bx, by, color) {
    const [px, py] = this.boardToPixel(bx, by);
    const r = this.cellSize * 0.12;
    ctx.beginPath();
    ctx.arc(px, py, r, 0, Math.PI * 2);
    ctx.fillStyle = color === 'black' ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.5)';
    ctx.fill();
  }
}
