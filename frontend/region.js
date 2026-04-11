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

// 默认区域：x ∈ [0,3], y ∈ [3,7]（20 格，包含 y=3 便于 B 从上方紧气）
function createDefaultMask(size = BOARD_SIZE) {
  const m = createEmptyMask(size);
  for (let x = 0; x <= 3; x++) {
    for (let y = 3; y <= 7; y++) {
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
