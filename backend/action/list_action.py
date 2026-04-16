"""列出所有题目。"""

from action.base import DB_PATH, Action


class ListAction(Action):
    def run(self, args) -> None:
        from problems import init_db, list_problems
        init_db(DB_PATH)
        problems = list_problems(DB_PATH)
        if not problems:
            print("暂无题目。请先在浏览器中创建题目并设定目标。")
            return
        fmt = "{:<14s} {:<14s} {:>3s} {:>3s} {:>4s} {:>3s} {:>3s}  {}"
        print(fmt.format("ID", "名称", "黑", "白", "区域", "杀", "守", "预处理"))
        print("-" * 72)
        for p in problems:
            name = (p["name"] or "")[:12]
            status = {"done": "✓ 已完成", "running": "⋯ 进行中", "none": "✗ 未处理"}.get(
                p["precompute_status"], p["precompute_status"])
            print(fmt.format(
                p["id"], name,
                str(p["black_count"]), str(p["white_count"]),
                str(p["region_count"]),
                str(p["kill_count"]), str(p["defend_count"]),
                status,
            ))
