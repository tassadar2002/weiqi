# 预处理架构演进：小任务 + 任务队列

## 背景

预处理系统对每个习题做 df-pn 穷举证明，生成 TT 存储供解题查表。原架构存在三个问题：

1. **内存不可控**：单个 worker 的 `solver.tt`（Python dict）随搜索无限增长，大题目 OOM
2. **负载不均**：静态分桶（LPT），某些 worker 跑数小时，其他空闲等待
3. **崩溃丢失**：worker 崩溃则该 worker 负责的所有根着进度全部丢失

## 架构演进

### 阶段 1：静态分桶

```
Coordinator 生成 N 个根着 → LPT 排序 → 分配到 W 个桶
Worker 0: [根着A, 根着D, 根着G]  → 顺序执行，每个跑到证明完
Worker 1: [根着B, 根着E, 根着H]
Worker 2: [根着C, 根着F]
```

问题：Worker 0 分到最难的根着 A（14 小时），Worker 2 分到简单的 C、F（共 5 分钟）后空闲。

### 阶段 2：小任务 + 任务队列（当前）

```
┌─────────────────────────────────────────────────────┐
│ Coordinator                                         │
│  生成根着 → 放入 Queue → 事件循环（监控/崩溃恢复）  │
└───────────┬─────────────────────────┬───────────────┘
            │                         │
     ┌──────▼──────┐          ┌──────▼──────┐
     │ Task Queue  │          │ Result Queue│
     │ (mp.Queue)  │          │ (mp.Queue)  │
     └──────┬──────┘          └──────▲──────┘
            │                        │
     ┌──────▼────┐  ┌──────────┐  ┌──┴─────────┐
     │ Worker 0  │  │ Worker 1 │  │  Worker 2  │
     │ (无状态)  │  │ (无状态) │  │  (无状态)  │
     └───────────┘  └──────────┘  └────────────┘
```

## 核心设计

### 小任务

```python
Task = (root_move, bin_path)
# root_move: (x, y) 根着法
# bin_path: 该根着的 .bin 文件路径（= checkpoint）
```

每个任务 = 对一个根着点执行一段 df-pn 搜索（节点预算 500 万 ≈ PyPy 下几分钟）。

### 无状态 Worker

Worker 不绑定特定根着点，循环从队列取任务：

```python
while True:
    task = queue.get()          # 取任务
    if task is None: break      # poison pill → 退出
    # DiskTT 自动加载 .bin checkpoint
    disk_tt = DiskTT(task.bin_path, max_entries)
    solver = DfpnSolver(..., max_nodes=budget, tt=disk_tt)
    result = solver.solve(...)
    disk_tt.close()
    if result == "UNPROVEN":
        queue.put(task)         # 未完成 → 放回队列
    else:
        result_queue.put(...)   # 已证明 → 汇报
```

同一根着的段 1 由 Worker A 执行，段 2 可能由 Worker C 执行（A 在忙别的任务）。

### DiskTT（内存缓冲 + 磁盘存储）

解决核心矛盾：df-pn 需要随机访问全量 TT，但全量 TT 放不进内存。

```
┌───────────────────────────────┐
│ DiskTT                        │
│                               │
│  mem: dict     ← O(1) 查询    │
│  (容量有限，满则 flush)       │
│                               │
│  disk: BinStore ← O(log N)   │
│  (mmap 二分查找，容量无限)    │
│                               │
│  get: mem → miss → disk       │
│  set: 写 mem → 满 → flush    │
│  flush: 排序 + 双路归并 → .bin│
└───────────────────────────────┘
```

**flush 归并**：内存数据排序后与磁盘 .bin 双路归并，相同 key 以内存为准（覆盖旧值），写入新 .bin。

### 资源分配（70% 规则）

所有资源使用 70% 上限，为系统和 HTTP 服务留余量：

- **Worker 数** = `CPU 核数 × 70%`
- **每 worker 内存缓冲** = `可用内存 × 70% / worker 数 / 每条开销`

```
每条 TT 在 Python dict 中的开销：
  CPython: ~188 字节
  PyPy:    ~112 字节
```

示例（PyPy）：

| 机器配置 | Worker 数 | 每 worker 缓冲 |
|---------|----------|---------------|
| 4 核 / 4GB | 2 | 1250 万条 |
| 8 核 / 8GB | 5 | 1000 万条 |
| 20 核 / 32GB | 14 | 1428 万条 |

### Coordinator 事件循环

不只是启动 worker 然后等待，而是持续监控：

```python
while len(done) < len(root_moves):
    收集心跳（worker 开始/完成/放回任务）
    收集结果（已证明的根着）
    检查 worker 存活：
        崩溃 → 回收丢失的任务放回队列 → 启动替补 worker
    汇总进度 → 写 progress.json
    sleep(2)
```

### 崩溃恢复

```
Worker 0 取走任务 A → 正在搜索
Worker 0 崩溃（OOM）
  → A.bin 磁盘 checkpoint 还在（之前 flush 的数据）
  → Coordinator 检测到 Worker 0 死亡
  → 回收 A → queue.put(A)
  → 启动替补 Worker
  → 新 Worker 取到 A → DiskTT 加载 A.bin → 续传
  → 最多丢失一段预算的搜索量（500 万节点 ≈ 几分钟）
```

## 执行示例

4 核 (2 workers)，根着 A~E，预算 500 万节点/段：

```
T0   队列: [A,B,C,D,E]    W0 取 A           W1 取 B
T3   队列: [C,D,E]        W0: A 段1 搜索中   W1: B 完成! → result
                                              W1 取 C
T8   队列: [D,E]          W0: A 段1 预算到    W1: C 段1 搜索中
     队列: [D,E,A] ←放回  W0 取 D
T12  队列: [E,A]          W0: D 完成!         W1: C 段1 预算到
     队列: [E,A,C] ←放回  W0 取 E             W1 取 A ← 不同 worker 续传
T25                       W0: E 完成!         W1: A 段2 完成
     队列: [C]            W0 取 C             (W1 空闲 → 取不到 → 等)
T30                       W0: C 段2 完成!     W1 取到 None → 退出
T38                       W0: A 最终完成?     (看队列是否还有 A)
全部完成 → 合并 → 主 .bin
```

没有 worker 空闲等待——只要队列有任务就取。

## 与旧架构对比

| | 静态分桶 | 小任务 + 队列 |
|---|---|---|
| Worker 数 | cpu - 1 | **cpu × 70%** |
| 任务分配 | 静态 LPT 分桶 | **Work-Stealing 队列** |
| solver.tt | Python dict（无限增长）| **DiskTT（内存有界 + 磁盘）** |
| 内存 | 不可控，可能 OOM | **可用内存 × 70% / worker 数** |
| 单任务时间 | 无限（跑到完） | **有界（500 万节点 ≈ 几分钟）** |
| 崩溃恢复 | 丢失全部 | **.bin checkpoint，最多丢一段** |
| Worker 空闲 | 分到的桶跑完就等 | **自动取下一个任务** |
| 负载均衡 | 依赖 LPT 预估 | **天然均衡** |

## 涉及文件

| 文件 | 改动 |
|------|------|
| `backend/precompute/binstore.py` | 新增 `DiskTT`、`_calc_max_tt_entries()`、`_calc_num_workers()`、`_merge_flush()` |
| `backend/precompute/solver.py` | `__init__` 新增 `tt` 参数；`_tt_get`/`_tt_set`/`_check_limits` 适配 DiskTT |
| `backend/precompute/coordinator.py` | 事件循环、任务队列、崩溃恢复（原 `run_precompute_parallel`） |
| `backend/precompute/worker.py` | 无状态 worker 主循环（原 `_worker_loop`） |

## DiskTT flush 机制详解

### 写入流程

```
set(key, pn, dn)
  → mem[key] = (pn, dn)
  → if len(mem) >= max_entries:
      flush()
```

### flush 流程

```
1. sorted_mem = sorted(mem.items())     # 内存数据排序
2. 打开旧 .bin 的 record 迭代器
3. 双路归并写入新 .bin：
   - disk 的 key < mem 的 key → 写 disk record
   - mem 的 key < disk 的 key → 写 mem record
   - 相同 key → 写 mem（覆盖旧值）
4. 替换旧 .bin → 重新 mmap
5. 清空 mem
```

### flush I/O 开销

| 每 worker 缓冲 | flush I/O 大小 | SSD 耗时 |
|---------------|--------------|---------|
| 500 万条 | ~120 MB | ~0.2 秒 |
| 1000 万条 | ~240 MB | ~0.5 秒 |
| 2000 万条 | ~480 MB | ~1 秒 |
