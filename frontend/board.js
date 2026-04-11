// ============================================================
// 前端棋盘 — 极简版
// 仅作为渲染数据载体；所有 Go 规则、解题逻辑都在 Python 后端
// ============================================================

const B = 1;
const W = -1;
const E = 0;
const BOARD_SIZE = 10;

class ClientBoard {
  constructor(size = BOARD_SIZE) {
    this.size = size;
    this.grid = new Int8Array(size * size);
    this.lastCapture = -1;
  }

  get(x, y) { return this.grid[y * this.size + x]; }
  set(x, y, v) { this.grid[y * this.size + x] = v; }

  // 用后端返回的扁平数组替换内部状态
  replaceFromArray(arr, lastCapture = -1) {
    for (let i = 0; i < this.grid.length; i++) this.grid[i] = arr[i];
    this.lastCapture = lastCapture;
  }

  toArray() {
    return Array.from(this.grid);
  }

  count(color) {
    let n = 0;
    for (let i = 0; i < this.grid.length; i++) if (this.grid[i] === color) n++;
    return n;
  }

  // 给 renderer 用的 4 邻
  adj(x, y) {
    const a = [];
    if (x > 0) a.push([x - 1, y]);
    if (x < this.size - 1) a.push([x + 1, y]);
    if (y > 0) a.push([x, y - 1]);
    if (y < this.size - 1) a.push([x, y + 1]);
    return a;
  }

  inBounds(x, y) {
    return x >= 0 && x < this.size && y >= 0 && y < this.size;
  }
}
