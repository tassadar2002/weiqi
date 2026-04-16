#!/usr/bin/env python3
"""
围棋习题预处理 CLI — 入口

用法:
  python3 cli_precompute.py list                    # 列出所有题目
  python3 cli_precompute.py status <problem_id>     # 查看预处理状态
  python3 cli_precompute.py run <problem_id> [-w N] # 运行预处理（自动切 pypy3）

每个子命令的实现在 backend/action/ 下。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from action.base import ensure_pypy3
from action.list_action import ListAction
from action.run_action import RunAction
from action.status_action import StatusAction


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="围棋习题预处理工具",
        usage="python3 cli_precompute.py {list,run,status} ...",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有题目")

    p_status = sub.add_parser("status", help="查看题目预处理状态")
    p_status.add_argument("problem_id", help="题目 ID")

    p_run = sub.add_parser("run", help="对指定题目运行预处理")
    p_run.add_argument("problem_id", help="题目 ID")
    p_run.add_argument("-w", "--workers", type=int, default=None,
                       help="worker 进程数（默认 CPU×70%%）")

    return parser


_ACTIONS = {
    "list": (ListAction, False),
    "status": (StatusAction, False),
    "run": (RunAction, True),   # True = 需要 pypy3
}


def main() -> None:
    parser = _build_parser()
    args, _ = parser.parse_known_args()
    entry = _ACTIONS.get(args.cmd)
    if entry is None:
        parser.print_help()
        sys.exit(1)
    action_cls, need_pypy = entry
    if need_pypy:
        ensure_pypy3()
    action_cls().run(args)


if __name__ == "__main__":
    main()
