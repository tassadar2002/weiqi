"""
weiqi3 后端 HTTP 服务

零外部依赖。同一端口托管 frontend/ 静态文件 + /api/* JSON 接口。

启动：python3 backend/server.py
"""

import json
import os
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from board import Board, BOARD_SIZE, EMPTY
from eyes import count_real_eyes, get_target_group
from precompute import BinCache, solve_from_cache
from problems import (create_problem, delete_problem, get_problem, init_db,
                       list_problems, update_problem)
from target import validate_target_stone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
PROBLEMS_DB = os.path.join(PROJECT_ROOT, "backend", "data", "problems.db")
CACHE_DIR = os.path.join(PROJECT_ROOT, "backend", "cache")

# 确保目录和 DB 存在
os.makedirs(os.path.join(PROJECT_ROOT, "backend", "data"), exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
init_db(PROBLEMS_DB)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _board_from(data: dict) -> Board:
    b = Board(BOARD_SIZE)
    b.grid = list(data["board"])
    b.last_capture = int(data.get("last_capture", -1))
    b.rebuild_zh()
    return b


def _target_status(board: Board, coord: Optional[List[int]]) -> Optional[dict]:
    if coord is None:
        return None
    tgt = get_target_group(board, tuple(coord))
    if tgt is None:
        return {"captured": True, "alive": False, "group": [], "libs": 0, "real_eyes": 0}
    eyes = count_real_eyes(board, tgt)
    return {
        "captured": False,
        "alive": eyes >= 2,
        "group": [list(p) for p in tgt["group"]],
        "libs": tgt["lib_count"],
        "real_eyes": eyes,
    }


def _multi_status(board: Board, kills: List[List[int]], defends: List[List[int]]) -> dict:
    ks = [_target_status(board, c) for c in kills]
    ds = [_target_status(board, c) for c in defends]
    all_killed = all(s and s["captured"] for s in ks) if ks else False
    any_alive = any(s and s["alive"] for s in ks)
    any_def_cap = any(s and s["captured"] for s in ds)
    terminal = None
    if any_def_cap: terminal = "DEFENDER_WINS"
    elif any_alive: terminal = "DEFENDER_WINS"
    elif all_killed: terminal = "ATTACKER_WINS"
    return {"kill_statuses": ks, "defend_statuses": ds, "terminal": terminal}


class Handler(BaseHTTPRequestHandler):
    server_version = "weiqi3/2.0"

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("POST /api/"):
            sys.stderr.write(f"{args[0]}\n")

    def _json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0: return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- Static ----

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/": path = "/index.html"

        # API: GET /api/problems
        if path == "/api/problems":
            self._json(200, {"problems": list_problems(PROBLEMS_DB)})
            return

        # API: GET /api/problems/{id}
        m = re.match(r"^/api/problems/([a-f0-9]+)$", path)
        if m:
            p = get_problem(PROBLEMS_DB, m.group(1))
            if p: self._json(200, p)
            else: self._json(404, {"error": "not found"})
            return

        # Static files
        if ".." in path:
            self.send_error(403); return
        fp = os.path.join(FRONTEND_DIR, path.lstrip("/"))
        if not os.path.isfile(fp):
            self.send_error(404); return
        with open(fp, "rb") as f:
            content = f.read()
        _, ext = os.path.splitext(fp)
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(ext.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_PUT(self):
        m = re.match(r"^/api/problems/([a-f0-9]+)$", self.path)
        if not m:
            self.send_error(404); return
        data = self._read()
        ok = update_problem(PROBLEMS_DB, m.group(1), **data)
        self._json(200, {"ok": ok})

    def do_DELETE(self):
        m = re.match(r"^/api/problems/([a-f0-9]+)$", self.path)
        if not m:
            self.send_error(404); return
        ok = delete_problem(PROBLEMS_DB, m.group(1), CACHE_DIR)
        self._json(200, {"ok": ok})

    def do_POST(self):
        try:
            data = self._read()
        except Exception as e:
            self._json(400, {"error": str(e)}); return
        path = self.path
        try:
            if path == "/api/problems":
                self._h_create_problem(data)
            elif path == "/api/play":
                self._h_play(data)
            elif path == "/api/validate_target":
                self._h_validate_target(data)
            elif path == "/api/legal_moves":
                self._h_legal_moves(data)
            elif path == "/api/solve":
                self._h_solve(data)
            else:
                self.send_error(404)
        except Exception as e:
            import traceback; traceback.print_exc()
            self._json(500, {"error": str(e)})

    # ---- Problems ----

    def _h_create_problem(self, data):
        pid = create_problem(PROBLEMS_DB, data.get("name", "未命名习题"),
                             data.get("board_grid"))
        p = get_problem(PROBLEMS_DB, pid)
        self._json(200, p)

    # ---- Play ----

    def _h_play(self, data):
        board = _board_from(data)
        x, y, color = int(data["x"]), int(data["y"]), int(data["color"])
        u = board.play_undoable(x, y, color)
        if u is None:
            self._json(200, {"ok": False, "error": "非法落子"}); return
        kills = data.get("kill_targets", [])
        defends = data.get("defend_targets", [])
        resp = {
            "ok": True,
            "new_board": board.grid,
            "last_capture": board.last_capture,
            "captured_count": len(u.captured),
        }
        if kills or defends:
            resp["multi_status"] = _multi_status(board, kills, defends)
        self._json(200, resp)

    def _h_validate_target(self, data):
        board = _board_from(data)
        self._json(200, validate_target_stone(board, data["region"], int(data["x"]), int(data["y"])))

    def _h_legal_moves(self, data):
        board = _board_from(data)
        moves = board.legal_moves_in_region(int(data["color"]), data["region"])
        self._json(200, {"moves": [list(m) for m in moves]})

    # ---- Solve (查表) ----

    def _h_solve(self, data):
        board = _board_from(data)
        target = data["target"]
        turn = int(data["turn"])
        attacker = int(target["attacker_color"])
        kill_coords = [tuple(c) for c in target.get("kill_targets_coords", [])]
        defend_coords = [tuple(c) for c in target.get("defend_targets_coords", [])]
        region = data["region"]

        # 检查预处理缓存
        cache_id = data.get("precompute_cache_id")
        if cache_id:
            bin_path = os.path.join(CACHE_DIR, f"{cache_id}.bin")
            if os.path.exists(bin_path):
                with BinCache(bin_path) as cache:
                    result = solve_from_cache(cache, board, turn, region, attacker)
                result["cached"] = True
                result["multi_status"] = _multi_status(
                    board, [list(c) for c in kill_coords], [list(c) for c in defend_coords])
                self._json(200, result)
                return

        # 无缓存：在线 df-pn（有时间限制）
        from solver import DfpnSolver
        max_time = int(data.get("max_time_ms", 60_000))
        max_nodes = int(data.get("max_nodes", 5_000_000))
        solver = DfpnSolver(
            board, region, attacker_color=attacker,
            kill_targets=list(kill_coords), defend_targets=list(defend_coords),
            max_nodes=max_nodes, max_time_ms=max_time,
        )
        r = solver.solve(turn)
        # 从 TT 提取最优着
        r["move"] = self._extract_move(solver, board, turn, region, attacker)
        r["cached"] = False
        r["multi_status"] = _multi_status(
            board, [list(c) for c in kill_coords], [list(c) for c in defend_coords])
        self._json(200, r)

    def _extract_move(self, solver, board, turn, region, attacker):
        """从 solver 的 TT 中提取最优着（含顽抗着逻辑）。"""
        is_or = (turn == attacker)
        winning = None
        resist_move = None
        resist_score = -1
        any_move = None
        for x, y in board.legal_moves_in_region(turn, region):
            if any_move is None: any_move = (x, y)
            u = board.play_undoable(x, y, turn)
            if u is None: continue
            cpn, cdn = solver._tt_get(-turn)
            board.undo(u)
            if is_or and cpn == 0:
                return {"x": x, "y": y, "certain": True}
            elif not is_or and cdn == 0:
                return {"x": x, "y": y, "certain": True}
            score = cpn if is_or else cdn
            if score > resist_score:
                resist_score = score
                resist_move = (x, y)
        if resist_move:
            return {"x": resist_move[0], "y": resist_move[1], "certain": False}
        if any_move:
            return {"x": any_move[0], "y": any_move[1], "certain": False}
        return None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    port = int(os.environ.get("PORT", "8080"))
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    print(f"weiqi3 on http://127.0.0.1:{port}/")
    print(f"  frontend: {FRONTEND_DIR}")
    print(f"  problems: {PROBLEMS_DB}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
