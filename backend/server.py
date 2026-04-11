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
from target import make_target_from_stone


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
    """根据当前棋盘 + 目标代表子坐标，返回目标群的实时状态。"""
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
        target_coord = data.get("target_coord")
        status = serialize_target_status(board, target_coord)
        self._send_json(200, {
            "ok": True,
            "new_board": board.grid,
            "last_capture": board.last_capture,
            "captured_count": len(u.captured),
            "target_status": status,
        })

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
        target_coord: Tuple[int, int] = tuple(target["target_coord"])
        attacker_color = int(target["attacker_color"])
        turn = int(data["turn"])
        max_time_ms = int(data.get("max_time_ms", 60_000))
        max_nodes = int(data.get("max_nodes", 5_000_000))
        max_depth = int(data.get("max_depth", 60))

        solver = DfpnSolver(
            board, region, target_coord, attacker_color,
            max_nodes=max_nodes,
            max_time_ms=max_time_ms,
            max_depth=max_depth,
        )
        result = solver.solve(turn)
        # 求解后局面未变，但顺便附上当前 target_status
        result["target_status"] = serialize_target_status(board, list(target_coord))
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
