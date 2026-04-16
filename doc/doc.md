# weiqi3 — 围棋死活与对杀严格求解器

> ⚠️ **历史文档**。本文记录 v1（单文件 JS）→ v2（前后端分离）的演进过程。
> 文中提到的 10×10 棋盘、单体 `solver.py`、请求内在线求解等描述均已过时。
> 当前状态请参考：`README.md` / `CLAUDE.md` / `doc/arch.md`。
> 当前项目为 **13×13 棋盘 + 预处理离线求解 + 在线查表**。

10×10 围棋死活/对杀题目的严格求解工具。

**架构（v2）**：前后端分离 — 浏览器前端 + Python 后端，零外部依赖（无需 npm/pip）。

---

## 1. 项目背景与需求

### 1.1 初始需求

- 10×10 棋盘死活/对杀解题工具
- 用户为黑棋，系统为白棋
- 三阶段流程：布局棋子 → 设置可落子区域 → 解题对弈
- 用户预设一道局面：约 21 颗黑白子，区域约 35 格
- "落子点不会太多（小于 30），不要使用 MCTS，使用 minimax"

### 1.2 需求演化

整个项目经历了多次需求转向，每一次都改变了核心算法或界面：

| 阶段 | 用户要求 | 影响 |
|---|---|---|
| 初始 | 用 minimax + alpha-beta，10×10 棋盘 | 实现 negamax 框架 |
| ~~优化~~ | "时间长一些不重要，结果要准确" | 加深搜索 / 加大分支 |
| **关键转折** | "可落子点 < 30 时必须得到唯一最优解，不能容忍错误" | **算法必须改为严格求解（df-pn）** |
| 解题流程 | 落子要播放到棋盘真正终局（提子或双眼） | autoplay 加入"顽抗着"和终局检测 |
| 棋谱可读性 | 棋子上显示步数序号 | renderer 加 `drawMoveNumbers` |
| 决策透明度 | 把所有决策依据显示成列表 | 决策日志 UI |
| 选目标 | 删除自动选目标，只保留手动点选 | 强制用户点击棋子指定目标 |
| 目标可视化 | 目标群打上明显标识 | 红圈 + 红三角 |
| 估算响应时间 | 显示可落子点数 | 区域统计行 |
| 性能 | 询问能否多线程 / 详解优化方案 | 单线程优化（撤销式 + Zobrist + 懒分配） |

---

## 2. 整体设计

### 2.1 架构

```
┌────────────────────────────────────────────────────┐
│ index.html  (UI 骨架，三段式侧栏)                    │
├────────────────────────────────────────────────────┤
│ style.css   (中式古典审美，朱红/玉绿/羊皮黄)          │
├────────────────────────────────────────────────────┤
│ board.js    SimBoard：规则引擎、Zobrist、撤销式落子   │
│ region.js   落子区域掩码工具                          │
│ eyes.js     严格真眼判定（绑定 groupMask）            │
│ target.js   makeTargetFromStone：手动目标构造         │
│ solver.js   df-pn 主循环 + vitalness 破平 + 终止判定 │
│ renderer.js BoardRenderer：Canvas 绘制 + 序号 + 高亮  │
│ app.js      App FSM：layout / region / pick-target / │
│             solve；落子记录；决策日志；自动对弈       │
└────────────────────────────────────────────────────┘
```

### 2.2 状态机

```
┌────────┐ 下一步 ┌──────┐ 下一步 ┌────────────┐ 自动 ┌────────┐
│ 布局   │──────→│ 区域 │──────→│ 解题（默认）│─────→│点选目标 │
└────────┘       └──────┘       │             │      └────────┘
   ↑                ↑           │ 等待手动点选 │
   └────────────────┴───────────└─────────────┘
        返回布局
```

进入"解题"时**强制进入"点选目标"子模式**（用户必须点击一颗棋子才能开始求解）。

### 2.3 关键数据结构

- **棋盘**：`Int8Array(100)`，B=1, W=−1, E=0
- **落子区域**：`Uint8Array(100)`，1=可落子, 0=墙
- **Zobrist 哈希**：两个 `Uint32`（高 32 + 低 32 位），增量维护
- **TT 转置表**：`Map<string, {pn, dn}>`，key = `zhashHi:zhashLo:turn:lastCapture`
- **目标信息**：`{targetCoord, defenderColor, attackerColor, targetStones, targetLibs, targetEyes, candidates, userPicked}`

---

## 3. 算法演进：从 minimax 到 df-pn

### 3.1 第一版：Minimax + Alpha-Beta（已废弃）

最初按需求实现了 negamax + alpha-beta，外加：
- 启发式 `evaluate()`（真眼数、气差、做眼空间、stones 等加权求和）
- 死活/对杀分类器（影响权重切换）
- 走法排序（提子 > 叫吃 > 救己 > 紧邻）
- 转置表 + 迭代加深
- 分支因子上限 15
- 最大深度 8

**深度 6 ~ 1 秒，深度 8 ~ 14 秒。**

### 3.2 转折：用户要求"不能容忍错误"

minimax + 启发式评估**根本上无法保证最优**，原因有三：

1. **分支因子上限漏掉最佳手**：排序启发式不可能保证最佳手永远在前 N 个
2. **静态评估是近似**：在截断深度上的评分是经验值，不是真相
3. **死活问题的"最优解"是布尔真相**：能不能活，是 是/否 问题，不是 +32 分

诚实结论：**minimax 数学上无法满足"严格最优"要求。**

### 3.3 第二版：Proof Number Search (df-pn)

**df-pn**（Depth-First Proof-Number search，Kishimoto 2002）是计算机围棋死活题的标准解法，对应 `tsumego.js` 等开源求解器。

核心思想：

不打分，直接证明布尔命题。维护两个数：
- **pn (proof number)**：证明"攻方胜"所需的最小代价
- **dn (disproof number)**：证明"防方胜"所需的最小代价

搜索树是 AND-OR 树：
- **OR 节点**（攻方行棋）：`pn = min(子.pn)`，`dn = sum(子.dn)`
- **AND 节点**（防方行棋）：`pn = sum(子.pn)`，`dn = min(子.dn)`

终止条件**精确**：
- 目标群代表子被提 → `pn=0, dn=∞`（攻方胜）
- 目标群有 ≥ 2 个真眼 → `pn=∞, dn=0`（防方胜）
- 否则继续展开

每次展开都选 `pn` 最小（OR）或 `dn` 最小（AND）的子节点，最像"最易证明的方向"。

**强保证**：
- 搜索时不剪枝，不依赖启发式估值
- 在预算内若搜索完成，结论数学上正确
- 搜不完时返回 `UNPROVEN`，**绝不猜测**

---

## 4. 关键算法详解

### 4.1 df-pn 主循环（`solver.js::_mid`）

```
function _mid(board, turn, depth, thPn, thDn):
  if depth > MAX_DEPTH: 标记 UNKNOWN, return
  
  term = _terminal(board)
  if term == 'ATK': 存 (0, INF), return
  if term == 'DEF': 存 (INF, 0), return
  
  kids = 生成所有合法走法（按 vitalness 降序排序）
  if 防方且无走法: 加 pass 子节点
  
  loop:
    for each kid in kids:
      play kid
      读 TT[kid 的局面]
      undo
      聚合 pn/dn
    存 TT[当前局面] = (pn, dn)
    if pn === 0 or dn === 0: return（已证明/反证）
    if pn >= thPn or dn >= thDn: return（达到阈值）
    
    play best kid
    递归 _mid(深度+1, 收紧的阈值)
    undo
```

### 4.2 严格真眼判定（`eyes.js`）

一个空点 P 是 color 方真眼，当且仅当：
1. P 为空
2. P 的 4 个正交邻**全部属于 target group**（不仅同色）
3. 对角检测：
   - P 在边/角（< 4 个正交邻）：所有存在的对角必须为 color
   - P 在内部：4 对角中 ≥ 3 个为 color

注意：**正交邻必须是同一连通块**，否则会把"4 个独立同色子围出的点"误判为眼。

### 4.3 vitalness 棋形破平（`solver.js::_vitalness`）

df-pn 的提取器只能在已证明的胜着中按"最快证明"破平，**不感知棋形**。多个等效杀着时可能返回非"中心要点"的胜着。

解决：定义 `vitalness(x, y) = 该点相邻方位中属于根目标群气的数量`。

边线板四的中心点同时紧两个目标气，vitalness=2，会被优先返回。

### 4.4 穷举根证明

df-pn 找到第一个胜着就立即返回，其他子节点的 TT 仍是 unknown。这导致 vitalness 破平失效（其他胜着没被证明）。

修复：在 `solveDfpn` 主调结束后，对所有未证明的根子节点再次调用 `_mid` 直到证完，让 `_extractBestMove` 能在所有真胜着中按 vitalness 选最好的。

### 4.5 顽抗着（lost-but-not-dead）

落败方在 df-pn 中会找不到"必胜手"。原版直接 `return null` → 游戏停止。

修复 4 级回退：
1. 必胜非 pass 着（按 vitalness）
2. 必胜 pass（仅防方）
3. **顽抗着**：选对手胜利指标（pn/dn）最大的，即"对手最难推进"的手
4. 任何合法手（兜底）

这样自动对弈一定能下到棋盘真正终局（提子或双眼），不会半路停下。

---

## 5. 重要决策与权衡

### 5.1 自动选目标 vs 手动点选

**早期方案**：`selectTarget` 用启发式排序（区内子数 → 气 → 眼）自动选目标群。

**问题**：启发式总会有反例。用户在某道题上发现自动选错了。

**最终方案**：完全删除自动选择，**强制用户点击棋子指定目标**。点中黑子 → 黑方为防方；点中白子 → 白方为防方。攻防角色由颜色推导。

**权衡**：少一步自动化，多一份准确性。对严格求解工具来说值得。

### 5.2 区域限制 vs 全盘搜索

只搜索"落子区域"内的合法走法，区域外的子作为不可触碰的"墙"。

**好处**：
- 限制搜索空间，让 df-pn 在合理时间内收敛
- 用户可以聚焦在题目核心区域

**风险**：
- 区域设小了，关键的紧气点（如 (0,3)）会被排除，导致目标"假活"
- 区域设大了，搜索爆炸

我们曾遇到过这个 bug：区域 `[0-3, 4-7]` 16 格时，目标白群在区域内 1 气，但唯一能紧的 `(0,3)` 在区域外，solver 报告 DEFENDER_WINS，实际上是"区域之外没气"的假象。修复：默认区域改为 `[0-3, 3-7]` 20 格。

### 5.3 双活 / seki 的处理

df-pn 的"防方胜 = 2 真眼"无法表达双活。

**简化方案**：防方允许 pass。如果防方 pass 后攻方也无法证明胜，说明谁都不能动手 → 防方胜（隐式包含双活）。

不严格区分"真活"和"双活"——对解题工具来说够用。

### 5.4 简单 ko 还是完整 superko

**选择简单 ko**：仅禁止"刚被提的位置立即回提"。`SimBoard.lastCapture` 跟踪上一手的提子点。

不实现 positional superko（路径全局哈希集）。代价：长生劫等罕见循环可能被路径深度上限（60 ply）截断为 UNPROVEN。对常规死活题完全够用。

---

## 6. 性能优化历程

### 6.1 起点

初版 df-pn 实现：
- `board.clone()` 每步分配新 `Int8Array`
- `groupAndLibs` 用 `Set` 做 visited / libs 集合
- `hash()` 拼 100 字符字符串
- `[[x,y],...]` 嵌套数组存储 group

**预设题目第一手 1880ms / 106K 节点 / ~56k nodes/s**

### 6.2 第一轮：Zobrist + 撤销式落子

- **Zobrist 哈希**：预计算 `ZOBRIST_HI/LO[200]` 表，`SimBoard.set` 异或更新两个 `Uint32`，`hash()` 拼接两个 base-36 字符串（O(1)）
- **撤销式落子**：`playUndoable(x, y, color)` 返回 `{x, y, color, captured, prevLastCapture}` undo 句柄；`undo(u)` 原地回滚
- `solver.js` 删除所有 `clone()`，改用 `_playKid` / `_undoKid` 在递归前后就地变化棋盘
- `legalMovesInRegion` 也改为 `playUndoable + undo`，避免每个候选都克隆

**结果：1880 → 1540ms（1.22×）**

实测发现远低于预期。`groupAndLibs` 每秒数百万次的 Set 操作是新的瓶颈。

### 6.3 第二轮：`groupAndLibs` 重写

- 用模块级 `Int32Array(100) _GAL_VISITED + _GAL_LIB_MARK` 加 epoch 计数器代替 `new Set()`
- 重置 epoch 时无需清空数组，只要 `_galEpoch++`
- 返回 `groupMask: Uint8Array` + `libArray: number[]` 代替 `groupSet/libSet`
- `eyes.js::isEyeOfGroup` 改为查 `groupMask[ni]`（O(1) 布尔），展开 4 邻循环避免分配

**结果：1540 → 1083ms（累计 1.74×）**

### 6.4 第三轮：快速 `_terminal`

把 flood fill + 真眼检测合并到 `solver.js::_terminal` 内部，使用独立的 `_TERMVIS / _TERMLIB` epoch 数组。避免 `getTargetGroup` 和 `countGroupRealEyes` 的对象/数组分配。

**结果：1083 → 1078ms（几乎无变化）**

意外发现：节点级 `_terminal` 调用占比并不大，大部分 `groupAndLibs` 调用来自 `play()` 内部的捕获判定。

### 6.5 第四轮：`wantGroup=false` 懒分配（**最大加速**）

`groupAndLibs` 接受新参数 `wantGroup`：
- `false`（默认在 play 内部）：不构建 `[[x,y],...]` 嵌套数组，不分配 `groupMask`
- `true`（仅用户接口）：完整构建

`playUndoable` 改为两阶段：
1. 用 `wantGroup=false` 探测对方群 libs
2. 仅当 `libs === 0` 时，再 `wantGroup=true` 重做一次以拿到要提的子坐标

提子是稀有事件，所以 99% 的 `groupAndLibs` 调用现在不再分配嵌套数组。

ko 检测原本依赖 `own.group.length === 1`，改为内联 4 邻布尔检查（直接看 4 个相邻位置是否有己方棋子）。

**结果：1078 → 488ms（累计 3.85×）**

### 6.6 累积对比

| 阶段 | 第一手耗时 | 累计加速 | 节点速率 |
|---|---|---|---|
| 原始 (Set + clone) | 1880 ms | 1.0× | 56k nodes/s |
| Zobrist + 撤销式落子 | 1540 ms | 1.22× | 69k nodes/s |
| 重写 groupAndLibs | 1083 ms | 1.74× | 98k nodes/s |
| 快速 _terminal | 1078 ms | 1.74× | 99k nodes/s |
| **wantGroup 懒分配** | **488 ms** | **3.85×** | **217k nodes/s** |

完整 9 手对局总耗时从 ~3100ms 降到 **~800ms**，结果与之前完全一致。

### 6.7 关于"为什么不上多线程"

用户问过多线程加速。诚实回答：
- df-pn 难以有效并行化（best-first 选子依赖 TT 全局状态，并行版本需要 virtual loss + 延迟 TT 写入等额外机制，典型加速只 2–3×）
- 单线程内存优化（已完成）+ bitboard 重写（未做）的 ROI 远高于多线程
- 单用户本地工具加 Node.js 后端只增加部署/网络复杂度，无对应收益

---

## 7. 已知限制

| 项 | 说明 |
|---|---|
| 棋盘大小 | 固定 10×10（修改 `BOARD_SIZE` 即可，但 hash 表大小和星位需同步） |
| 棋盘表示 | `Int8Array`，未升级到 bitboard。bitboard 还能再快 3–10× |
| 多目标 | 只支持单群目标。多群"同时杀掉两条龙"等需要扩展 `_terminal` 语义 |
| Superko | 仅检测简单一步 ko，不维护路径全局哈希。复杂打劫题可能 UNPROVEN |
| Seki 区分 | 用 pass + 攻方无法证胜 = 防方胜的简化方案。不区分真活与双活 |
| 真眼判定 | 经典对角规则。某些边界 case（如假眼链可强行做眼）可能错判 |
| 浏览器单线程 | 求解期间 UI 阻塞。复杂题目应先在 region 缩小后再算 |

---

## 8. 文件清单

| 文件 | 作用 | 行数（约） |
|---|---|---|
| `index.html` | UI 骨架 + 脚本加载 | 100 |
| `style.css` | 样式（中式古典） | 500 |
| `board.js` | SimBoard：规则 + Zobrist + 撤销式 | 250 |
| `region.js` | 区域掩码工具 | 45 |
| `eyes.js` | 严格真眼判定 | 100 |
| `target.js` | 手动目标构造 | 70 |
| `solver.js` | df-pn 求解器 | 400 |
| `renderer.js` | Canvas 渲染 + 序号 + 目标高亮 | 230 |
| `app.js` | FSM + 决策日志 + 自动对弈 | 500 |

总代码约 **2200 行**，零外部依赖。

---

## 9. 使用方法

### 9.1 启动

```bash
cd /home/hanlixin/apps/weiqi/weiqi3
python3 -m http.server 8080
# 浏览器打开 http://localhost:8080/
```

### 9.2 流程

1. **① 布局模式**：在棋盘上点击放置黑/白子，或用"擦除"删除。点 `下一步：设置落子点`。
2. **② 设置落子点**：点击单元切换"可落子/墙"。预设默认区域为 `(0,3)–(3,7)`。点 `下一步：解题`。
3. **③ 解题**：自动进入"点选目标"子模式，提示"点击任意棋子设为目标"。
   - 点黑子 → 黑方为防方
   - 点白子 → 白方为防方
   - 目标群被红圈高亮，代表子带红三角
4. 落子方式：
   - **手动**：点击落子区域内任意空点（必须是黑棋的回合）
   - **自动对弈**：点 `最优解` 按钮，每 2 秒一手自动播放到终局
5. **决策日志**：右下角"决策与落子记录"实时显示每手的攻/防角色、必胜/顽抗标签、节点数、耗时

### 9.3 决策可见性

每次搜索后，"③ 解题" 卡片显示：

```
目标：白@(1,4) 4子/7气 · 攻方：白
区域 20 格 · 空位 N · 合法 黑M/白N        ← 用于估算下一步耗时
上一手：攻方必胜 · 106,538 节点 · 0.5s     ← 上次搜索元数据
```

每手在棋子上叠加序号（粗体白/黑字 + 描边）。

---

## 11. 前后端分离重构（v2）

### 11.1 动机

v1 版本所有逻辑都在浏览器 JS 中，单文件可运行但有以下问题：
- 求解逻辑与 UI 耦合，长求解会卡住浏览器主线程
- JS 代码经历多轮极致优化（Zobrist、撤销式、epoch scratch、wantGroup 懒分配），可读性下降
- 用户希望"用 Python，重视可读性"

### 11.2 架构

```
┌──────────────────────┐         HTTP/JSON          ┌──────────────────────┐
│  浏览器 (frontend/)   │ ────────────────────────→  │  Python (backend/)   │
│                      │                              │                      │
│  - index.html        │  POST /api/play              │  - server.py         │
│  - style.css         │  POST /api/make_target       │  - board.py          │
│  - board.js  [极简]   │  POST /api/inspect_target   │  - eyes.py           │
│  - region.js         │  POST /api/legal_moves       │  - target.py         │
│  - api.js   [新]     │  POST /api/solve             │  - solver.py         │
│  - renderer.js       │ ←────────────────────────   │                      │
│  - app.js   [改写]    │                              │                      │
└──────────────────────┘                              └──────────────────────┘
       Canvas 渲染                                       df-pn 求解 + 规则
       事件 + 历史 + 日志                                  转置表 + 终止判定
```

**通信协议**：纯 JSON 请求/响应。每次请求包含完整棋盘状态（100 整数数组），后端无会话状态。

**单端口**：`server.py` 同时托管 `frontend/` 静态文件 + `/api/*` 路由。
访问 `http://localhost:8080/` 即可。

### 11.3 后端模块（Python）

按可读性优先重写，未做 JS 版的极致优化：

| 文件 | 行数 | 内容 |
|---|---|---|
| `board.py` | ~180 | `Board` 类（dataclass UndoInfo、撤销式 play、清晰的 group_and_libs） |
| `eyes.py` | ~80 | 严格真眼判定 |
| `target.py` | ~60 | 手动目标构造 |
| `solver.py` | ~330 | `DfpnSolver` 类（df-pn 主循环 + vitalness + 顽抗着 + 穷举根证明） |
| `server.py` | ~270 | http.server + 5 个 API 端点 |

总后端代码约 **920 行**，全部基于 stdlib，无 `pip install`。

### 11.4 前端变更

**删除**：
- `solver.js`、`eyes.js`、`target.js` — 全部移到后端
- `board.js` 简化为 `ClientBoard`（仅渲染数据载体，无规则）

**新增**：
- `api.js`：`API.play / makeTarget / inspectTarget / legalMoves / solve` 5 个 fetch 包装

**改写**：
- `app.js`：所有事件处理改为 `async` + `await API.*`；落子、目标查询、求解全部由后端响应推动
- `renderer.js`：`drawTargetHighlight` 不再调 `groupAndLibs`，改用后端返回的 `targetGroupCoords`

### 11.5 性能对比

| 指标 | v1 (JS, 优化后) | v2 (Python, 可读) | 倍数 |
|---|---|---|---|
| 第一手耗时 | 488 ms | ~10,400 ms | ~21× 慢 |
| 节点速率 | 217k nodes/s | ~10k nodes/s | ~21× 慢 |
| 完整 9 手对局 | ~800 ms | ~18 s | ~22× 慢 |

**结论**：Python 版的速度大约是优化后 JS 的 1/20。对于交互响应来说，第一手 10 秒
是"明显的等待"但不至于不可用。后续步骤都在秒级或亚秒级（搜索树大幅缩小 + TT 缓存）。

如果嫌慢的可能优化方向（按 ROI 排序）：
1. **缩小问题规模**：让用户调小落子区域 / 选小目标
2. **PyPy**：换 JIT 解释器，预计 5–10× 加速，无需改代码
3. **C 扩展**：把 `board.py::group_and_libs` 用 C 重写，预计再 5× 加速
4. **多进程根分裂**：每个 worker 拿一组根候选并行求解，预计 2–4× 加速

### 11.6 正确性

后端的 df-pn 与 v1 的 JS 版**算法完全等价**——同样的预设、同样的目标，得到的：
- 节点数完全一致（106,538）
- 第一手完全一致（B(0,3)）
- 完整 9 手序列完全一致

测试方式：
```bash
cd backend && python3 -c "
from board import Board, BLACK
from target import make_target_from_stone
from solver import DfpnSolver
# ... 设置预设 ...
solver = DfpnSolver(board, mask, (1,4), -1)
print(solver.solve(BLACK))
"
```

### 11.7 取舍

| 项 | v1 (JS 单文件) | v2 (前后端) |
|---|---|---|
| 部署 | 双击 .html 即可 | 需要 `python3 backend/server.py` |
| 可读性 | 低（多轮优化痕迹） | 高（dataclass + 类型注解） |
| 速度 | 快 | 慢 ~20× |
| 算法正确性 | 一致 | 一致 |
| 跨语言学习价值 | JS only | Python + JS 双端 |
| UI 阻塞 | 求解时浏览器卡顿 | 求解在后端，浏览器仍响应 |

---

## 10. 总结

**核心成就**：从最初的 minimax + 启发式（不可证明最优）演化到严格的 df-pn（数学上保证最优），并通过四轮单线程优化把响应时间从 1.9 秒压到 0.5 秒以下，全部保持算法正确性不变。

**核心权衡**：选择 df-pn 而非 minimax 是为了"严格最优"的承诺；选择手动目标而非自动选择是为了"零启发式"的承诺；选择简单 ko / 单群目标是为了"代码可维护"的边界。

**未走的路**：bitboard 重写、Web Workers 并行、多目标支持。这些都属于"更激进的工程投入"区间，当前性能已能满足常规交互需求，留作未来可选项。
