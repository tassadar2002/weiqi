# weiqi3 — 围棋习题集严格求解系统 · 完整架构文档

> **用途**：把这个文件拿到任何新环境，Claude Code 可以据此完整复刻出整个项目。
> 包含：产品定义、架构、算法、数据模型、API 协议、文件结构、代码规范。

---

## 1. 产品定义

### 1.1 一句话

13×13 围棋死活/对杀题目的严格求解习题集平台，前后端分离，预处理模式确保结果完全正确。

### 1.2 核心能力

| 能力 | 说明 |
|---|---|
| **习题管理** | 首页列表展示所有题目；可新建、编辑、删除；每题独立存储 |
| **布局编辑** | 13×13 棋盘，自由放置黑白子，设定可落子区域 |
| **多目标指定** | 点白子=杀目标(红高亮)，点黑子=守目标(蓝高亮) |
| **预处理穷举** | 命令行多进程并行 df-pn（无限制），增量写入二进制缓存，完成后结果永久缓存 |
| **秒出最优解** | 预处理完成后，直接查 TT 缓存，毫秒级返回严格最优着 |
| **自动对弈** | 双方按证明树落子到棋盘真正终局 |
| **决策日志** | 每步显示攻/防角色、必胜/顽抗标签、节点数、耗时 |

### 1.3 核心设计理念

**"先穷举，后查表"** —— 不追求在线实时求解的速度优化，而是靠预处理一次性算完+缓存。

结果是：
- 只需要耐心等待预处理完成（分钟到小时级）
- 完成后每一步都是严格最优、毫秒级响应

### 1.4 用户流程

```
首页（习题列表）
  ├─ [+ 新建] → 空白棋盘
  └─ [点击某题] → 加载已有布局

习题详情页：
  布局 → 设置落子点 → 设定目标 → 确认 → 保存 → 返回列表

命令行预处理：
  python3 backend/precompute.py list              # 列出所有题目
  python3 backend/precompute.py run <problem_id>  # 运行预处理（自动切换 pypy3）
  python3 backend/precompute.py status <problem_id>  # 查看进度/结果
                                              ↓
                                    多进程并行穷举 df-pn
                                    终端实时显示进度
                                    TT 条目增量写入二进制缓存
                                              ↓
                                    预处理完成 → 浏览器"最优解"秒出
```

### 1.5 约束

- **Python 3.11**（后端指定版本）
- **零外部依赖**：后端纯 stdlib（不需 pip install）
- **零构建步骤**：前端纯 HTML/JS/CSS（不需 npm/webpack）
- **单命令启动**：`python3 backend/server.py`
- **可读性优先**：代码清晰 > 极致性能
- **棋盘 13×13**：grid 为 169 个整数（`BOARD_SIZE = 13`）

---

## 2. 架构

### 2.1 整体

```
┌──────────────────────────┐      HTTP/JSON       ┌──────────────────────────────┐
│  浏览器 (frontend/)       │ ──────────────────→  │  Python 3 (backend/)          │
│                          │                        │                              │
│  index.html              │  /api/problems/*       │  server.py  HTTP 路由         │
│  style.css               │  /api/play             │  board.py   规则引擎          │
│  board.js   [极简数据]    │  /api/solve            │  eyes.py    真眼判定          │
│  region.js  [区域掩码]    │  /api/validate_target  │  target.py  目标构造          │
│  api.js     [fetch 包装] │                        │  solver.py  df-pn 求解器      │
│  renderer.js [Canvas]    │                        │  precompute.py 多进程预处理(CLI) │
│  app.js     [FSM 控制]   │ ←──────────────────   │  problems.py 习题 CRUD        │
└──────────────────────────┘                        └──────────────────────────────┘
```

### 2.2 两种求解模式

| 模式 | 触发 | 过程 | 结果 |
|---|---|---|---|
| **预处理（主要）** | 命令行 `precompute.py run` | 多进程无限制穷举 → 全量 TT 写入二进制缓存 (.bin) | 永久缓存，毫秒级查询 |
| **查表（预处理后）** | 用户点"最优解" | mmap 打开 .bin → 二分查找 pn/dn → 遍历子节点找 pn=0 的着 | 不跑 df-pn，纯查表 |

**预处理完成后的查询不需要**：跨请求 TT 缓存、vitalness 破平、走法排序、穷举根证明、顽抗着选择。这些仅在"在线实时求解"场景下有用，预处理模式下全部冗余。

### 2.3 数据存储

| 存储 | 位置 | 用途 |
|---|---|---|
| 习题数据库 | `backend/data/problems.db` | 习题的布局、区域、目标、状态 |
| 预处理缓存 | `backend/cache/{job_id}.bin` | 每题的完整 TT（排序二进制记录，mmap 查询） |
| 预处理进度 | `backend/cache/{job_id}_progress.json` | 实时进度（CLI 终端 / status 命令读取） |

全部 SQLite + JSON，无需任何数据库服务。

---

## 3. 核心算法：df-pn

### 3.1 概述

**df-pn (Depth-First Proof Number Search)**：Kishimoto 2002。对围棋死活题做严格证明搜索。

- **pn (proof number)** = 证明"攻方胜"所需的最小代价
- **dn (disproof number)** = 证明"防方胜"所需的最小代价
- **OR 节点**（攻方行棋）：pn = min(子.pn)，dn = sum(子.dn)
- **AND 节点**（防方行棋）：pn = sum(子.pn)，dn = min(子.dn)
- 每次展开选 pn 最小（OR）或 dn 最小（AND）的子节点

### 3.2 终止条件（复合多目标）

```
攻方胜 (ATK) = 所有 kill_targets 被提子
防方胜 (DEF) = 任一 kill_target 做出 2 真眼
             OR 任一 defend_target 被提子
```

### 3.3 真眼判定

空点 P 是 color 方真眼，当且仅当：
1. P 为空点
2. P 的 4 个正交邻**全部属于同一连通群**（不仅是同色）
3. 对角条件：边/角所有存在的对角同色；内部 ≥3 同色

### 3.4 棋盘规则引擎

- **13×13** 棋盘，`list[int]`（-1=白, 0=空, 1=黑），169 个元素
- 撤销式落子：`play_undoable()` 返回 `UndoInfo`，`undo()` 原地恢复
- 群与气：洪水填充（bytearray visited + 内联 4 邻展开）
- 简单 ko：`last_capture` 记录上一手提子位置，禁止立即回提
- 合法走法：限定于 `region_mask`（0/1 数组，169 个元素），`play_undoable` + `undo` 逐个试

### 3.5 预处理阶段需要的 solver 特性

| 特性 | 预处理时 | 查表时 | 说明 |
|---|---|---|---|
| 转置表 (TT) | 需要 | 从 .bin 缓存 mmap 加载 | df-pn 的核心数据结构 |
| 走法排序 | 需要 | 不需要 | 好的排序加快穷举收敛 |
| `try/finally` play/undo | 需要 | 不需要 | 防止异常导致棋盘脏 |
| `progress_callback` | 需要 | 不需要 | 报告穷举进度 |
| 跨请求 TT 缓存 | 不需要 | 不需要 | SQLite 已是完整持久缓存 |
| Vitalness 破平 | 不需要 | 不需要 | 穷举后所有胜着都已证完，查表时直接遍历 |
| 穷举根证明 | 不需要 | 不需要 | 预处理本身就穷举所有 |
| 顽抗着逻辑 | 不需要 | 查表时用简化版 | 落败方从 TT 中选任意合法着即可 |

### 3.6 查表阶段的求解逻辑

```python
def solve_from_cache(tt, board, turn, region_mask, kill_targets, defend_targets, attacker_color):
    """
    预处理完成后的查表求解。不跑 df-pn，纯查 TT。
    """
    # 1. 查根结果
    root_key = tt_key(board, turn)
    root_pn, root_dn = tt.get(root_key, (1, 1))

    if root_pn == 0:
        result = "ATTACKER_WINS"
    elif root_dn == 0:
        result = "DEFENDER_WINS"
    else:
        result = "UNPROVEN"  # 不应发生（穷举完了）

    # 2. 找最优着
    is_or = (turn == attacker_color)
    best_move = None
    best_move_certain = False

    # --- 胜方：选必胜着 ---
    winning_moves = []
    # --- 败方：选"最有可能翻盘"的顽抗着 ---
    resist_move = None
    resist_score = -1  # 越大 = 对手越难推进 = 我方翻盘可能性越大

    for x, y in board.legal_moves_in_region(turn, region_mask):
        u = board.play_undoable(x, y, turn)
        if u is None:
            continue
        child_key = tt_key(board, -turn)
        child_pn, child_dn = tt.get(child_key, (1, 1))
        board.undo(u)

        if is_or and child_pn == 0:
            winning_moves.append((x, y, child_dn))
        elif not is_or and child_dn == 0:
            winning_moves.append((x, y, child_pn))
        else:
            # 顽抗着评分：对手要证明胜利的代价越大 = 我方翻盘可能性越大
            # OR 节点（攻方败）：对手是防方，对手胜 = dn=0，pn 越大 = 攻方证胜越难 → 选 max pn
            # AND 节点（防方败）：对手是攻方，对手胜 = pn=0，dn 越大 = 攻方证胜越难 → 选 max dn
            score = child_pn if is_or else child_dn
            if score > resist_score:
                resist_score = score
                resist_move = (x, y)

    if winning_moves:
        # 有胜着 → 选第一个（穷举后都等效）
        best_move = (winning_moves[0][0], winning_moves[0][1])
        best_move_certain = True
    elif resist_move:
        # 无胜着（我方必败）→ 选对手最难推进的手 = "最有可能翻盘"
        best_move = resist_move
        best_move_certain = False
    else:
        # 无合法手
        best_move = None

    return {
        "result": result,
        "move": {"x": best_move[0], "y": best_move[1], "certain": best_move_certain} if best_move else None,
    }
```

### 3.7 顽抗着逻辑详解

**场景**：落败方（被证明必输）轮到走棋。穷举已证明无论怎么走都输，但仍需选一手——目的是"让对手最容易下错"。

**原理**：TT 中每个子节点存了 `(pn, dn)`。对手要赢需要 `pn=0`（对手是攻方时）或 `dn=0`（对手是防方时）。**对手要赢的"代价"数值越大 = 变化越复杂 = 对手越容易在实战中犯错**。

```
顽抗着评分 = 对手要证明自己胜利的代价（pn 或 dn）

具体：
  我是 OR 节点（攻方，但我必败）：
    对手是防方，对手通过 dn=0 获胜
    我选子节点中 pn 最大的 → 意味着"从这个局面出发，攻方证胜最难"
    → 对手在这个分支中需要应对最多变化 → 最容易犯错

  我是 AND 节点（防方，但我必败）：
    对手是攻方，对手通过 pn=0 获胜
    我选子节点中 dn 最大的 → 意味着"从这个局面出发，防方证活最难"
    → 对手在这个分支中需要精确计算更多步 → 最容易犯错
```

**效果**：不是"随便下一手等死"，而是"选最复杂的分支，期待对手犯错"。这是围棋实战中"搅局"的精确数学化。

---

## 4. 预处理系统

### 4.1 目的

对任意大小区域（包括空位 > 30）的题目，命令行无限制穷举 df-pn，结果缓存到二进制文件 (.bin)。

### 4.2 多进程并行策略：根节点分裂

```
         root (OR/AND)
        / | | | \
      m1  m2 m3 m4 m5 ...   ← N 个根候选着法
      ↓   ↓  ↓  ↓  ↓
   worker1  worker2  ...     ← W 个进程各分一组子节点
```

- 主进程（coordinator）：生成根候选 → 分配到 W 个 worker → 等待完成 → 合并
- 每个 worker：对分到的子节点逐个跑 df-pn → 增量写入自己的排序 .bin 分片
- 完成后：k-way 归并分片 → 计算根 pn/dn → 写最终 .bin
- `num_workers = cpu_count() - 1`

### 4.3 增量存储（二进制，随算随存）

- 每 10K 新 TT 条目 → 追加写入临时 .bin 文件
- Worker 完成后将内存 TT 排序去重，写入最终有序 .bin
- 每个 worker 写自己的 `{job_id}_w{i}.bin` → 无锁竞争
- 完成后 k-way 归并到 `{job_id}.bin`

### 4.4 进度显示

预处理通过命令行启动和监控：

```bash
# 启动预处理（自动切换 pypy3）
python3 backend/precompute.py run <problem_id>

# 运行中实时输出：
  计算中 3:42  节点=   1,234,567  TT=     890,123   15,432 n/s  进程 3/3

# 另开终端查看状态（含每个 worker 明细）：
python3 backend/precompute.py status <problem_id>
```

- coordinator 每 2 秒汇总 worker 进度到 `{job_id}_progress.json`
- `run` 命令后台线程读取进度文件，单行刷新终端
- `status` 命令可在任意时刻查看：总进度 + 每个 worker 的节点数、TT、用时、PID、状态
- coordinator 监控 worker 异常退出（exitcode != 0），记录日志，合并时跳过失败 worker

### 4.5 solver 预处理模式配置

```python
solver = DfpnSolver(
    board, region,
    kill_targets=..., defend_targets=..., attacker_color=...,
    max_nodes=10**18,        # 等效无限
    max_time_ms=10**18,      # 等效无限
    reuse_tt=False,          # 不用跨请求缓存（有 SQLite）
    progress_callback=on_progress,  # 进度回调
)
```

预处理时仍保留**走法排序**（提子优先 > 邻石），因为它加快穷举收敛速度。其他在线优化全部不用。

---

## 5. 习题集系统

### 5.1 数据模型

```sql
-- backend/data/problems.db
CREATE TABLE problems (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT DEFAULT '',
    board_size        INTEGER DEFAULT 13,       -- 棋盘边长
    board_grid        TEXT NOT NULL,             -- JSON: 169 int（13×13）
    region_mask       TEXT NOT NULL,             -- JSON: 169 int (0/1) — 可落子区域
    kill_targets      TEXT DEFAULT '[]',         -- JSON: [[x,y], ...] — 杀目标代表子
    defend_targets    TEXT DEFAULT '[]',         -- JSON: [[x,y], ...] — 守目标代表子
    attacker_color    INTEGER DEFAULT 1,         -- 1=黑攻 -1=白攻
    precompute_status TEXT DEFAULT 'none',       -- none / running / done
    precompute_job_id TEXT DEFAULT NULL,
    created_at        TEXT NOT NULL,             -- ISO datetime
    updated_at        TEXT NOT NULL
);
```

**可落子区域和目标作为棋局的一部分持久化**：`region_mask`、`kill_targets`、`defend_targets` 都存在 problems 表中。用户保存习题时这些字段一起写入 DB；加载时一起恢复到前端。

### 5.2 页面结构

两视图 SPA（同一 index.html，JS 切换 `#list-view` / `#detail-view`）：

- **列表视图**：标题 + "+ 新建习题" + 习题卡片列表（名称、子数、区域格数、预处理状态）
- **详情视图**：`← 返回列表 | 保存 | 删除` + 棋盘界面（布局/区域/目标设置）

---

## 6. API 协议

### 6.1 习题 CRUD

| 端点 | 方法 | 请求 | 响应 |
|---|---|---|---|
| `/api/problems` | GET | — | `{problems: [{id, name, ...}, ...]}` |
| `/api/problems` | POST | `{name?, board_grid?}` | `{id, ...}` |
| `/api/problems/{id}` | GET | — | `{id, name, board_grid, region_mask, ...}` |
| `/api/problems/{id}` | PUT | `{name?, board_grid?, region_mask?, kill_targets?, ...}` | `{ok}` |
| `/api/problems/{id}` | DELETE | — | `{ok}` |

### 6.2 棋盘操作

| 端点 | 请求 | 响应 |
|---|---|---|
| `POST /api/play` | `{board, last_capture, x, y, color, kill_targets?, defend_targets?}` | `{ok, new_board, last_capture, captured_count, multi_status?}` |
| `POST /api/validate_target` | `{board, region, x, y}` | `{coord, color, libs, stones, eyes, group}` 或 `{error}` |
| `POST /api/legal_moves` | `{board, last_capture, region, color}` | `{moves: [[x,y], ...]}` |

**注**：board 为 169 个整数的扁平数组（13×13）；region 为 169 个 0/1 的数组。

### 6.3 求解（查表模式）

| 端点 | 请求 | 响应 |
|---|---|---|
| `POST /api/solve` | `{board, last_capture, region, target: {kill_targets_coords, defend_targets_coords, attacker_color}, turn, precompute_cache_id}` | `{result, move, multi_status}` |

预处理完成后的 solve **不跑 df-pn**，只从 .bin 缓存 mmap 查表。响应中没有 nodes/elapsed_ms/pn/dn（因为不搜索）。

### 6.4 预处理（命令行）

预处理不再通过 HTTP API 触发，改为命令行工具：

```bash
python3 backend/precompute.py list                    # 列出所有题目
python3 backend/precompute.py status <problem_id>     # 查看预处理状态（含 worker 明细）
python3 backend/precompute.py run <problem_id> [-w N] # 运行预处理（N=worker数）
```

`run` 命令自动检测并切换到 pypy3 执行（强制要求安装 pypy3）。

---

## 7. 文件结构

```
weiqi3/
├── frontend/
│   ├── index.html           两视图 SPA（列表 + 详情）
│   ├── style.css            中式古典审美
│   ├── board.js             ClientBoard：极简数据载体（无规则）
│   ├── region.js            落子区域掩码工具
│   ├── api.js               后端 API 客户端（fetch 包装）
│   ├── renderer.js          Canvas 渲染：棋子、序号、多色目标高亮
│   └── app.js               FSM 控制 + 决策日志 + 习题列表
│
├── backend/
│   ├── server.py            HTTP 服务（stdlib http.server）+ 所有路由
│   ├── board.py             Board 规则引擎 + UndoInfo + 撤销式落子
│   ├── eyes.py              严格真眼判定（绑定特定群）
│   ├── target.py            validate_target_stone
│   ├── solver.py            DfpnSolver（df-pn + TT + 多目标终止 + 进度回调）
│   ├── precompute.py        多进程并行预处理 + 二进制缓存 + 查表求解 + CLI 入口
│   ├── problems.py          习题 CRUD（problems.db 读写）
│   ├── data/                习题数据库目录
│   │   └── problems.db
│   ├── cache/               预处理缓存目录
│   │   ├── {job_id}.bin               每题的完整 TT（排序二进制，mmap 查询）
│   │   ├── {job_id}_w{i}.bin          worker 分片（合并后自动删除）
│   │   ├── {job_id}_w{i}_progress.json  worker 实时进度
│   │   ├── {job_id}_progress.json     汇总进度（含 worker 快照）
│   │   └── {job_id}_pids.json         worker PID 列表（完成后删除）
│   └── README.md
│
├── arch.md                  本文件（完整架构，可据此复刻）
└── README.md                快速启动
```

---

## 8. 代码规范

### 8.1 Python（backend/）

- **Python 3.11**
- **零 pip 依赖**，仅 stdlib（json, sqlite3, http.server, multiprocessing, time, os, uuid）
- 类型注解完整
- 中文 docstring 解释算法
- `__slots__` on Board, UndoInfo
- `try/finally` 保护所有 play/undo 配对

### 8.2 JavaScript（frontend/）

- 原生 JS（ES2020+），无框架/构建
- `const B=1, W=-1, E=0, BOARD_SIZE=13`
- `ClientBoard`：仅 grid + get/set，不含规则
- `API` 对象：每个方法返回 Promise
- `App` 类：FSM 状态机
- `BoardRenderer`：Canvas 绘制

### 8.3 CSS

- CSS Variables（`--ink-black`, `--jade`, `--vermillion`, `--parchment`）
- 中式古典审美
- Google Fonts：Ma Shan Zheng + Noto Serif SC
- 响应式 grid 布局

---

## 9. 关键数据结构

### 9.1 Board

```python
class Board:
    __slots__ = ("size", "grid", "last_capture")
    # size: 13
    # grid: List[int]，169 个元素，-1/0/1
    # last_capture: int，-1 表示无 ko
```

### 9.2 UndoInfo

```python
class UndoInfo:
    __slots__ = ("x", "y", "color", "captured", "prev_last_capture")
    # captured: List[Tuple[int, int, int]]  → (x, y, color) 被提子
```

### 9.3 DfpnSolver（仅预处理阶段使用）

```python
class DfpnSolver:
    board: Board
    region_mask: List[int]
    kill_targets: List[Tuple[int, int]]
    defend_targets: List[Tuple[int, int]]
    attacker_color: int
    tt: Dict[str, Tuple[int, int]]     # 转置表 key→(pn,dn)
    progress_callback: Optional[Callable]
```

### 9.4 多目标 targetInfo（前端 JS）

```javascript
this.targetInfo = {
    attacker_color: 1,
    kill_targets_coords: [[2,5], [4,5]],
    defend_targets_coords: [[1,5]],
};
this.killTargets = [{coord, color, group, libs, stones, eyes}, ...];
this.defendTargets = [...];
```

---

## 10. 前端 FSM 状态机

```
App.mode:
  'layout'      → 布局模式（放子/擦除）
  'region'      → 设置落子点
  'pick-target' → 多目标点选（白=杀红，黑=守蓝）
  'solve'       → 解题（手动落子 或 自动对弈）
```

```
layout ──[下一步]──→ region ──[下一步]──→ pick-target
                                           │
                                     点选目标 → [确认] → 保存 → 返回列表
                                     
                                     (命令行运行预处理后)

                                     列表 → [解题] → solve → [最优解] → autoplay（查表）

任何阶段 ──[返回布局]──→ layout
任何阶段 ──[← 返回列表]──→ list-view
```

---

## 11. 渲染器（renderer.js）

### 图层（从下到上）

1. 棋盘底色（木纹渐变）
2. 网格线 + 星位
3. 区域蒙版（暗化非可落子区）
4. 棋子（黑=径向渐变暗色，白=渐变亮色）
5. 多目标高亮（杀=红环+光晕，守=蓝环+光晕）+ 代表子三角
6. 落子序号（粗体白/黑字 + 描边）
7. 最后一手标记（圆点，有序号时跳过）
8. 鬼影石（hover 预览）

---

## 12. 启动

```bash
cd weiqi3
python3 backend/server.py
# 打开 http://localhost:8080/
# 换端口：PORT=9000 python3 backend/server.py
```

---

## 13. 测试方法

### 后端

```bash
cd backend
python3 -c "
from board import Board, BLACK
from solver import DfpnSolver
board = Board(13)
board.set(1, 4, -1); board.set(1, 5, -1)  # 白
board.set(0, 4, 1); board.set(0, 5, 1)    # 黑
mask = [0]*169
for i in range(169): mask[i] = 1
solver = DfpnSolver(board, mask, kill_targets=[(1,4)], attacker_color=1)
r = solver.solve(1)
print(r['result'], r['nodes'])
"
```

### 前端

1. 首页 → 新建习题 → 布局 → 设区域 → 选目标 → 确认 → 保存 → 返回列表
2. 命令行：`python3 backend/precompute.py run <id>` → 观察实时进度 → 完成
3. 进入习题 → 解题 → 最优解 → 秒出 → 自动对弈 → 终局
