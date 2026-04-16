# weiqi3

13×13 围棋死活与对杀严格求解工具。前后端分离：
- **前端**：浏览器原生 HTML/JS/Canvas，零依赖
- **后端**：Python 3 stdlib，零依赖（不需要 pip install）

求解算法是 **df-pn (depth-first proof number search)**，对落子点 < 30 的题目能在
预处理阶段给出**数学上严格的最优解**。查询阶段读取已写好的二进制存储，亚毫秒返回。

---

## 启动

```bash
cd /home/hanlixin/apps/weiqi/weiqi3
python3 backend/server.py
```

控制台显示：

```
weiqi3 on http://127.0.0.1:8080/
  frontend: .../frontend
  problems: .../backend/data/problems.db
```

浏览器打开 `http://127.0.0.1:8080/` 即可使用。

需要换端口：`PORT=9000 python3 backend/server.py`

---

## 工作流程

1. **在浏览器中编题**：布局黑白子 → 设置落子区域 → 点选目标 → 保存
2. **离线预处理**（必需）：命令行跑 df-pn，把穷举结果写成二进制存储
   ```bash
   python3 backend/cli_precompute.py run <problem_id>
   ```
   （自动切换到 PyPy3 运行，必需安装 `pypy3`。）
3. **在浏览器中解题**：点 `最优解`，后端 mmap 二进制存储即时返回最优手

没有预处理结果就无法查询 —— `/api/solve` 会直接报错返回，不会在线求解。

---

## 目录结构

```
weiqi3/
├── frontend/                 静态资源 + 浏览器逻辑
│   ├── index.html            两视图 SPA（习题列表 / 解题）
│   ├── style.css
│   ├── app.js                FSM + 事件处理 + 决策日志
│   ├── board.js              ClientBoard：极简数据载体
│   ├── region.js             落子区域掩码工具
│   ├── api.js                后端 API 客户端（fetch 包装）
│   └── renderer.js           Canvas 渲染
│
├── backend/                  Python 求解服务
│   ├── server.py             HTTP 服务（stdlib http.server）
│   ├── board.py              规则引擎 + 撤销式落子
│   ├── eyes.py               严格真眼判定
│   ├── target.py             目标群校验
│   ├── problems.py           习题 CRUD（SQLite）
│   ├── test_solver.py        求解器单元测试
│   ├── cli_precompute.py     预处理命令行入口
│   ├── action/               CLI 子命令（list/status/run）
│   ├── precompute/           核心预处理包
│   │   ├── solver.py         DfpnSolver
│   │   ├── binstore.py       二进制存储 + DiskTT + solve_from_store
│   │   ├── coordinator.py    多进程调度 + 崩溃恢复
│   │   └── worker.py         无状态 worker
│   ├── data/                 SQLite 习题库
│   └── store/                每题预处理结果目录（.bin / progress json）
│
├── doc/                      设计文档
└── README.md                 本文件
```

---

## CLI 命令

```bash
python3 backend/cli_precompute.py list               # 列出所有习题
python3 backend/cli_precompute.py status <id>        # 查看预处理状态 + worker/根着明细
python3 backend/cli_precompute.py run <id>           # 运行预处理（自动切 pypy3）
python3 backend/cli_precompute.py run <id> -w 4      # 指定 worker 数
```

`run` 支持断点续传：中断后再次 `run` 复用同一 `job_id`，已完成的根着跳过。

---

## API 端点

所有接口返回 JSON。

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/problems` | 列出习题 |
| GET | `/api/problems/{id}` | 读取单个习题 |
| POST | `/api/problems` | 创建习题 |
| PUT | `/api/problems/{id}` | 更新习题 |
| DELETE | `/api/problems/{id}` | 删除习题（连同预处理结果） |
| POST | `/api/play` | 校验并落子，返回新棋盘与目标状态 |
| POST | `/api/validate_target` | 校验用户点击的棋子能否作目标 |
| POST | `/api/legal_moves` | 枚举区域内某一方的合法落子 |
| POST | `/api/solve` | 查询预处理结果，返回最优手 |

详见 `backend/server.py` 各 `_h_*` 方法。

---

## 设计参考

- `doc/arch.md` — 架构总览（推荐首读）
- `doc/algorithms.md` — df-pn 理论与实现
- `doc/db.md` — 二进制存储格式
- `doc/pre_compute01.md`, `pre_compute02.md` — 多进程预处理设计
- `doc/doc.md` — 项目历史演进
