# 术语表

| 简写 | 全称 | 含义 |
|------|------|------|
| **OR** | OR node | 攻方选着的节点（只需一个子节点赢） |
| **AND** | AND node | 防方选着的节点（需要所有子节点赢） |
| **pn** | Proof Number | 证明攻方赢还需的最小工作量 |
| **dn** | Disproof Number | 证明防方赢还需的最小工作量 |
| **TT** | Transposition Table | 转置表（局面→pn/dn 的哈希表） |
| **INF** | Infinity (10^9) | 不可能证明 |
| **ATK** | Attacker wins | 攻方赢（目标全部被杀） |
| **DEF** | Defender wins | 防方赢（目标做活或守目标被杀） |
| **df-pn** | Depth-First Proof Number search | 深度优先证明数搜索 |
| **MID** | Multiple Iterative Deepening | 多重迭代深化（df-pn 的核心循环） |
| **zh** | Zobrist Hash | 棋盘局面的增量哈希值（64位） |
| **lc** | Last Capture | 上一步提子位置（用于 ko 判定） |
| **ko** | 劫 (日语: コウ) | 禁止立即回提的规则 |
| **LPT** | Longest Processing Time first | 最长处理时间优先（调度算法） |
| **nps** | Nodes Per Second | 每秒搜索节点数 |
| **BFS** | Breadth-First Search | 广度优先搜索（group_and_libs 中用） |
| **OOM** | Out Of Memory | 内存溢出 |
| **WAL** | Write-Ahead Logging | SQLite 预写日志模式（已弃用） |
| **mmap** | Memory-Mapped file | 内存映射文件 |
| **IPC** | Inter-Process Communication | 进程间通信 |
| **GIL** | Global Interpreter Lock | Python 全局解释器锁 |
| **JIT** | Just-In-Time compilation | 即时编译（PyPy 的加速机制） |
