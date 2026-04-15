# 预处理性能优化

## Context

预处理阶段（多进程并行 df-pn 穷举）耗时过长。当前纯 Python 每 worker 仅 700–3600 nodes/sec。实测大题目（区域 45 格、20+ 空位）某些 worker 跑了 51000 秒（14 小时）。

瓶颈分析（按占比排序）：
1. `board.hash()` — 每次构建 169 字符字符串，TT key 为 ~175 字符，dict 哈希 175 字节
2. `group_and_libs()` — 每次调用分配 `bytearray(169)` + list + set，每个 `play_undoable` 调用 ~5 次
3. `_flush_tt` — `list(tt_dict.items())[offset:]` 是 O(TT总量)，30M 条目时灾难级
4. `_aggregate` — 对每个子节点做 play→hash→TT查→undo，N 次/轮
5. Worker 负载不均 — 静态轮询分配，有的秒完有的跑数小时

## 优化方案（按优先级）

### 1. Zobrist 增量哈希 — 替换 `board.hash()`

**文件**: `backend/board.py`, `backend/solver.py`, `backend/bincache.py`

**改动**:
- `board.py` 模块级：用固定种子生成 `ZOBRIST[169][3]` 表（64位随机数，index 0/1/2 对应 EMPTY/BLACK/WHITE）
- `Board.__slots__` 加 `"zh"`，`__init__` 初始化 `self.zh = 0`
- `play_undoable`：落子时 `zh ^= ZOBRIST[i][color+1] ^ ZOBRIST[i][1]`；提子时同理 XOR 翻转
- `undo`：做相同 XOR 还原（XOR 自逆）
- `clone` 复制 `zh`
- 删除 `hash()` 方法

- `solver.py`：`_tt_key` 改为返回 `(self.board.zh, turn, self.board.last_capture)` 元组
- TT 类型从 `Dict[str, ...]` 改为 `Dict[tuple, ...]`

- `bincache.py`：`_flush_tt` 和 `solve_from_cache` 中的 TT key 同步改为元组
- SQLite 存储 key 时用 `f"{zh}|{turn}|{lc}"` 字符串（仅在 flush 时转换，不影响内存中查表速度）

**预期**: TT 读写速度 ~3x 提升（int tuple hash ~30ns vs 175-char string ~2-4μs）

### 2. 复用 `bytearray` 缓冲区 — 消除 `group_and_libs` 分配

**文件**: `backend/board.py`

**改动**:
- `Board.__slots__` 加 `"_visited"`
- `__init__` 中 `self._visited = bytearray(size * size)`
- `group_and_libs` 用 `self._visited` 替代每次 `bytearray(size*size)`
- BFS 结束后只清零已访问的位置（O(group_size) 而非 O(169)）：
  ```python
  # BFS 结束后
  for pos in _touched:  # 收集所有 visited[pos]=1 的 pos
      visited[pos] = 0
  ```
- `clone` 中 `b._visited = bytearray(size * size)`（各自独立）

**预期**: `group_and_libs` 速度 ~1.5x（消除 bytearray 分配开销）

### 3. 修复 `_flush_tt` 的 O(N) 灾难

**文件**: `backend/solver.py`, `backend/bincache.py`

**改动**:
- `DfpnSolver` 新增 `self.tt_log: list = []`（记录新增 key 的有序列表）
- `_tt_set` 中：如果 key 不在 tt 中，`self.tt_log.append(key)`
- `bincache.py` 的 `_flush_tt` 改为：
  ```python
  new_keys = solver.tt_log[already_flushed:]
  db.executemany(..., ((str(k), solver.tt[k][0], solver.tt[k][1]) for k in new_keys))
  ```
  切片 O(新增量) 而非 O(总量)

**预期**: 消除随 TT 增长的二次开销。30M 条目时从每次 flush O(30M) 降为 O(10K)

### 4. 快速气数检查 — 减少 `play_undoable` 中的 BFS

**文件**: `backend/board.py`

**改动**:
- 新增 `Board.count_libs_fast(self, x, y, max_libs=2) -> int`：
  - 同 `group_and_libs` 的 BFS 但不收集 group 列表
  - `len(libs) >= max_libs` 时立即返回（提前终止）
  - 复用 `self._visited`
- `play_undoable` 中 4 邻提子检测改为两步：
  1. `count_libs_fast(nx, ny, max_libs=1)` → 返回 >0 则跳过（不需提子）
  2. 仅当返回 0 时才调完整 `group_and_libs` 获取坐标用于提子
- 自杀检测同理：`count_libs_fast(x, y, max_libs=1)` → 返回 >0 则合法

**预期**: `play_undoable` 速度 ~1.3x（多数邻群气>1，BFS 提前退出且不分配 group 列表）

### 5. Worker 负载均衡 — LPT 调度

**文件**: `backend/precompute.py`

**改动**:
- `run_precompute_parallel` 在分桶前，对每个根候选做浅探（max_nodes=500）估算难度
- 按难度降序排列，交替分配到 worker（LPT 最长处理时间优先算法）：
  ```python
  sorted_moves = sorted(zip(difficulty, root_moves), reverse=True)
  for i, (_, move) in enumerate(sorted_moves):
      buckets[i % num_workers].append(move)
  ```
- 浅探总开销 ~N×500 节点（N=根候选数，通常 10-20），几秒可完成

**预期**: 墙钟时间 ~1.5-2x（减少最慢 worker 的等待尾巴）

### 6. 1+ε Trick — 减少子节点切换开销

**文件**: `backend/solver.py`

**问题**：`_mid` 的 while 循环中，子节点预算用 `second_best + 1`，当两个子节点 pn/dn 接近时产生"乒乓球"现象——反复切换，每次只做极少有效搜索，但每次切换都要付出 `_aggregate` 的 O(N) play/hash/undo 开销。

```
循环1: best=子A(pn=100), second=子B(pn=101) → 预算102 → 子A升到103 → 切换
循环2: best=子B(pn=101), second=子A(pn=103) → 预算104 → 子B升到105 → 切换
... 反复乒乓
```

**改动**：将 `second_best + 1` 改为 `int(second_best * 1.25) + 1`（ε=0.25）

```python
# solver.py _mid 方法，第 185-190 行
# 原：
th_pn_c = min(th_pn, second_best + 1)
# 改为：
th_pn_c = min(th_pn, int(second_best * 1.25) + 1)

# 原：
th_dn_c = min(th_dn, second_best + 1)
# 改为：
th_dn_c = min(th_dn, int(second_best * 1.25) + 1)
```

**效果**：每次给最优子节点多 25% 预算，减少切换频率 ~60%。总搜索节点可能增加 ~15%，但节省的 `_aggregate` 开销远大于此。

**预期**: 整体搜索速度 ~1.3-2x 提升。ε=0.25 是文献推荐值（Nagai 2002），不影响证明正确性。

**风险**: 零。只影响搜索顺序，不影响结果。

### 7. Killer Move 排序 — 搜索经验指导走法顺序

**文件**: `backend/solver.py`

**问题**：`_gen_children` 只用静态信号排序（提子数+邻近棋子数），与搜索经验无关。df-pn 第一轮 `_aggregate` 时所有子节点 TT 值都是 (1,1)，默认选第一个。如果好着排在后面，要浪费大量节点才能通过 TT 值更新找到它。

**核心思想**：同一深度不同节点的"好着"往往相似（棋盘只差几手棋，局部形状类似）。记录每个深度最近导致证明成功的着法，下次同深度优先尝试。

**改动**：

1. `DfpnSolver.__init__` 新增 killer 表：
```python
self._killers: List[List[Tuple[int,int]]] = [[] for _ in range(max_depth + 1)]
```

2. `_gen_children` 增加 `depth` 参数，killer 着法加分：
```python
def _gen_children(self, turn, allow_pass, depth):
    killer_set = set(self._killers[depth]) if depth < len(self._killers) else set()
    # ... 现有循环 ...
    score = len(u.captured) * 10000
    for nx, ny in board.neighbors(x, y):
        if grid[ny * size + nx] != EMPTY: score += 5
    if (x, y) in killer_set:
        score += 50000  # 最优先
    # ...
```

3. `_mid` while 循环中，当节点被证明（pn==0 或 dn==0）时记录 killer：
```python
if pn == 0 or dn == 0:
    if best_idx >= 0 and kids[best_idx][0] is not None:
        self._record_killer(depth, kids[best_idx][0])
    return

def _record_killer(self, depth, move):
    killers = self._killers[depth]
    if move in killers: return
    killers.insert(0, move)
    if len(killers) > 2:
        killers.pop()
```

4. `_mid` 调用 `_gen_children` 时传 depth：
```python
kids = self._gen_children(turn, allow_pass=not is_or, depth=depth)
```

**适用条件**：分支因子大（15-25 空位）+ 同深度局面相似（生死题局部性强）→ 非常适合。

**预期**: 搜索节点数减少 20-40%。额外开销极小（每节点多一次 set 查询）。

**风险**: 零。只影响搜索顺序，不影响正确性。最坏情况退化为无 killer 的行为。

### 综合预期

| 优化 | 单项提速 | 累积效果 |
|------|---------|---------|
| 1. Zobrist 哈希 | ~3x TT 操作 | ~2x 整体 |
| 2. 复用 bytearray | ~1.5x BFS | ~2.5x |
| 3. 修复 flush | 消除 O(N²) | 大题目 >>3x |
| 4. 快速气检 | ~1.3x play | ~3x |
| 5. LPT 调度 | ~1.5x 墙钟 | ~4-5x 墙钟 |
| 6. 1+ε trick | ~1.3-2x 减少切换 | ~5-7x 墙钟 |
| 7. Killer move | ~1.3-1.5x 减少节点 | ~6-10x 墙钟 |

保守估计：全部实施后，大题目总预处理时间缩短到原来的 1/8 ~ 1/12。

## 实施顺序

6 → 7 → 1 → 3 → 2 → 4 → 5

6 和 7 是算法层优化（减少节点数/减少切换），改动最小、风险最低、对 PyPy 效果最好，优先实施。
1-5 是微观优化（减少每节点开销），逐步叠加。

## 验证

1. 单元测试：用已知题目跑 solver，对比优化前后的 result（必须一致）
2. 性能对比：同一题目记录优化前后 nodes/sec
3. 预处理端到端：`python3 backend/cli_precompute.py run <id>`，观察终端进度显示的 n/s 指标
4. 正确性：Zobrist hash 后 `solve_from_cache` 结果与原 string hash 一致

## 关键文件

- `backend/board.py` — Zobrist 表、zh 字段、_visited 缓冲区、count_libs_fast
- `backend/solver.py` — 1+ε trick、killer move、tt_key 改元组、tt_log 增量记录
- `backend/bincache.py` — flush 修复、solve_from_cache 适配
- `backend/precompute.py` — LPT 调度、worker 求解
- `backend/eyes.py` — 无需修改
