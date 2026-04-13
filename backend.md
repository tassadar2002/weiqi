# 前后端分离后的操作纪要

本文记录 weiqi3 项目从单文件 JS 版（v1）迁移到 Python 后端 + 浏览器前端（v2）后，
所有后端相关的改动、问题与优化过程。目标读者：后续接手者。

---

## 1. 动机

v1 是浏览器端 JS 单文件，求解逻辑经过 4 轮极致优化后约 800ms 跑完一整局。
但问题：
- 求解阻塞浏览器主线程，复杂题目下 UI 卡顿
- JS 代码多轮优化后可读性显著下降（epoch scratch、wantGroup 懒分配等技巧）
- 用户明确要求"用 Python，重视可读性"

v2 目标：
- **用 Python 实现后端**，用 HTTP/JSON 与浏览器通信
- **优先可读性**，牺牲一定性能
- **零外部依赖**（不需要 `pip install`）
- **单文件启动**（`python3 backend/server.py` 即可）

---

## 2. 架构

```
┌──────────────────────┐         HTTP/JSON          ┌──────────────────────┐
│  浏览器 (frontend/)   │ ────────────────────────→  │  Python (backend/)   │
│                      │                              │                      │
│  - index.html        │  POST /api/play              │  - server.py         │
│  - style.css         │  POST /api/make_target       │  - board.py          │
│  - board.js  [极简]   │  POST /api/inspect_target   │  - eyes.py           │
│  - region.js         │  POST /api/legal_moves       │  - target.py         │
│  - api.js    [新]    │  POST /api/solve             │  - solver.py         │
│  - renderer.js       │ ←────────────────────────   │                      │
│  - app.js    [改写]   │                              │                      │
└──────────────────────┘                              └──────────────────────┘
       Canvas 渲染                                       df-pn 求解 + 规则
       事件 + 历史 + 日志                                  转置表 + 终止判定
```

**关键决策**：
- 后端**无状态** — 每个请求都包含完整棋盘状态，不需要 session / cookie
- 单端口同时托管 `frontend/` 静态资源 + `/api/*` 路由
- 使用 Python stdlib `http.server`，无 Flask/Django 等框架

---

## 3. 后端模块

| 文件 | 作用 | 行数 |
|---|---|---|
| `server.py` | HTTP 服务 + 5 个 API 端点 | ~280 |
| `board.py` | `Board` 规则引擎 + `UndoInfo` 撤销式落子 | ~270 |
| `eyes.py` | 严格真眼判定（绑定特定群） | ~100 |
| `target.py` | `make_target_from_stone`：手动目标构造 | ~70 |
| `solver.py` | `DfpnSolver`：df-pn 主循环 + vitalness + TT 缓存 | ~400 |
| `README.md` | 启动说明 + API 文档 | ~70 |

所有模块：
- 类型注解完整（`List[int]`, `Optional[UndoInfo]`, `Tuple[int, int]` 等）
- 中文 docstring 解释算法
- 无 `pip` 依赖，纯 stdlib

---

## 4. API 端点

所有接口均为 POST，请求/响应都是 JSON。

| 路径 | 用途 | 输入（关键字段） | 输出（关键字段） |
|---|---|---|---|
| `/api/play` | 落子验证 + 应用 | `board`, `x`, `y`, `color`, `target_coord` | `ok`, `new_board`, `captured_count`, `target_status` |
| `/api/make_target` | 构造目标群 | `board`, `region`, `x`, `y` | `target_coord`, `defender_color`, `attacker_color`, `group`, `target_libs`, `target_status` |
| `/api/inspect_target` | 查询目标当前状态 | `board`, `target_coord` | `captured`, `alive`, `libs`, `real_eyes`, `group` |
| `/api/legal_moves` | 枚举区域内合法着法 | `board`, `region`, `color` | `moves` |
| `/api/solve` | 运行 df-pn 求解器 | `board`, `region`, `target`, `turn` | `result`, `move`, `nodes`, `elapsed_ms`, `pn`, `dn`, `tt_reused` |

**棋盘序列化**：`board` 是 100 个整数的扁平数组（0=空，1=黑，-1=白），`region` 是 100 个 0/1 的 mask 数组。这样 JSON 体积小（~400 字节），解析快。

---

## 5. 前端改动

**删除**（逻辑搬到后端）：
- 原 `board.js`（规则引擎 + Zobrist + 撤销式）
- 原 `solver.js`（df-pn + TT + vitalness）
- 原 `target.js`、`eyes.js`

**新增**：
- `api.js`：5 个 `async` fetch 包装函数

**改写**：
- `board.js` → 极简 `ClientBoard`：只有 `grid`、`get`/`set`、`replaceFromArray` 等数据操作，无 Go 规则
- `app.js`：所有事件处理改为 `async` + `await API.*`
- `renderer.js::drawTargetHighlight`：不再本地计算 `groupAndLibs`，改用后端返回的 `targetGroupCoords`

**保持原样**：
- `index.html` / `style.css`
- `region.js`（纯前端 mask 工具）
- `renderer.js` 主要绘制逻辑

---

## 6. 性能演化

题目：用户预设 10×10 棋盘，20 格落子区域，目标白 4 子群 `(1,4)(1,5)(2,5)(1,6)`。
搜索规模：**106,538 个节点**。

### 6.1 首次移植：CPython 朴素实现

最初实现重点在可读性，使用 `list` 存 grid、`set()` 做 visited、`dataclass` 定义 UndoInfo。

| 指标 | 值 |
|---|---|
| 第一手 | ~14,500 ms |
| 完整 9 手 | ~18,000 ms |
| 节点速率 | ~7k nodes/s |

相比 v1 JS 优化版（800ms / 9 手）慢约 22×。

### 6.2 第一轮优化：PyPy

安装 PyPy 3.11，零代码改动，启动方式从 `python3` 改为 `pypy3`：

```bash
pypy3 backend/server.py
```

| 指标 | 值 | vs CPython |
|---|---|---|
| 第一手（预热后） | 3,413 ms | **4.3×** |
| 完整 9 手 | 5,868 ms | 3.1× |
| 节点速率 | 31.5k nodes/s | 4.5× |

**JIT 预热观察**：第 1 次比之后稳态多 10% 左右，几乎可忽略。

### 6.3 第二轮优化：跨请求 TT 缓存

**洞察**：自动对弈连续 9 手都是同一棵证明树的子局面。第一手建立的 TT 几乎
能直接命中后续所有查询。

**实现**（`solver.py`）：
- 新增模块级 `_TT_CACHE: Dict[(region_fp, target_coord, attacker), Dict[pos_key, (pn, dn)]]`
- `DfpnSolver.__init__(reuse_tt=True)`（默认开启）时，从缓存读取已有 TT 作为起点
- `solve()` 结束后把更新的 TT 回写到缓存
- 内存上限：单表 > 50 万条丢弃、缓存中 > 8 张表则 LRU 淘汰
- 工具函数：`clear_tt_cache()`、`tt_cache_stats()`

**性能**：

| 指标 | 无缓存 | 有缓存 | 倍数 |
|---|---|---|---|
| 第一手 | 3,441 ms | 3,451 ms | ~1×（无缓存可用） |
| 后续 8 手合计 | 2,427 ms | **7 ms** | **~347×** |
| 完整 9 手 | 5,868 ms | 3,458 ms | 1.7× |

**细节**：后续每一手的 `nodes` 只有 2-200 个 —— 进入 `_mid` 后 TT 立即命中，几乎什么都不用做。

### 6.4 第三轮优化：Tier-1 纯 Python 微优化

目标：消除 Python 解释器开销。

**改动**（全部在 `backend/board.py`）：

1. **`__slots__`** on `Board` 和 `UndoInfo`：减少 dict 分配，固化属性布局
2. **`UndoInfo` 从 dataclass 改为手写类**：避免 dataclass `__init__` 的 kwargs 开销
3. **`group_and_libs` 重写**：
   - `(x, y)` 元组 → 扁平索引 `i = y * size + x`
   - `set()` visited → `bytearray(100)`（作为布尔标志位）
   - `for nx, ny in self.neighbors(cx, cy)` 生成器 → 展开为北/南/西/东四段直接判断
   - 函数开头缓存 `size = self.size; grid = self.grid`
4. **`play_undoable` 内联 4 邻检查**：同上展开
5. **`undo()` 直接写 grid**：不调 `self.set()`

**性能**：

| 指标 | Tier-1 前 | Tier-1 后 | 倍数 |
|---|---|---|---|
| 第一手 | 3,413 ms | **1,584 ms** | **2.16×** |
| 节点速率 | 31.5k nodes/s | 67.2k nodes/s | 2.13× |

**代码量对比**：

| 函数 | 改前 | 改后 |
|---|---|---|
| `group_and_libs` | ~20 行 | ~60 行 |
| `play_undoable` | ~55 行 | ~75 行 |

代码变长，但每个分支都是机械展开的北/南/西/东 —— 可读性几乎不变。

### 6.5 累积对比

| 阶段 | 完整 9 手 | 第一手 | vs CPython | vs v1 JS |
|---|---|---|---|---|
| CPython 朴素 | ~18,000 ms | 14,500 ms | 1.0× | 22× 慢 |
| PyPy | 5,868 ms | 3,413 ms | 3.1× | 7× 慢 |
| PyPy + TT 缓存 | 3,458 ms | 3,451 ms | 5.2× | 4× 慢 |
| **PyPy + 缓存 + Tier-1** | **1,594 ms** | **1,584 ms** | **11.3×** | **2× 慢** |

距离 v1 JS 优化版（800ms）只差 2 倍，同时保留了**清晰的 Python 源码 + 前后端解耦**。

---

## 7. 启动 & 使用

```bash
cd /home/hanlixin/apps/weiqi/weiqi3
pypy3 backend/server.py
# 默认端口 8080；PORT=9000 pypy3 backend/server.py 换端口
```

浏览器打开 `http://localhost:8080/` 即可使用。

没有 PyPy 时仍可 `python3 backend/server.py` 启动，但会慢 4 倍。

---

## 8. 遇到的问题 & 解决

### 8.1 后端第一次启动 404
**现象**：`POST /api/make_target → 404 Not Found`

**原因**：有一个老的 server 进程在 8765 端口上运行（不属于 weiqi3），
误以为是我的新 server。

**解决**：用 `pgrep -af server.py` 确认，换个端口测试。

### 8.2 `board.py` 某些字段变成 None
**现象**：Tier-1 优化后，部分调用处访问 `u.group` 得到 None。

**原因**：把 `UndoInfo` 改成手写类时，字段名写错了（原先是 `group`，现在是
`captured`）。

**解决**：统一成 `captured`，并确认所有调用点都用新字段。

### 8.3 TT 缓存 key 是否需要包含 `last_capture`？
**问题**：TT 缓存的 key 是 `(region, target, attacker)`，但 TT 条目内部的
key 是 `(board_hash, turn, last_capture)`。两层 key 不能混淆。

**结论**：外层 key（`_TT_CACHE`）只区分"哪道题"，内层 key（单张 TT 里的
位置哈希）才需要包含 `last_capture` —— 不同 `last_capture` 下同一 board
是不同的 TT 条目（因为打劫禁着变了）。

当前实现正确。

### 8.4 跨请求 TT 的线程安全
**现象**：担心多线程访问 `_TT_CACHE` 有竞争。

**确认**：Python stdlib `HTTPServer` 是**单线程**的，请求串行处理。无需加锁。
若改用 `ThreadingHTTPServer`，需要 `threading.Lock` 包一下读写。

---

## 9. 算法正确性验证

每次优化后都跑同一个测试并对照结果：

```bash
pypy3 -c "
import sys; sys.path.insert(0,'backend')
from board import Board, BLACK
from target import make_target_from_stone
from solver import DfpnSolver
board = Board(10)
blacks = [(1,3),(2,3),(4,3),(3,4),(4,5),(5,5),(3,6),(3,7),(0,8),(1,8),(2,8),(1,9)]
whites = [(1,4),(1,5),(2,5),(1,6),(5,6),(0,7),(7,7),(3,8),(3,9)]
for x,y in blacks: board.set(x,y,BLACK)
for x,y in whites: board.set(x,y,-1)
mask = [0]*100
for x in range(0,4):
    for y in range(3,8): mask[y*10+x] = 1
target = make_target_from_stone(board, mask, 1, 4)
r = DfpnSolver(board, mask, (1,4), 1).solve(BLACK)
print(r['result'], r['move'], r['nodes'])
"
```

**期望**：`ATTACKER_WINS {'x': 0, 'y': 3, 'certain': True} 106538`

所有优化阶段的**节点数均为 106,538**，**第一手均为 B(0,3)**，**全局 9 手序列一致**。
这保证了优化全程没有改变搜索的正确性。

---

## 10. 还可以做的（按 ROI 排序）

以下都保留 Python（不需 Cython/C/Rust）：

1. **`capture-first` 走法排序 + killer move** (~50 行, 1.2-1.5×)
2. **持久化 TT 到磁盘** (~30 行, 第二次跑同题秒出)
3. **df-pn+** (Kishimoto 阈值改进，~50 行, 1.5-3×)
4. **形状库 / Benson 活棋检测** (~200 行, 常见形秒杀)
5. **Lambda search** (~300 行, 简单题 10-100×)

需要工具链：

6. **mypyc**（零代码改动编译，3-5×）
7. **Cython**（单文件 `.pyx`，5-15×）
8. **C 扩展 via ctypes**（重写 `group_and_libs` 为 C，10-30×）

架构：

9. **多进程根分裂**（2-4×）
10. **后台预计算 + SSE 进度推送**（UX 提升）

---

## 11. 代码总量

| 部分 | 行数 |
|---|---|
| `backend/board.py` | ~270 |
| `backend/eyes.py` | ~100 |
| `backend/target.py` | ~70 |
| `backend/solver.py` | ~400 |
| `backend/server.py` | ~280 |
| **后端合计** | **~1,120** |
| `frontend/api.js` | ~60 |
| `frontend/board.js` (ClientBoard) | ~50 |
| `frontend/app.js`（async 重写） | ~620 |
| `frontend/renderer.js`（微调） | ~250 |
| `frontend/region.js`, `index.html`, `style.css` | ~600 |
| **前端合计** | **~1,580** |
| **v2 总计** | **~2,700** |

对比 v1（~2,200 行）略多，但：
- 大部分新增是**后端的 Python 源码**（可读性高）
- 前端减负：删了规则引擎 + 求解器
- 新增了 `api.js`（通信层）和决策日志 UI

---

## 12. 一句话总结

**从"18 秒 CPython"到"1.6 秒稳定响应"，累计 11× 加速，零外部依赖，
代码可读性全程保留。**
