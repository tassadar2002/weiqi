# weiqi3 Backend

Python 3 stdlib only. No `pip install` needed.

## 启动

```bash
python3 server.py
# 默认端口 8080；用 PORT=9000 python3 server.py 换端口
```

## 模块

- **`server.py`** - HTTP 服务（静态托管 + JSON API + 预处理结果查询）
- **`board.py`** - `Board` 类：13×13 棋盘、规则引擎、撤销式落子、Zobrist 增量哈希
- **`eyes.py`** - 严格真眼判定（绑定特定群的 group_set）
- **`target.py`** - `validate_target_stone`：校验用户点击的棋子是否可作目标
- **`problems.py`** - SQLite 习题 CRUD
- **`precompute/`** - 预处理核心包
  - `solver.py` — `DfpnSolver`：df-pn 主循环
  - `binstore.py` — 二进制存储格式、`BinStore` mmap 查表、`solve_from_store`、k-way 合并、`DiskTT`
  - `coordinator.py` — 多进程调度、事件循环、崩溃恢复（可用 `pypy3 -m precompute.coordinator <cfg>` 直接启动）
  - `worker.py` — 无状态 worker：从任务队列取根着并解
- **`cli_precompute.py`** - CLI 入口；子命令分派到 `action/` 下的 Action 类
- **`action/`** - `ListAction` / `StatusAction` / `RunAction`
- **`test_solver.py`** - 求解器单元测试

## 直接使用 solver（不通过 HTTP）

```python
from board import Board, BLACK
from precompute.solver import DfpnSolver

board = Board(13)
# ... 摆子 ...

mask = [1] * 169  # 全盘可落子
solver = DfpnSolver(
    board, mask,
    attacker_color=BLACK,
    kill_targets=[(1, 4)],
    defend_targets=[],
)
result = solver.solve(BLACK)
print(result)
# {'result': 'ATTACKER_WINS', 'move': {'x': ..., 'y': ..., 'certain': True},
#  'nodes': ..., 'elapsed_ms': ..., 'pn': 0, 'dn': ..., 'timed_out': False}
```

实际产品流程不走这条路径；HTTP 层通过 `precompute/binstore.py::solve_from_store`
查询离线写好的 `.bin`，不会在线跑 df-pn。

## 设计原则

- 零依赖：仅用 Python stdlib；前端同原则
- 无状态 HTTP：每次请求携带完整棋盘，服务器不存会话状态
- 预处理与查表分离：`DfpnSolver` 只在 Worker 进程用；在线解题只查 `BinStore`
- 预处理必须 PyPy3：`cli_precompute.py run` 自动 `execv` 到 pypy3

参考：
- df-pn 算法 → `precompute/solver.py` 顶部 docstring
- 撤销式落子 → `board.py::play_undoable` / `undo`
- 严格真眼定义 → `eyes.py` 顶部 docstring
- API 协议 → `server.py` 各 `_h_*` 方法
- 多进程调度 → `precompute/coordinator.py` 顶部 docstring
