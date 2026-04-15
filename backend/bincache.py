"""
二进制 TT 缓存格式 + 查表求解

格式 (.bin):
  Header (20B): magic "WQ3C" + version u8 + status u8 + result u8 + pad u8
                + count u32 + root_pn u32 + root_dn u32
  Records (12B each, 按 key 排序): key 10B (zh u64 + turn i8 + lc u8) + pn u8 + dn u8
  pn/dn 编码: 0~254 原值, 255 = DFPN_INF (10^9)
  主 .bin 文件中 records 按 key 排序，支持二分查找。
"""

import mmap
import os
import struct
from typing import Dict, List, Optional, Tuple

from board import Board

DFPN_INF = 10**9
_MAGIC = b"WQ3C"
_HEADER_FMT = ">4sBBBxIII"  # 20 bytes
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 20
_RECORD_FMT = ">QBBBB"  # 12 bytes: zh(8) + turn(1) + lc(1) + pn(1) + dn(1)
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)  # 12

# status: 0=running 1=done 2=stopped
# result: 0=UNPROVEN 1=ATTACKER_WINS 2=DEFENDER_WINS
_RESULT_MAP = {"UNPROVEN": 0, "ATTACKER_WINS": 1, "DEFENDER_WINS": 2}
_RESULT_RMAP = {0: "UNPROVEN", 1: "ATTACKER_WINS", 2: "DEFENDER_WINS"}


# ============================================================
# 编码 / 解码
# ============================================================

def _encode_pn(v: int) -> int:
    return 255 if v >= 255 else v

def _decode_pn(v: int) -> int:
    return DFPN_INF if v == 255 else v

def _encode_turn(turn: int) -> int:
    """turn (-1 or 1) → unsigned byte (0 or 2)。保证字节序 = 数值序。"""
    return turn + 1  # -1→0, 1→2

def _decode_turn(v: int) -> int:
    return v - 1  # 0→-1, 2→1

def _encode_lc(lc: int) -> int:
    """last_capture (-1 or 0~168) → unsigned byte。-1 排最前（编码为 0），0~168 编码为 1~169。"""
    return 0 if lc < 0 else lc + 1

def _decode_lc(v: int) -> int:
    return -1 if v == 0 else v - 1


# ============================================================
# 打包 / 读写
# ============================================================

def _pack_key(key: tuple) -> bytes:
    """TT key (zh, turn, lc) → 10 字节。大端序，字节比较 = 数值比较。"""
    zh, turn, lc = key
    return struct.pack(">QBB", zh, _encode_turn(turn), _encode_lc(lc))

def _pack_record(key: tuple, pn: int, dn: int) -> bytes:
    """TT (key, pn, dn) → 12 字节。"""
    zh, turn, lc = key
    return struct.pack(_RECORD_FMT, zh, _encode_turn(turn), _encode_lc(lc),
                       _encode_pn(pn), _encode_pn(dn))

def _write_header(f, status=0, result=0, count=0, root_pn=0, root_dn=0):
    f.seek(0)
    f.write(struct.pack(_HEADER_FMT, _MAGIC, 1, status, result, count, root_pn, root_dn))

def _read_header(path: str) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            data = f.read(_HEADER_SIZE)
        if len(data) < _HEADER_SIZE:
            return None
        magic, ver, status, result, count, root_pn, root_dn = struct.unpack(_HEADER_FMT, data)
        if magic != _MAGIC:
            return None
        return {
            "status": status, "result": _RESULT_RMAP.get(result, "UNPROVEN"),
            "count": count, "root_pn": root_pn, "root_dn": root_dn,
        }
    except (OSError, struct.error):
        return None

def _flush_records(f, solver, already_flushed: int) -> int:
    """增量追加新 TT 记录到文件（运行中崩溃安全）。"""
    new_keys = solver.tt_log[already_flushed:]
    if not new_keys:
        return already_flushed
    tt = solver.tt
    buf = bytearray(len(new_keys) * _RECORD_SIZE)
    offset = 0
    for k in new_keys:
        pn, dn = tt[k]
        zh, turn, lc = k
        struct.pack_into(_RECORD_FMT, buf, offset,
                         zh, _encode_turn(turn), _encode_lc(lc),
                         _encode_pn(pn), _encode_pn(dn))
        offset += _RECORD_SIZE
    f.write(buf)
    f.flush()
    return already_flushed + len(new_keys)

def _dump_sorted_bin(bin_path: str, tt: Dict[tuple, Tuple[int, int]],
                     child_results: list) -> None:
    """将内存 TT dict 排序后写入 .bin 文件（去重 + 排序）。"""
    items = sorted(tt.items())
    with open(bin_path, "wb") as f:
        _write_header(f, status=1, count=len(items))
        for key, (pn, dn) in items:
            f.write(_pack_record(key, pn, dn))
    # child_results 写到 JSON
    import json
    results_path = bin_path + ".results.json"
    try:
        with open(results_path, "w") as rf:
            json.dump(child_results, rf)
    except OSError:
        pass

def _iter_records(path: str):
    """流式读取已排序 .bin 的记录，yield 12 字节 bytes。"""
    with open(path, "rb") as f:
        f.seek(_HEADER_SIZE)
        while True:
            rec = f.read(_RECORD_SIZE)
            if len(rec) < _RECORD_SIZE:
                break
            yield rec


# ============================================================
# k-way 归并合并 worker .bin
# ============================================================

def _merge_worker_bins(workers: List[dict], main_bin_path: str,
                       attacker_color: int, first_turn: int) -> None:
    """k-way 归并已排序的 worker .bin → 排序的主 .bin。流式，内存 ~0。"""
    import heapq
    import json

    child_results = []
    for w in workers:
        results_path = w["bin"] + ".results.json"
        try:
            with open(results_path) as rf:
                for move_str, pn, dn, nodes in json.load(rf):
                    child_results.append((move_str, pn, dn))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    # 计算根 pn/dn
    is_or = (first_turn == attacker_color)
    if child_results:
        if is_or:
            root_pn = min(pn for _, pn, _ in child_results)
            root_dn = min(sum(dn for _, _, dn in child_results), DFPN_INF)
        else:
            root_pn = min(sum(pn for _, pn, _ in child_results), DFPN_INF)
            root_dn = min(dn for _, _, dn in child_results)
    else:
        root_pn, root_dn = DFPN_INF, DFPN_INF

    result_str = "ATTACKER_WINS" if root_pn == 0 else (
        "DEFENDER_WINS" if root_dn == 0 else "UNPROVEN")

    # k-way 归并（各 worker .bin 已排序，key 不重叠）
    iters = [_iter_records(w["bin"]) for w in workers if os.path.exists(w["bin"])]
    os.makedirs(os.path.dirname(main_bin_path) or ".", exist_ok=True)
    with open(main_bin_path, "wb") as f:
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=0, root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))
        count = 0
        for rec in heapq.merge(*iters):
            f.write(rec)
            count += 1
        # 回写 count
        f.seek(0)
        _write_header(f, status=1, result=_RESULT_MAP.get(result_str, 0),
                      count=count, root_pn=min(root_pn, 0xFFFFFFFF),
                      root_dn=min(root_dn, 0xFFFFFFFF))

    # 清理 worker 临时文件
    for w in workers:
        for path in (w["bin"], w["bin"] + ".tmp", w["bin"] + ".results.json", w["progress"]):
            try:
                os.remove(path)
            except OSError:
                pass


# ============================================================
# BinCache: mmap + 二分查找
# ============================================================

class BinCache:
    """mmap 映射 .bin 文件，二分查找按需查询。内存占用 ~0。"""

    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "rb")
        size = os.fstat(self.f.fileno()).st_size
        if size < _HEADER_SIZE:
            self.mm = None
            self.count = 0
            self.header = None
            return
        self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        self.header = _read_header(path)
        self.count = self.header["count"] if self.header else 0

    def lookup(self, key: tuple) -> Optional[Tuple[int, int]]:
        if self.mm is None or self.count == 0:
            return None
        target = _pack_key(key)
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            off = _HEADER_SIZE + mid * _RECORD_SIZE
            rec_key = self.mm[off:off + 10]
            if rec_key < target:
                lo = mid + 1
            elif rec_key > target:
                hi = mid
            else:
                return (_decode_pn(self.mm[off + 10]), _decode_pn(self.mm[off + 11]))
        return None

    def close(self):
        if self.mm:
            self.mm.close()
            self.mm = None
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# 查表求解（预处理完成后）
# ============================================================

def solve_from_cache(cache: BinCache,
                     board: Board, turn: int, region_mask: List[int],
                     attacker_color: int) -> dict:
    """纯查表（mmap 二分查找），不跑 df-pn。毫秒级。"""

    def tt_lookup(t: int) -> Tuple[int, int]:
        r = cache.lookup((board.zh, t, board.last_capture))
        return r if r else (1, 1)

    root_pn, root_dn = tt_lookup(turn)
    if root_pn == 0:
        result = "ATTACKER_WINS"
    elif root_dn == 0:
        result = "DEFENDER_WINS"
    else:
        result = "UNPROVEN"

    is_or = (turn == attacker_color)
    winning_move = None
    resist_move = None
    resist_score = -1
    any_move = None

    for x, y in board.legal_moves_in_region(turn, region_mask):
        if any_move is None:
            any_move = (x, y)
        u = board.play_undoable(x, y, turn)
        if u is None:
            continue
        child_pn, child_dn = tt_lookup(-turn)
        board.undo(u)

        if is_or and child_pn == 0:
            winning_move = (x, y)
            break
        elif not is_or and child_dn == 0:
            winning_move = (x, y)
            break

        score = child_pn if is_or else child_dn
        if score > resist_score:
            resist_score = score
            resist_move = (x, y)

    if winning_move:
        move = {"x": winning_move[0], "y": winning_move[1], "certain": True}
    elif resist_move:
        move = {"x": resist_move[0], "y": resist_move[1], "certain": False}
    elif any_move:
        move = {"x": any_move[0], "y": any_move[1], "certain": False}
    else:
        move = None

    return {"result": result, "move": move}
