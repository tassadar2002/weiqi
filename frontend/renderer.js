// Canvas 棋盘渲染器 — 13×13
class BoardRenderer {
  constructor(canvas, boardSize) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.boardSize = boardSize;
    this.cellSize = 0;
    this.padding = 0;
    this.lastMove = null;
    this.ghostStone = null;
    this.showMask = false;
    this.regionMask = null;
    this.moveHistory = [];
    this.killGroups = [];
    this.defendGroups = [];
    this.resize();
  }

  resize() {
    const container = this.canvas.parentElement;
    const maxW = Math.min(container.clientWidth, 640);
    const dpr = window.devicePixelRatio || 1;
    this.cellSize = Math.floor(maxW / (this.boardSize + 1));
    this.padding = this.cellSize;
    const canvasW = this.cellSize * (this.boardSize - 1) + this.padding * 2;
    this.canvas.style.width = canvasW + 'px';
    this.canvas.style.height = canvasW + 'px';
    this.canvas.width = canvasW * dpr;
    this.canvas.height = canvasW * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.canvasW = canvasW;
  }

  boardToPixel(bx, by) {
    return [this.padding + bx * this.cellSize, this.padding + by * this.cellSize];
  }

  pixelToBoard(px, py) {
    const bx = Math.round((px - this.padding) / this.cellSize);
    const by = Math.round((py - this.padding) / this.cellSize);
    const [sx, sy] = this.boardToPixel(bx, by);
    if (Math.sqrt((px-sx)**2 + (py-sy)**2) > this.cellSize * 0.45) return null;
    if (bx < 0 || bx >= this.boardSize || by < 0 || by >= this.boardSize) return null;
    return [bx, by];
  }

  render(board) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvasW, this.canvasW);
    this._drawBoard(ctx);
    this._drawGrid(ctx);
    this._drawStarPoints(ctx);
    if (this.showMask && this.regionMask) this._drawMask(ctx);
    this._drawStones(ctx, board);
    this._drawTargetHighlight(ctx, board);
    this._drawMoveNumbers(ctx, board);
    if (this.ghostStone) this._drawStone(ctx, this.ghostStone[0], this.ghostStone[1], this.ghostStone[2], 0.35);
    if (this.lastMove && !this._hasNumber(this.lastMove[0], this.lastMove[1]))
      this._drawLastMove(ctx, this.lastMove[0], this.lastMove[1], this.lastMove[2]);
  }

  _drawBoard(ctx) {
    const grad = ctx.createLinearGradient(0, 0, this.canvasW, this.canvasW);
    grad.addColorStop(0, '#e2be6a'); grad.addColorStop(1, '#cfa848');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, this.canvasW, this.canvasW);
  }

  _drawGrid(ctx) {
    const n = this.boardSize;
    ctx.strokeStyle = 'rgba(40,30,10,0.55)'; ctx.lineWidth = 0.8;
    for (let i = 0; i < n; i++) {
      const [px, py1] = this.boardToPixel(i, 0);
      const [, py2] = this.boardToPixel(i, n-1);
      ctx.beginPath(); ctx.moveTo(px, py1); ctx.lineTo(px, py2); ctx.stroke();
    }
    for (let j = 0; j < n; j++) {
      const [px1, py] = this.boardToPixel(0, j);
      const [px2] = this.boardToPixel(n-1, j);
      ctx.beginPath(); ctx.moveTo(px1, py); ctx.lineTo(px2, py); ctx.stroke();
    }
    ctx.lineWidth = 1.5;
    const [lx, ty] = this.boardToPixel(0, 0);
    const [rx, by] = this.boardToPixel(n-1, n-1);
    ctx.strokeRect(lx, ty, rx-lx, by-ty);
  }

  _drawStarPoints(ctx) {
    // 13×13 星位
    const pts = [[3,3],[3,9],[9,3],[9,9],[6,6],[3,6],[6,3],[6,9],[9,6]];
    ctx.fillStyle = 'rgba(40,30,10,0.7)';
    for (const [sx,sy] of pts) {
      const [px,py] = this.boardToPixel(sx, sy);
      ctx.beginPath(); ctx.arc(px, py, this.cellSize*0.1, 0, Math.PI*2); ctx.fill();
    }
  }

  _drawMask(ctx) {
    const n = this.boardSize;
    ctx.save(); ctx.fillStyle = 'rgba(30,20,5,0.32)';
    for (let y = 0; y < n; y++)
      for (let x = 0; x < n; x++) {
        if (this.regionMask[y*n+x]) continue;
        const [px,py] = this.boardToPixel(x, y);
        const h = this.cellSize * 0.48;
        ctx.fillRect(px-h, py-h, h*2, h*2);
      }
    ctx.restore();
  }

  _drawStones(ctx, board) {
    for (let y = 0; y < board.size; y++)
      for (let x = 0; x < board.size; x++) {
        const c = board.get(x, y);
        if (c === B) this._drawStone(ctx, x, y, 'black', 1);
        else if (c === W) this._drawStone(ctx, x, y, 'white', 1);
      }
  }

  _drawStone(ctx, bx, by, color, alpha = 1) {
    const [px, py] = this.boardToPixel(bx, by);
    const r = this.cellSize * 0.44;
    ctx.save(); ctx.globalAlpha = alpha;
    if (alpha > 0.5) {
      ctx.beginPath(); ctx.arc(px+1.5, py+1.5, r, 0, Math.PI*2);
      ctx.fillStyle = 'rgba(0,0,0,0.2)'; ctx.fill();
    }
    if (color === 'black') {
      const g = ctx.createRadialGradient(px-r*0.3, py-r*0.3, r*0.1, px, py, r);
      g.addColorStop(0, '#555'); g.addColorStop(0.6, '#222'); g.addColorStop(1, '#111');
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI*2); ctx.fillStyle = g; ctx.fill();
    } else {
      const g = ctx.createRadialGradient(px-r*0.3, py-r*0.3, r*0.1, px, py, r);
      g.addColorStop(0, '#fff'); g.addColorStop(0.5, '#f5f2ea'); g.addColorStop(1, '#ddd8cc');
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI*2); ctx.fillStyle = g; ctx.fill();
      ctx.strokeStyle = 'rgba(120,110,100,0.3)'; ctx.lineWidth = 0.5; ctx.stroke();
    }
    ctx.restore();
  }

  _drawTargetHighlight(ctx, board) {
    const r = this.cellSize * 0.44;
    ctx.save();
    for (const g of this.killGroups)
      this._groupRing(ctx, board, g.coords, g.coord, '#c73e3a', 'rgba(199,62,58,0.30)', r);
    for (const g of this.defendGroups)
      this._groupRing(ctx, board, g.coords, g.coord, '#3a6ec7', 'rgba(58,110,199,0.30)', r);
    ctx.restore();
  }

  _groupRing(ctx, board, coords, rep, stroke, glow, r) {
    if (!coords || !coords.length) return;
    for (const [x,y] of coords) {
      if (board.get(x,y) === E) continue;
      const [px,py] = this.boardToPixel(x, y);
      const grad = ctx.createRadialGradient(px, py, r*0.8, px, py, r*1.5);
      grad.addColorStop(0, glow); grad.addColorStop(1, glow.replace(/[\d.]+\)$/, '0)'));
      ctx.fillStyle = grad; ctx.fillRect(px-r*1.5, py-r*1.5, r*3, r*3);
      ctx.beginPath(); ctx.arc(px, py, r+1.5, 0, Math.PI*2);
      ctx.strokeStyle = stroke; ctx.lineWidth = 2.2; ctx.stroke();
    }
    if (rep) {
      const [rx,ry] = this.boardToPixel(rep[0], rep[1]);
      const ts = this.cellSize * 0.18;
      const tx = rx+r*0.85, ty = ry-r*0.85;
      ctx.beginPath(); ctx.moveTo(tx, ty-ts);
      ctx.lineTo(tx+ts*0.9, ty+ts*0.6); ctx.lineTo(tx-ts*0.9, ty+ts*0.6); ctx.closePath();
      ctx.fillStyle = stroke; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.fill(); ctx.stroke();
    }
  }

  _drawMoveNumbers(ctx, board) {
    if (!this.moveHistory.length) return;
    ctx.save();
    const fs = Math.max(10, Math.floor(this.cellSize * 0.42));
    ctx.font = `900 ${fs}px system-ui, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round'; ctx.lineWidth = Math.max(2, fs * 0.18);
    for (const m of this.moveHistory) {
      if (board.get(m.x, m.y) !== m.color) continue;
      const [px, py] = this.boardToPixel(m.x, m.y);
      const t = String(m.number);
      if (m.color === B) {
        ctx.strokeStyle = 'rgba(0,0,0,0.85)'; ctx.strokeText(t, px, py+1);
        ctx.fillStyle = '#fff';
      } else {
        ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.strokeText(t, px, py+1);
        ctx.fillStyle = '#000';
      }
      ctx.fillText(t, px, py+1);
    }
    ctx.restore();
  }

  _hasNumber(x, y) {
    return this.moveHistory.some(m => m.x === x && m.y === y);
  }

  _drawLastMove(ctx, bx, by, color) {
    const [px, py] = this.boardToPixel(bx, by);
    const r = this.cellSize * 0.12;
    ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI*2);
    ctx.fillStyle = color === 'black' ? 'rgba(255,255,255,0.7)' : 'rgba(0,0,0,0.5)';
    ctx.fill();
  }
}
