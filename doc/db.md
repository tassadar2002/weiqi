# TT 存储优化历程

## 背景

预处理系统（df-pn 穷举）产生大量 TT（Transposition Table）条目，需要持久化到磁盘供后续查表求解。存储格式经历了三次演进。

## 演进过程

### 阶段 1：SQLite + TEXT key

**格式**：SQLite 数据库，tt 表 `(key TEXT, pn INTEGER, dn INTEGER)`

| 字段 | 格式 | 大小 |
|------|------|------|
| key | `"14069456218826250379\|1\|-1"` TEXT | ~25 字节 |
| pn | SQLite INTEGER（变长） | 1-8 字节 |
| dn | SQLite INTEGER（变长） | 1-8 字节 |
| SQLite 行开销 | B-tree 指针、cell header、page padding | ~58 字节 |

**每条 TT：~113.6 字节**

**问题**：
- key 是 175 字符字符串（board.hash() 拼接），dict 哈希慢
- SQLite 行开销占 77%
- PyPy 的 sqlite3 实现有 "cannot commit - SQL statements in progress" 兼容性问题

### 阶段 2：SQLite + BLOB key

**优化**：引入 Zobrist 增量哈希，TT key 改为 `(zh, turn, lc)` 元组。SQLite 存储改 BLOB。

| 字段 | 格式 | 大小 |
|------|------|------|
| key | BLOB `pack(">QbB", zh, turn, lc)` | 10 字节 |
| pn | SQLite INTEGER | 1-8 字节 |
| dn | SQLite INTEGER | 1-8 字节 |
| SQLite 行开销 | 同上 | ~58 字节 |

**每条 TT：~75.7 字节（-33%）**

**改动**：
- `_pack_key`: `(zh, turn, lc)` → 10 字节 BLOB（zh uint64 8B + turn int8 1B + lc uint8 1B）
- `_unpack_key`: 反向解码
- `load_tt_from_sqlite`: 加载时 BLOB → tuple

### 阶段 3：纯二进制文件（当前）

**优化**：完全弃用 SQLite，改为定长记录的二进制文件。

```
文件格式 (.bin):
┌────────────────────────────┐
│ Header (20 字节)           │
│  magic:   4B "WQ3C"       │
│  version: 1B              │
│  status:  1B (0=running, 1=done) │
│  result:  1B (0=UNPROVEN, 1=ATK_WINS, 2=DEF_WINS) │
│  padding: 1B              │
│  count:   4B uint32       │
│  root_pn: 4B uint32       │
│  root_dn: 4B uint32       │
├────────────────────────────┤
│ Record (12 字节, 按 key 排序) │
│  zh:   8B uint64          │
│  turn: 1B uint8 (-1→0, 1→2) │
│  lc:   1B uint8 (-1→0, 0~168→1~169) │
│  pn:   1B uint8 (255=INF) │
│  dn:   1B uint8 (255=INF) │
├────────────────────────────┤
│ Record ...                 │
└────────────────────────────┘
```

**每条 TT：12 字节（-84%）**

## 关键设计决策

### pn/dn 用 1 字节

预处理完全穷举后，pn/dn 几乎全部收敛到 0（已证明）或 INF（不可能）。实测值分布：

| 值 | 占比 |
|---|---|
| pn=0 | 69.8% |
| pn=1~16 | 30.2% |
| dn=10^9 (INF) | 69.8% |
| dn=1~9 | 30.2% |

编码：0~254 原值，255 = DFPN_INF(10^9)。中途停止时 >254 的值截断为 254，不影响必胜着识别（pn=0/dn=0 不受影响）。

### key 全 unsigned 编码

二分查找直接比较 bytes，要求字节序 = 数值序。signed 字段需要重编码：

| 字段 | 原始值 | 编码 | 保序原因 |
|------|--------|------|---------|
| zh | uint64 | 直接存 | 已是 unsigned |
| turn | -1 或 1 | -1→0, 1→2 | 0 < 2，保序 |
| lc (last_capture) | -1 或 0~168 | -1→0, 0~168→1~169 | 0 < 1 < ... < 169，保序 |

大端序（`>`）保证高字节在前，bytes 字典序 = 数值序。

### 记录有序

主 .bin 文件中 records 严格按 key 排序。这使得：
- mmap + 二分查找 O(log N)，不需加载全量到内存
- 20 亿条仅需 31 次比较，每次触发一次 4KB 页加载（OS 缓存命中时接近 0 延迟）

### 增量写入 + 最终排序

Worker 运行中增量 append（崩溃安全，允许重复 key）。Worker 结束时用内存中的 `solver.tt`（天然去重）排序重写为有序 .bin。

### k-way 归并合并

多个 worker 的有序 .bin 用 `heapq.merge` 流式归并写入主 .bin。内存仅需 k 个缓冲区（k = worker 数），与总数据量无关。各 worker 负责不同根候选，key 天然不重叠。

## 查询方式：BinStore (mmap + 二分查找)

```python
class BinStore:
    def __init__(self, path):
        self.mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    
    def lookup(self, key):
        target = _pack_key(key)  # 10 bytes
        # 二分查找 12 字节定长记录
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            off = 20 + mid * 12
            rec_key = self.mm[off:off+10]
            if rec_key < target: lo = mid + 1
            elif rec_key > target: hi = mid
            else: return (pn, dn)
        return None
```

- 20 亿条：31 次比较 × ~100μs（SSD）= ~3ms（最坏）
- 实际多数页被 OS 缓存，远快于此
- 内存占用 ~0（仅虚拟地址映射，物理内存按需加载 4KB 页）

## 体积对比

| 条目数 | SQLite TEXT | SQLite BLOB | 二进制 |
|--------|-----------|------------|--------|
| 649 | 72 KB | 48 KB | **7.8 KB** |
| 10 万 | ~11.4 MB | ~7.6 MB | **1.2 MB** |
| 100 万 | ~114 MB | ~76 MB | **11.4 MB** |
| 3000 万 | ~3.4 GB | ~2.3 GB | **343 MB** |
| 20 亿 | ~227 GB | ~151 GB | **22.4 GB** |

**SQLite TEXT → 二进制：~9.5x 压缩**
**SQLite BLOB → 二进制：~6.3x 压缩**

## 遇到的问题

### 1. PyPy sqlite3 兼容性

**问题**：PyPy 的 `_sqlite3` 模块中，`db.execute()` 隐式创建的 cursor 不会自动关闭，导致后续 `db.commit()` 报 "cannot commit transaction - SQL statements in progress"。CPython 下不触发。

**修复**：所有 sqlite3 操作改为显式 `cursor()` → `execute()` → `close()`。最终弃用 SQLite 后此问题不再存在。

### 2. board.grid 直接赋值后 zh 未同步

**问题**：多处代码 `board.grid = list(data)` 绕过 `board.set()`，导致 Zobrist hash (zh) 与 grid 不一致。预处理产生的 TT key 与查表时的 key 不匹配。

**修复**：新增 `Board.rebuild_zh()` 方法，在直接修改 grid 后调用。修复了 `server._board_from`、`precompute._worker_solve`、coordinator 等 4 处。

### 3. signed 字段的字节序问题

**问题**：`turn` 编码为 signed int8（`b` 格式），-1 在 bytes 中是 0xFF，比 1（0x01）大。二分查找比较 bytes 时 -1 排在 1 后面，但 Python tuple 排序中 -1 < 1。`lc = -1` 编码为 255 也有同样问题。导致 Worker 输出的"排序"文件实际无序，二分查找失败。

**修复**：turn 和 lc 都改为 unsigned 编码，保证字节序 = 数值序。

## 涉及文件

- `backend/precompute/binstore.py` — 二进制格式定义、BinStore mmap 查表、k-way 合并、DiskTT
- `backend/precompute/coordinator.py` — coordinator 调度
- `backend/precompute/worker.py` — Worker 写入
- `backend/server.py` — 使用 BinStore 查表
- `backend/board.py` — Zobrist hash、rebuild_zh()
- `backend/precompute/solver.py` — tt_log 增量记录
