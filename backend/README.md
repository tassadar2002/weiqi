# weiqi3 Backend

Python 3 stdlib only. No `pip install` needed.

## 启动

```bash
python3 server.py
# 默认端口 8080；用 PORT=9000 python3 server.py 换端口
```

## 模块

- **`board.py`** - `Board` 类：10×10 棋盘、规则引擎、撤销式落子
- **`eyes.py`** - 严格真眼判定（绑定特定群的 group_set）
- **`target.py`** - `make_target_from_stone`：把用户点击转成目标对象
- **`solver.py`** - `DfpnSolver`：df-pn 求解器
- **`server.py`** - HTTP 服务（静态托管 + JSON API）

## 直接使用 solver（不通过 HTTP）

```python
from board import Board, BLACK, WHITE
from target import make_target_from_stone
from solver import DfpnSolver

board = Board(10)
# ... 摆子 ...

mask = [0]*100
for x in range(0,4):
    for y in range(3,8): mask[y*10+x] = 1

target = make_target_from_stone(board, mask, 1, 4)
solver = DfpnSolver(
    board, mask,
    target_coord=tuple(target['target_coord']),
    attacker_color=target['attacker_color'],
    max_time_ms=120000,
)
result = solver.solve(BLACK)
print(result)
# {'result': 'ATTACKER_WINS', 'move': {'x': 0, 'y': 3, 'certain': True},
#  'nodes': 106538, 'elapsed_ms': ~10000, 'pn': 0, 'dn': 1000000000, 'timed_out': False}
```

## 设计原则

可读性优先于性能。Python 单线程速度对解题题目尺寸够用。

具体参考：
- df-pn 算法 → `solver.py` 顶部 docstring
- 撤销式落子 → `board.py::play_undoable` / `undo`
- 严格真眼定义 → `eyes.py` 顶部 docstring
- API 协议 → `server.py` 各 `_handle_*` 方法
