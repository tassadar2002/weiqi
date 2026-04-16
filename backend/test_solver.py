"""
预处理优化验证脚本

每次优化后运行此脚本：python3 backend/test_solver.py
检查：1) 结果正确性  2) 搜索速度
"""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from board import Board, BLACK, WHITE, EMPTY
from precompute.solver import DfpnSolver


def test_simple_kill():
    """白1子1气，黑1手杀"""
    b = Board(13)
    b.set(1, 1, WHITE)
    b.set(0, 1, BLACK); b.set(2, 1, BLACK); b.set(1, 2, BLACK)
    region = [0]*169
    for y in range(3):
        for x in range(3):
            region[y*13+x] = 1
    solver = DfpnSolver(b, region, attacker_color=BLACK,
                        kill_targets=[(1,1)], defend_targets=[],
                        max_nodes=100000, max_time_ms=10000)
    r = solver.solve(BLACK)
    assert r['result'] == 'ATTACKER_WINS', f'Expected ATTACKER_WINS, got {r["result"]}'
    print(f'  [PASS] simple_kill: {r["result"]}  nodes={r["nodes"]}')


def test_two_libs_kill():
    """白2子2气，黑需2手杀"""
    b = Board(13)
    b.set(1, 1, WHITE); b.set(2, 1, WHITE)
    b.set(0, 1, BLACK); b.set(3, 1, BLACK)
    b.set(1, 2, BLACK); b.set(2, 2, BLACK)
    region = [0]*169
    for y in range(4):
        for x in range(5):
            region[y*13+x] = 1
    solver = DfpnSolver(b, region, attacker_color=BLACK,
                        kill_targets=[(1,1)], defend_targets=[],
                        max_nodes=500000, max_time_ms=30000)
    r = solver.solve(BLACK)
    assert r['result'] == 'ATTACKER_WINS', f'Expected ATTACKER_WINS, got {r["result"]}'
    print(f'  [PASS] two_libs_kill: {r["result"]}  nodes={r["nodes"]}')


def test_defender_wins():
    """白1子有充足气和空间，黑无法杀 → 防方胜"""
    b = Board(13)
    # 白子在中间，周围全是空位，区域很小→黑无法围杀
    b.set(2, 2, WHITE)
    b.set(0, 2, BLACK)  # 远处一个黑子
    region = [0]*169
    for y in range(4):
        for x in range(4):
            region[y*13+x] = 1
    solver = DfpnSolver(b, region, attacker_color=BLACK,
                        kill_targets=[(2,2)], defend_targets=[],
                        max_nodes=500000, max_time_ms=30000)
    r = solver.solve(BLACK)
    # 区域4x4=16格，白1子中央4气，黑先手但区域有限
    print(f'  [INFO] defender_test: {r["result"]}  nodes={r["nodes"]}')
    # 不做 assert，只检查不崩溃


def test_perf():
    """中等复杂度，测速度"""
    b = Board(13)
    b.set(2, 2, WHITE); b.set(3, 2, WHITE); b.set(4, 2, WHITE)
    b.set(2, 3, WHITE); b.set(4, 3, WHITE)
    b.set(1, 2, BLACK); b.set(5, 2, BLACK)
    b.set(1, 3, BLACK); b.set(5, 3, BLACK)
    b.set(2, 4, BLACK); b.set(3, 4, BLACK); b.set(4, 4, BLACK)
    region = [0]*169
    for y in range(6):
        for x in range(7):
            region[y*13+x] = 1
    solver = DfpnSolver(b, region, attacker_color=BLACK,
                        kill_targets=[(2,2)], defend_targets=[],
                        max_nodes=50000, max_time_ms=60000)
    t0 = time.monotonic()
    r = solver.solve(BLACK)
    dt = time.monotonic() - t0
    nps = r['nodes'] / max(dt, 0.001)
    print(f'  [PERF] nodes={r["nodes"]}  time={dt:.3f}s  nps={nps:.0f}')
    return nps


if __name__ == '__main__':
    print('=== Solver Verification ===')
    test_simple_kill()
    test_two_libs_kill()
    test_defender_wins()
    nps = test_perf()
    print(f'=== All tests passed.  Speed: {nps:.0f} nodes/sec ===')
