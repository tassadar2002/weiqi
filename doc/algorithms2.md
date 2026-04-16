# weiqi3 算法策略详解（当前版）

本文盘点当前代码实际使用的每一项搜索/数据结构/规则引擎策略，以及历史上
使用过但现已弃用的策略。每一项都覆盖：**是什么 / 在哪里用 / 为什么（不）用 / 效果**。

与 `doc/algorithms.md`（按历史演进组织）不同，本文只描述"现在这一版代码"的决策现状。

---

## 一、当前在用的策略

### 1. df-pn 主搜索（Depth-First Proof Number Search）

**是什么**：AND/OR 树上的"最佳优先+深度优先"证明数搜索。每个节点维护两个数：
- `pn`（proof number）：把该节点证成攻方胜所需的最小子节点证明数
- `dn`（disproof number）：证成守方胜所需的最小子节点证明数

OR 节点（攻方回合）：`pn = min(子.pn)`，`dn = Σ子.dn`
AND 节点（守方回合）：`pn = Σ子.pn`，`dn = min(子.dn)`
展开方向：始终往"最小证明代价"的子节点走（df-pn 的"most-proving node"原则）。

**在哪里用**：`backend/precompute/solver.py::DfpnSolver._mid` (L233) — 预处理 Worker 进程内穷举证明单个根着子树。

**为什么用**：
- 用户明确要求"可落子点 < 30 时必须得到唯一最优解，不能容忍错误"——必须是**严格证明**而非启发式评估。
- 相对 classic PN-search，df-pn 通过阈值下推改成深度优先递归，**内存从 O(树大小) 降为 O(搜索深度)**。当前大题目 TT 可达 30M+ 条目，classic PN 根本装不下。
- 相对 αβ + 迭代加深，df-pn 有"最薄弱子树优先"的启发，节点数少 3–10×。

**效果**：PyPy 下单 worker 跑 ~30k nodes/sec；30 空位大题目总节点 ~10⁷–10⁸，可在几分钟到几小时内证完。

---

### 2. 转置表 TT 存 `(pn, dn)`

**是什么**：`Dict[(zh, turn, last_capture) → (pn, dn)]`。同一局面经不同路径到达时共享证明进度。

**在哪里用**：
- `DfpnSolver._tt_get/_tt_set` (L65–77)
- key = `(board.zh, turn, last_capture)` (`_tt_key` L63)
- value 编码 `pn`/`dn` 各 1 字节（0~254 原值，255 表示 DFPN_INF），磁盘 record 仅 12 字节

**为什么用**：
- df-pn 的正确性依赖 TT：没有 TT 则同一局面重复展开，指数爆炸。
- `(pn, dn)` 比 αβ TT 的单个"分数 + flag"信息量更大——保留了"证明进度"，支持增量搜索。

**效果**：大题目 TT 命中率可达 60%+。命中一条比完整 `_mid` 调用快 10³× 以上。

---

### 3. Zobrist 增量哈希

**是什么**：为每个 `(位置, 颜色)` 预生成 64-bit 随机数 `ZOBRIST[169][3]`；落子/提子时 `zh ^= ZOBRIST[i][old] ^ ZOBRIST[i][new]`。XOR 自逆，`undo` 用相同操作还原。

**在哪里用**：
- 表生成：`board.py::ZOBRIST` (L18)
- 维护：`Board.play_undoable` (L203, L214 等) / `Board.undo` (L273, L278)
- 直接覆写 `grid` 后的补救：`Board.rebuild_zh()` (L49)
- `Board.set` 里也走 zh 更新 (L70)

**为什么用**：原方案是把 169 个 grid 值拼成字符串 `board.hash()`。每调一次分配 169 字符 + dict 查找时 hash 175 字节字符串，大题目里是首位瓶颈。Zobrist 让 `_tt_key` 变成 O(1) 拼 tuple，TT 操作整体 ~3× 提速。

**效果**：TT key 生成从 ~2–4μs 降到 ~30ns。对整体吞吐的贡献约 2×。

---

### 4. DiskTT（内存缓冲 + 磁盘存储的两级 TT）

**是什么**：外置的 TT 结构。写入先进内存 dict，超过 `max_entries` 时：
1. 内存 dict 排序
2. 与磁盘 `.bin` 已排序记录做双路归并
3. 写入新 `.bin.tmp`，`os.replace` 原子替换旧 `.bin`
4. 重新 mmap 打开磁盘层，内存清空
查询：先查内存 O(1)，miss 再查磁盘 O(log N)。

**在哪里用**：
- `backend/precompute/binstore.py::DiskTT` (L316)
- Worker 构造 `DiskTT(bin_path, max_entries)` 后作为 `tt=` 传给 `DfpnSolver`
- `max_entries` 由 `_calc_max_tt_entries` 按"可用内存 70% / worker 数"算出

**为什么用**：早期纯 dict TT 在 30M+ 条目大题上直接 OOM；且多进程并行时内存压力乘上 worker 数。两级存储把单 worker 峰值内存压到 "`max_entries` × ~112B" 可控范围，同时不丢数据——**断点续传**的基础：工作完成的 TT 已经落盘，再次 `run` 可直接复用。

**效果**：用 30G 可用内存、4 worker 时，`max_entries` ≈ 45M 条/ worker；实际 flush 开销远小于重复搜索的开销。

---

### 5. tt_log 增量刷盘

**是什么**：`DfpnSolver` 额外维护一个 `tt_log: List[tuple]`，每次 `_tt_set` 遇到新 key 就 append。刷盘时只序列化 `tt_log[already_flushed:]`。

**在哪里用**：
- `DfpnSolver.tt_log` (L54) + `_tt_set` 分支 L75
- `binstore.py::_flush_records` (L94)：`new_keys = solver.tt_log[already_flushed:]`

**为什么用**：原实现 `list(tt.items())[offset:]` 每次 flush 要遍历整张 TT，30M 条目时单次 flush 耗时秒级，随 TT 增长变成 O(N²) 灾难。有了 `tt_log` 后切片只扫增量，O(新增量)。

**效果**：30M 条目题目单次 flush 从 O(30M) 降到 O(10⁴)。消除了预处理后期的二次开销。
（注：只在内部 dict 模式下使用；DiskTT 模式走自己的 `flush()`，不依赖 `tt_log`。）

**限制**：仅当 `solver.tt` 是内部 dict（`_external_tt == False`）时才维护。Worker 现在走 DiskTT 路径，这条优化主要在单进程直调 `DfpnSolver` 的场景下生效（如 `test_solver.py`）。

---

### 6. 1+ε Trick（ε=0.25）

**是什么**：df-pn 内层 while 循环里，原版给最优子节点的阈值是 `second_best + 1`。改为 `int(second_best * 1.25) + 1`，让当前最优子多 25% 预算。

**在哪里用**：`DfpnSolver._mid` L265 (OR 节点 `th_pn_c`) 和 L269 (AND 节点 `th_dn_c`)。

**为什么用**：当 `best` 和 `second_best` 的 pn/dn 接近时，`+1` 阈值会产生"乒乓球"现象——反复在两个子节点间切换，每次只做极少有效搜索却都要付出一次 `_aggregate`（play→TT 查→undo）开销。给 25% 预算后能在最优子节点上多停留，减少切换。

**效果**：切换频率减少约 60%。总搜索节点可能因为多跑了 25% 而略增（+15% 左右），但节省的 `_aggregate` 开销更大，净收益 1.3–2× 整体加速。

**来源**：Nagai (2002) 推荐值，不影响证明正确性。

---

### 7. Killer Move 排序

**是什么**：每个深度 `d` 维护最近 2 个曾导致证明成功的着法（`_killers[d]`）。生成子节点时，killer 着法加 50000 分（高于提子的 10000 分），排在最前。

**在哪里用**：
- 存储：`DfpnSolver._killers` (L58)
- 记录：`_record_killer` (L181) — `_mid` 里 `pn==0 or dn==0` 时调用 (L260)
- 使用：`_gen_children` 的 `killer_set` (L116) + 加分 (L172–173)

**为什么用**：
- `_gen_children` 第一轮调 `_aggregate`，所有子节点 TT 值默认 `(1,1)`，`best_idx` 取排在前面的第一个。如果"真正的好着"排在后面，要多轮循环 + TT 反馈才能挑到它，浪费大量节点。
- 同一深度不同 `_mid` 调用的棋盘相似（只差一手），好着也往往相似。记录后命中率很高。

**效果**：搜索节点数减少 20–40%。额外开销极小（每节点一次 set 查询）。

---

### 8. 走法静态排序（提子优先 + 邻近实子加分）

**是什么**：`_gen_children` 为每个候选着打分：
- 本手能提子：`score += captured_count × 10000`
- 4 邻每有一子：`score += 5`
- killer：`score += 50000`
排序后送入 `_aggregate` 和 `_mid` 递归。

**在哪里用**：`DfpnSolver._gen_children` L126–174；最后 `kids.sort(key=lambda k: -k[1])` L176。

**为什么用**：df-pn 的 `_aggregate` 在首轮对所有 TT=`(1,1)` 的子节点"等价选择"，`best_idx` 是第一个最优。把提子着排到最前显著缩短"找到真胜着"所需的 `_mid` 轮数——提子通常是决定性走法。

**效果**：预处理时大题目加速 1.3× 以上（与 killer 叠加后更明显）。

---

### 9. 合法性快速路径

**是什么**：`_gen_children` 对每个空位先看 4 邻：
- 至少一个空邻（落子后自己有气）
- 对手相邻群气 ≥ 2（落子后不提子）
- 非 ko 再现（`last_capture != idx`）

三条同时满足 → **一定合法**，直接 append，跳过 `play_undoable/undo` 的完整模拟。否则走慢速路径。

**在哪里用**：`_gen_children` L162–170。

**为什么用**：
- 绝大多数生成出来的走法是"普通落子"，没有紧气、没有提子、没有 ko。对这些走法调 `play_undoable/undo` 是纯开销：各约 169 行代码的路径 + 4 次 `count_libs_fast` + 自杀检查。
- 快速路径只扫 4 个邻居值，几乎零成本。

**效果**：`_gen_children` 里约 80–90% 的候选走快速路径。整体加速 1.5–2×。

---

### 10. count_libs_fast（提前终止的气数 BFS）

**是什么**：类似 `group_and_libs`，但不收集 group 列表，找到第 `max_libs` 口气就立刻返回。

**在哪里用**：
- `board.py::count_libs_fast` (L147)
- 生成候选时判"会不会提子"：`_gen_children` 里 L134/L142/L150/L158，`max_libs=2`（只关心"≤1 还是 ≥2"）
- `play_undoable` 提子检测 L210/L217/L224/L231，`max_libs=1`（只关心"是否会被提"）
- `play_undoable` 自杀检测 L240，`max_libs=1`

**为什么用**：95% 情况下邻群气 ≥ 2，BFS 只要访问两个空邻就返回，根本不用走完整张群。完整 `group_and_libs` 必须扫完全群 + 所有气。

**效果**：`play_undoable` 中 BFS 开销降低 ~1.3×；与快速路径叠加后，生成候选的总开销降低到原来的 40–50%。

---

### 11. `_visited` bytearray 复用（Epoch 清零模式）

**是什么**：`Board.__slots__` 里挂一个 `_visited = bytearray(169)`。BFS 时直接标 1，结束后只清零 `touched` 列表里的位置（BFS 实际访问过的）——`O(群大小 + 气数)` 而非 `O(169)`。

**在哪里用**：
- `board.py::Board.__init__` L45
- `group_and_libs` (L96, L142–143)
- `count_libs_fast` (L156, L182–187)
- `clone` 为新 Board 分配独立 bytearray (L61)

**为什么用**：原实现每次 BFS 都 `bytearray(169)`（分配 + 释放）。大题目预处理里 BFS 调用次数亿级，分配开销成瓶颈。复用 + 局部清零把每次成本压到接近零。

**效果**：`group_and_libs` / `count_libs_fast` 各加速约 1.5×。PyPy JIT 也更好优化稳定的 bytearray 访问。

---

### 12. 撤销式落子（play_undoable / undo）

**是什么**：`play_undoable` 返回 `UndoInfo`，记录落子坐标 + 本次提掉的子坐标 + 之前的 `last_capture`。`undo(u)` 按 `UndoInfo` 原地还原，不 clone 棋盘。

**在哪里用**：
- `board.py::play_undoable` (L192) / `undo` (L268)
- 搜索中：`DfpnSolver._play_kid/_undo_kid` (L194–209)、`_aggregate` (L288–292)
- `solve_from_store` 也走同样模式 (L424–428)

**为什么用**：clone 一次 13×13 棋盘涉及 169 元素 list copy + zh 复制，成本虽单次不大但搜索中调用亿级。撤销式 = O(1) 回退 + 零分配。

**效果**：相对 clone 方案，单次落子/回退成本降低 10× 以上；对整体加速 ~5×。

---

### 13. 多目标复合终止条件

**是什么**：同时支持"杀若干块 + 守若干块"，按以下顺序判：
1. 任一 `defend_target` 被提（空格） → `DEF`
2. 任一 `kill_target` 做出双真眼（`count_real_eyes >= 2`） → `DEF`
3. 所有 `kill_targets` 都被提 → `ATK`

**在哪里用**：`DfpnSolver._terminal` L81–100；真眼判定在 `eyes.py::count_real_eyes`。

**为什么用**：用户提出"同时吃两块白棋 + 保住一块黑棋"的复合场景。单目标模式是此逻辑的特例（kill_targets 一个、defend_targets 为空）。

**效果**：支持多目标题型。正确性依赖——条件顺序很重要：必须先判 `defend`（其中一块守方目标被吃就算攻方输，哪怕其他杀目标还活着）。

---

### 14. 多进程根着分工 + 任务队列（Work-Stealing）

**是什么**：Coordinator 枚举根着（攻方在区域内所有合法首手）→ 推入 `multiprocessing.Queue`。Worker 进程从队列取一个根着，跑 `DfpnSolver` 证完它的子树，输出 `{job_id}_{x}_{y}.bin`，然后再取下一个。

**在哪里用**：
- `backend/precompute/coordinator.py` 事件循环
- `backend/precompute/worker.py` 主循环
- `binstore.py::_merge_worker_bins` 把所有 worker 的 `.bin` 合并到最终 `{job_id}.bin`

**为什么用**：
- 静态轮询分配会遇到"最慢 worker 的尾巴"问题——某些根着可能比别人难 100×，分到它的 worker 独自跑数小时而其他 worker 早早空闲。
- 动态队列天然均衡：简单根着秒完就拿下一个，难根着慢慢跑。

**效果**：墙钟时间相对静态分配加速 1.5–2×。worker 数 = CPU × 70%（`_calc_num_workers`）。

---

### 15. 崩溃恢复（job_id 复用 + 孤儿清理 + 根着跳过）

**是什么**：
- 每个 job 的 worker PIDs 写 `{job_id}_pids.json`。重启 `run` 时先读它，对还活着的旧 PID 发 SIGTERM。
- Coordinator 枚举根着时，凡是 `{job_id}_{x}_{y}.bin` 已存在且 `header.status == 1`（证完标记）的，直接跳过，不入任务队列。
- Worker 证完一个根着后 `_mark_bin_done` 翻转 status byte = 1（见 `worker.py`）。

**在哪里用**：`backend/precompute/coordinator.py`（`_kill_orphan_workers`、`_init_queues`）；`backend/precompute/worker.py::_mark_bin_done`。

**为什么用**：大题目预处理以小时计，中断（机器重启、Ctrl+C、OOM）时丢全部工作不可接受。根着粒度的 checkpoint 在工作量 / 实现复杂度之间取得平衡——比每次 `_mid` 递归都 checkpoint 简单得多，又足够细。

**效果**：断点续传粒度 = 单个根着（通常几分钟到几十分钟）。最坏情况丢失正在跑的根着的进度。

---

### 16. 二进制存储 + mmap 查表（BinStore）

**是什么**：预处理输出的 `.bin` 文件是**按 key 排序的 12 字节记录**。`BinStore` 用 `mmap` 映射整个文件 + 二分查找。

**在哪里用**：
- 格式定义：`binstore.py` 顶部 (L22–31)
- 查表：`BinStore.lookup` (L222)
- `/api/solve` 入口：`server.py::_h_solve` → `solve_from_store`

**为什么用**：
- `.bin` 常驻在"磁盘冷数据"层，Python 进程不占堆；mmap 让操作系统按页缓存，访问热区 ~0 开销。
- 二分查找 O(log N)，30M 条目约 25 次比较 = 亚微秒。
- 记录定长 12B + key 定长 10B 可直接 `bytes` 比较（大端序 + unsigned 编码，保证字节序 = 数值序）。不需要反序列化。

**效果**：
- 相对 SQLite+dict：查询延迟从 ~50ms 降到 <1ms。
- 相对全部加载到 dict：30M 条目省 3.5GB 内存。
- 6.3× 压缩（12B/条 vs Python dict ~75B/条）。

---

## 二、已弃用的策略

### D1. Vitalness 破平（棋形要点优先）

**是什么**：对已证明的多个胜着按"要点性"排序。`vitalness(x, y)` = 该点相邻方位中属于根杀目标气的数量。多个等价胜着时返回 vitalness 最大的（真正的要点点）。

**曾用在**：原先的在线 `DfpnSolver._extract_best_move`——配合"穷举根证明"一起用。

**为什么弃用**：`/api/solve` 现在只查预处理结果，不再排序备选。`solve_from_store` 的策略是：
- 遍历合法走法
- 第一个 `child_pn==0`（OR 节点）或 `child_dn==0`（AND 节点）的直接返回
- 没有胜着时返回 dn 最大的顽抗着

**为什么这样够用**：预处理阶段 df-pn 已经证出**存在**胜着；在线查表只需要**返回一个**胜着即可，不需要棋形审美。用户提供的题目通常胜着唯一或等价。

**代价**：极少数"多个等价胜着"的题目可能返回非要点。如果用户反馈这个问题，可以在 `solve_from_store` 里加 vitalness 破平（代码里留了扩展点——遍历已经全扫，加打分几乎免费）。

---

### D2. 跨请求 TT 缓存（`_TT_CACHE` + `reuse_tt=True`）

**是什么**：模块级 `Dict[(region, targets, attacker) → tt]`。同一题连续 9 手对弈时，第一手建立的 TT 保留给后续 8 手复用。内存上限：单表 > 50 万条丢弃、缓存 > 8 张表则 LRU 淘汰。

**曾用在**：原先的 `DfpnSolver.__init__(reuse_tt=True)`；每次 `/api/solve` 请求结束后回写 TT 到缓存。

**为什么弃用**：预处理架构下，第一次之后**不再调 `DfpnSolver`**——所有后续手直接查 `.bin`。跨请求内存缓存的前提（"连续同一棵子树的多次递增查询"）消失了。

**历史效果**：原方案下首手 3441ms，后续 8 手从 2427ms 降到 7ms（347× 加速）。这份加速现在由"离线预处理 + 在线查表"以另一种方式兑现（每手 <1ms）。

---

### D3. 穷举根证明（Exhaustive Root Proof）

**是什么**：主 df-pn 搜索找到第一个胜着就停（因为根 pn=0 已满足）。但这样**其他根子节点 pn/dn 还是 `(1,1)`**，没法在等价胜着中排序。穷举根证明：主搜索完后继续对所有根子节点跑 `_mid`，逐个证完，为 vitalness 打分提供素材。

**曾用在**：原先的 `DfpnSolver.solve()` 尾部。

**为什么弃用**：
- 它服务的 vitalness 已弃用（见 D1）。
- 预处理下每个根着都由独立 worker 证明——这本身就是"穷举"。

**效果**：随 vitalness 一起自然消失。预处理输出的 `.bin` 里所有根子节点 pn/dn 天然都是已证完的值。

---

### D4. 静态 worker 分桶（LPT 调度）

**是什么**：预处理启动时，对每个根着做浅探（500 节点）估算难度。按难度降序交替分给每个 worker。这是"最长处理时间优先"（LPT）算法。

**曾用在**：旧 `backend/precompute.py::run_precompute_parallel`。

**为什么弃用**：LPT 只是"静态预估"——依赖浅探的难度估算准。实测估算误差大（树形变化剧烈），有时把一个大坑分给只有一个 worker 的队列，其他 worker 空闲。改成**任务队列 + work-stealing**（策略 14）后天然均衡，不需要预估。

**效果对比**：LPT 墙钟 ~1.5× 加速（相对轮询）；队列 + work-stealing ~1.5–2×（相对 LPT），且实现更简单，代码行数少 40%。

---

### D5. Minimax + αβ（原搜索算法）

**是什么**：经典零和博弈算法，带静态评估函数（气数、眼数、提子数加权打分）。

**曾用在**：v1（JS 单文件版）和 v2 最初的 `solver.py`。

**为什么弃用**：用户明确要求"不能容忍错误，必须得到唯一最优解"。minimax 依赖**截断深度上的静态评分**——无论评分函数多精巧，都是**近似**而非真相。无法给出数学严格的生死证明。df-pn 是为"给出证明"而非"挑出好着"设计的，天然契合生死问题。

---

### D6. MCTS（从未使用，仅考虑过）

**是什么**：蒙特卡洛树搜索。在高分支因子游戏（全盘围棋 19×19）上是主流。

**为什么不用**：
- 用户明确指示"落子点不会太多（<30），不用 MCTS"。
- MCTS 给概率性最优解而非**证明**，与"严格求解"需求不符。
- 局部死活的分支因子在 20–35 之间，精确搜索（df-pn）有明确优势。

---

### D7. `board.hash()` 字符串哈希

**是什么**：把 169 个 grid 值拼成字符串作为 TT key。

**为什么弃用**：每次调 `hash()` 分配 169 字符对象，TT 查找时需 hash 175 字节字符串（~2–4μs）。被 Zobrist 增量哈希（策略 3）取代后快 ~60×。

**残留**：`board.py::Board.hash()` 还在（L304），注释标注 "Legacy string hash (for compatibility)"。当前代码无调用——保留是为了兼容测试或未来调试。可以删除。

---

## 三、策略协同关系

某些策略单独看效果有限，叠加后互相放大：

```
撤销式落子 (12)            — 每次搜索不 clone 棋盘
  ↓ 使得 play/undo 成为热路径
合法性快速路径 (9)          — 大部分走法跳过 play/undo
  ↓ 剩下的少数走 play_undoable
count_libs_fast (10)       — play_undoable 里的提子检测也用快速气数
  ↓ 气数 BFS 本身要访问 bytearray
_visited bytearray (11)    — 消除 bytearray 分配
  ↓ BFS 里的 O(169) 清零
epoch 清零                 — BFS 仅清零 touched 位置

搜索层：
走法静态排序 (8) + killer (7) → 让 _aggregate 首轮就找到好着
  ↓ 减少 _mid 的 while 循环轮数
1+ε trick (6) → 减少 while 循环里的切换
  ↓ 每轮循环调用 _aggregate + _mid 更少
TT (2) + Zobrist (3) → 使得每次 _mid 的"查是否已证过"≈ 0 开销

内存/断点续传层：
DiskTT (4) → TT 不怕大（工作量边界 = 磁盘）
  ↓ Worker 内存可控
多进程根着 (14) → 利用多核 + 隔离失败
  ↓ 每个根着独立 .bin
崩溃恢复 (15) → 进程死掉不丢根着
  ↓ 预处理产物最终合并
BinStore mmap (16) → 在线查询 <1ms
```

综合效果：相对 v1（JS 单文件）纯 αβ 实现，当前预处理 + 查表架构在**大题目**上可实现 10–100× 墙钟加速（含多进程），查询延迟降低 >1000×。

---

## 四、参考

- df-pn 主算法：Nagai (2002), "Df-pn: A New Proof-Number Search Algorithm"
- 1+ε trick：同上论文
- 预处理设计：`doc/pre_compute01.md`（优化方案）、`doc/pre_compute02.md`（调度架构）
- 存储格式：`doc/db.md`
- 历史演进：`doc/doc.md`、`doc/algorithms.md`
