// 前端棋盘 — 极简数据载体，无规则
const B = 1, W = -1, E = 0, BOARD_SIZE = 13;

class ClientBoard {
  constructor(size = BOARD_SIZE) {
    this.size = size;
    this.grid = new Int8Array(size * size);
    this.lastCapture = -1;
  }
  get(x, y) { return this.grid[y * this.size + x]; }
  set(x, y, v) { this.grid[y * this.size + x] = v; }
  replaceFromArray(arr, lc = -1) {
    for (let i = 0; i < this.grid.length; i++) this.grid[i] = arr[i];
    this.lastCapture = lc;
  }
  toArray() { return Array.from(this.grid); }
  count(color) {
    let n = 0;
    for (let i = 0; i < this.grid.length; i++) if (this.grid[i] === color) n++;
    return n;
  }
  adj(x, y) {
    const a = [];
    if (x > 0) a.push([x-1, y]);
    if (x < this.size-1) a.push([x+1, y]);
    if (y > 0) a.push([x, y-1]);
    if (y < this.size-1) a.push([x, y+1]);
    return a;
  }
}
