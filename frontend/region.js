// ============================================================
// 落子点区域掩码工具
// 1 = 可落子点；0 = 墙（不可落子）
// ============================================================

function createEmptyMask(size = BOARD_SIZE) {
  return new Uint8Array(size * size);
}

function createFullMask(size = BOARD_SIZE) {
  const m = new Uint8Array(size * size);
  m.fill(1);
  return m;
}

// 默认区域：(0,3) 单点 + x∈[0,5] y∈[4,9] 矩形 = 37 格
function createDefaultMask(size = BOARD_SIZE) {
  const m = createEmptyMask(size);
  // 单点 (0,3)
  m[3 * size + 0] = 1;
  // 矩形 x∈[0,5] y∈[4,9]
  for (let x = 0; x <= 5; x++) {
    for (let y = 4; y <= 9; y++) {
      m[y * size + x] = 1;
    }
  }
  return m;
}

function cloneMask(mask) {
  const m = new Uint8Array(mask.length);
  m.set(mask);
  return m;
}

function toggleMaskCell(mask, x, y, size = BOARD_SIZE) {
  const idx = y * size + x;
  mask[idx] = mask[idx] ? 0 : 1;
}

function maskCellCount(mask) {
  let n = 0;
  for (let i = 0; i < mask.length; i++) if (mask[i]) n++;
  return n;
}
