"""
weiqi3 后端 HTTP 服务

零外部依赖（仅 Python 3 stdlib）。同一端口同时：
  - 静态托管 ../frontend/ 目录
  - 提供 /api/* JSON 接口

启动：
    python3 backend/server.py
默认端口 8080；可用 PORT 环境变量覆盖。

API 端点：
    POST /api/play           落子（验证 + 应用）
    POST /api/make_target    构造目标群（用户点选）
    POST /api/inspect_target 查询目标当前状态（活/死/气/眼）
    POST /api/legal_moves    枚举合法走法（用于前端高亮）
    POST /api/solve          运行 df-pn 求解，返回最优着
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional, Tuple

# 让 backend 目录内的模块可以直接 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from board import Board
from eyes import count_real_eyes, get_target_group
from solver import DfpnSolver
from target import make_target_from_stone, validate_target_stone


# 项目根 + 静态目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")


# ============================================================
# 序列化辅助
# ============================================================

def deserialize_board(grid: List[int], last_capture: int = -1) -> Board:
    """前端发来的扁平 grid 数组 → Board 实例。"""
    b = Board(10)
    b.grid = list(grid)
    b.last_capture = int(last_capture)
    return b


def serialize_target_status(board: Board,
                            target_coord: Optional[List[int]]) -> Optional[dict]:
    """根据当前棋盘 + 单个目标代表子坐标，返回该群的实时状态。"""
    if target_coord is None:
        return None
    target = get_target_group(board, tuple(target_coord))
    if target is None:
        return {
            "captured": True,
            "alive": False,
            "group": [],
            "libs": 0,
            "real_eyes": 0,
        }
    eyes = count_real_eyes(board, target)
    return {
        "captured": False,
        "alive": eyes >= 2,
        "group": [list(p) for p in target["group"]],
        "libs": target["lib_count"],
        "real_eyes": eyes,
    }


def serialize_multi_target_status(board: Board,
                                  kill_coords: List[List[int]],
                                  defend_coords: List[List[int]]) -> dict:
    """返回所有杀目标 + 守目标的实时状态 + 复合终局判定。"""
    kills = [serialize_target_status(board, c) for c in kill_coords]
    defends = [serialize_target_status(board, c) for c in defend_coords]

    all_killed = all(s and s["captured"] for s in kills) if kills else False
    any_kill_alive = any(s and s["alive"] for s in kills)
    any_defend_captured = any(s and s["captured"] for s in defends)

    terminal = None
    if any_defend_captured:
        terminal = "DEFENDER_WINS"
    elif any_kill_alive:
        terminal = "DEFENDER_WINS"
    elif all_killed:
        terminal = "ATTACKER_WINS"

    return {
        "kill_statuses": kills,
        "defend_statuses": defends,
        "terminal": terminal,
        "all_killed": all_killed,
        "any_kill_alive": any_kill_alive,
        "any_defend_captured": any_defend_captured,
    }


# ============================================================
# 静态文件 MIME 推断
# ============================================================

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def guess_mime(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _MIME.get(ext.lower(), "application/octet-stream")


# ============================================================
# HTTP 处理器
# ============================================================

class WeiqiHandler(BaseHTTPRequestHandler):
    server_version = "weiqi3/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # 安静一点：只打印有用的请求
        if args and args[0].startswith("POST /api/"):
            sys.stderr.write("%s\n" % (args[0],))

    # ---- 工具方法 ----

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    # ---- 路由 ----

    def do_GET(self) -> None:
        path = self.path
        if path == "/" or path == "":
            path = "/index.html"
        # 砍掉 query string
        if "?" in path:
            path = path.split("?", 1)[0]
        # 防路径穿越
        if ".." in path:
            self.send_error(403, "Forbidden")
            return
        file_path = os.path.join(FRONTEND_DIR, path.lstrip("/"))
        if not os.path.isfile(file_path):
            self.send_error(404, "Not Found")
            return
        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except OSError:
            self.send_error(500, "Read Error")
            return
        self.send_response(200)
        self.send_header("Content-Type", guess_mime(file_path))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        try:
            data = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": "invalid JSON: %s" % e})
            return

        try:
            if self.path == "/api/play":
                self._handle_play(data)
            elif self.path == "/api/make_target":
                self._handle_make_target(data)
            elif self.path == "/api/validate_target":
                self._handle_validate_target(data)
            elif self.path == "/api/inspect_target":
                self._handle_inspect_target(data)
            elif self.path == "/api/legal_moves":
                self._handle_legal_moves(data)
            elif self.path == "/api/solve":
                self._handle_solve(data)
            else:
                self.send_error(404, "Unknown API endpoint")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send_json(500, {"error": str(e)})

    # ---- API 实现 ----

    def _handle_play(self, data: dict) -> None:
        board = deserialize_board(data["board"], data.get("last_capture", -1))
        x = int(data["x"])
        y = int(data["y"])
        color = int(data["color"])
        u = board.play_undoable(x, y, color)
        if u is None:
            self._send_json(200, {
                "ok": False,
                "error": "非法落子（自杀或打劫禁着）",
            })
            return
        # 支持单目标（旧）和多目标（新）
        kill_coords = data.get("kill_targets", [])
        defend_coords = data.get("defend_targets", [])
        single_coord = data.get("target_coord")
        resp = {
            "ok": True,
            "new_board": board.grid,
            "last_capture": board.last_capture,
            "captured_count": len(u.captured),
        }
        if kill_coords or defend_coords:
            resp["multi_status"] = serialize_multi_target_status(board, kill_coords, defend_coords)
        if single_coord:
            resp["target_status"] = serialize_target_status(board, single_coord)
        self._send_json(200, resp)

    def _handle_make_target(self, data: dict) -> None:
        board = deserialize_board(data["board"], data.get("last_capture", -1))
        region = data["region"]
        x = int(data["x"])
        y = int(data["y"])
        result = make_target_from_stone(board, region, x, y)
        # 附加 target_status 便于前端立即展示当前群
        if "error" not in result:
            result["target_status"] = serialize_target_status(board, result["target_coord"])
        self._send_json(200, result)

    def _handle_validate_target(self, data: dict) -> None:
        """验证单个棋子是否可作为目标（不区分杀/守，由前端决定）。"""
        board = deserialize_board(data["board"], data.get("last_capture", -1))
        region = data["region"]
        x = int(data["x"])
        y = int(data["y"])
        result = validate_target_stone(board, region, x, y)
        self._send_json(200, result)

    def _handle_inspect_target(self, data: dict) -> None:
        board = deserialize_board(data["board"], data.get("last_capture", -1))
        target_coord = data.get("target_coord")
        status = serialize_target_status(board, target_coord)
        self._send_json(200, status or {})

    def _handle_legal_moves(self, data: dict) -> None:
        board = deserialize_board(data["board"], data.get("last_capture", -1))
        region = data["region"]
        color = int(data["color"])
        moves = board.legal_moves_in_region(color, region)
        self._send_json(200, {"moves": [list(m) for m in moves]})

    def _handle_solve(self, data: dict) -> None:
        board = deserialize_board(data["board"], data.get("last_capture", -1))
        region = data["region"]
        target = data["target"]
        attacker_color = int(target["attacker_color"])
        turn = int(data["turn"])
        max_time_ms = int(data.get("max_time_ms", 60_000))
        max_nodes = int(data.get("max_nodes", 5_000_000))
        max_depth = int(data.get("max_depth", 60))

        # 解析多目标（新）或单目标（旧兼容）
        kill_targets = [tuple(c) for c in target.get("kill_targets_coords", [])]
        defend_targets = [tuple(c) for c in target.get("defend_targets_coords", [])]
        # 旧接口兼容：若无多目标字段，从 target_coord 构造
        if not kill_targets and not defend_targets and "target_coord" in target:
            tc = tuple(target["target_coord"])
            # 旧语义：target_coord 指向防方群 = 攻方要杀
            kill_targets = [tc]

        solver = DfpnSolver(
            board, region,
            attacker_color=attacker_color,
            kill_targets=kill_targets,
            defend_targets=defend_targets,
            max_nodes=max_nodes,
            max_time_ms=max_time_ms,
            max_depth=max_depth,
        )
        result = solver.solve(turn)

        # 附多目标实时状态
        kill_coords = [list(c) for c in kill_targets]
        defend_coords = [list(c) for c in defend_targets]
        result["multi_status"] = serialize_multi_target_status(board, kill_coords, defend_coords)
        # 兼容旧接口
        if kill_targets:
            result["target_status"] = serialize_target_status(board, list(kill_targets[0]))
        self._send_json(200, result)


# ============================================================
# 启动
# ============================================================

def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    addr = ("127.0.0.1", port)
    httpd = HTTPServer(addr, WeiqiHandler)
    print(f"weiqi3 backend listening on http://{addr[0]}:{addr[1]}/")
    print(f"  static dir: {FRONTEND_DIR}")
    print(f"  press Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutdown")


if __name__ == "__main__":
    main()
