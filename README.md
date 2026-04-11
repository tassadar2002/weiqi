# weiqi3

10×10 围棋死活与对杀严格求解工具。前后端分离：
- **前端**：浏览器原生 HTML/JS/Canvas，零依赖
- **后端**：Python 3 stdlib，零依赖（不需要 pip install）

求解算法是 **df-pn (depth-first proof number search)**，对落子点 < 30 的题目能在
节点/时间预算内给出**数学上严格的最优解**。

---

## 启动

```bash
cd /home/hanlixin/apps/weiqi/weiqi3
python3 backend/server.py
```

控制台显示：

```
weiqi3 backend listening on http://127.0.0.1:8080/
  static dir: /home/hanlixin/apps/weiqi/weiqi3/frontend
  press Ctrl-C to stop
```

浏览器打开 `http://127.0.0.1:8080/` 即可使用。

需要换端口：`PORT=9000 python3 backend/server.py`

---

## 目录结构

```
weiqi3/
├── frontend/                 静态资源 + 浏览器逻辑
│   ├── index.html
│   ├── style.css
│   ├── board.js              ClientBoard：极简数据载体（无规则）
│   ├── region.js             落子区域掩码工具
│   ├── api.js                后端 API 客户端（fetch 包装）
│   ├── renderer.js           Canvas 渲染：棋子、序号、目标高亮
│   └── app.js                FSM + 事件处理 + 决策日志
│
├── backend/                  Python 求解服务
│   ├── server.py             HTTP 服务（stdlib http.server）
│   ├── board.py              SimBoard：规则引擎 + 撤销式落子
│   ├── eyes.py               严格真眼判定
│   ├── target.py             make_target_from_stone
│   └── solver.py             DfpnSolver：df-pn 主循环
│
├── doc.md                    项目历史与架构说明（推荐阅读）
└── README.md                 本文件
```

---

## 使用流程

### ① 布局
点击棋盘放置黑/白子，或用"擦除"删除。点 `下一步：设置落子点`。

### ② 设置落子点
点击单元切换"可落子 / 墙"。可用快捷按钮 `默认区域 / 全部清空 / 全部可下`。
点 `下一步：解题`。

### ③ 解题
进入后**自动提示"点击任意棋子设为目标"**：
- 点击黑子 → 黑方为防方
- 点击白子 → 白方为防方
- 点空点 → 取消（仍需选目标）

目标群被红圈高亮，代表子带红三角。

#### 落子方式
- **手动**：点击落子区域内任意空点（黑棋的回合）
- **自动对弈**：点 `最优解` 按钮，每 2 秒一手自动播放到棋盘真正终局
  （目标被提子或目标做出双眼）

#### 决策日志
右下角"决策与落子记录"实时显示：
- 选定目标的攻防角色 + 子/气/眼信息
- 每手的攻/防角色、必胜/顽抗/试探标签
- 搜索元数据：节点数、耗时、pn/dn

---

## API 端点

所有接口都接受/返回 JSON，请求方法均为 POST。

| 路径 | 用途 |
|---|---|
| `POST /api/play` | 验证 + 应用一手落子，返回新棋盘和目标状态 |
| `POST /api/make_target` | 由用户点击的棋子构造目标 (target_info) |
| `POST /api/inspect_target` | 查询某个目标坐标在当前棋盘上的活/死/气/眼状态 |
| `POST /api/legal_moves` | 枚举区域内某一方的合法落子点 |
| `POST /api/solve` | 运行 df-pn 求解器，返回最优着 + 元数据 |

详见 `backend/server.py` 中各 `_handle_*` 方法的入参/出参。

---

## 性能

后端使用纯 Python，预算可观但比 V8 慢约 20×。预设题目实测：

| 步骤 | 节点数 | 后端耗时（Python） |
|---|---|---|
| 黑先第一手 (B(0,3)) | 106,538 | ~10 s |
| 白第二手 | 56,895 | ~5.5 s |
| 黑第三手 | 14,274 | ~1.6 s |
| 第 4–9 手 | 几百–几千 | < 0.5 s |
| **完整 9 手对局** | — | **~18 s** |

如果嫌慢，可以：
- 缩小落子区域（减少分支）
- 选择更小、更危险的目标群
- 后续考虑用 PyPy（向后兼容）或重写为 C 扩展

---

## 设计参考

详细的架构、算法演进、问题排查、决策权衡见 [`doc.md`](./doc.md)。
