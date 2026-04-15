// 落子区域掩码工具
function createEmptyMask(size = BOARD_SIZE) { return new Uint8Array(size * size); }
function createFullMask(size = BOARD_SIZE) { const m = new Uint8Array(size*size); m.fill(1); return m; }
function cloneMask(mask) { const m = new Uint8Array(mask.length); m.set(mask); return m; }
function toggleMaskCell(mask, x, y, size = BOARD_SIZE) { const i = y*size+x; mask[i] = mask[i] ? 0 : 1; }
function maskCellCount(mask) { let n=0; for (let i=0; i<mask.length; i++) if (mask[i]) n++; return n; }
