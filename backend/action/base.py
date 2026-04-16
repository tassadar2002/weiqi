"""Action 基类 + 共享常量和 helper。"""

import os
import sys


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DB_PATH = os.path.join(_PROJECT_ROOT, "backend", "data", "problems.db")
STORE_DIR = os.path.join(_PROJECT_ROOT, "backend", "store")


class Action:
    """CLI action 基类。每个子类实现 run(args)。"""

    def run(self, args) -> None:
        raise NotImplementedError


def fmt_duration(ms: int) -> str:
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_size(b: int) -> str:
    if b >= 1024 * 1024:
        return f"{b/1024/1024:.1f} MB"
    if b >= 1024:
        return f"{b/1024:.1f} KB"
    return f"{b} B"


def ensure_pypy3() -> None:
    """如果当前不是 pypy3，用 pypy3 重新执行自身（同参数）。"""
    import platform
    import shutil
    if platform.python_implementation() == "PyPy":
        return
    pypy3 = shutil.which("pypy3")
    if not pypy3:
        print("错误：找不到 pypy3，预处理必须使用 pypy3 运行", file=sys.stderr)
        print("请安装 pypy3 后重试", file=sys.stderr)
        sys.exit(1)
    os.execv(pypy3, [pypy3] + sys.argv)
