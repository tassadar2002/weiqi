# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**weiqi3** is a 13×13 Go (围棋) tsumego solver implementing strict proof number search (df-pn). The project consists of a browser-based frontend (HTML/JS/CSS) and a Python backend for rule enforcement and solving logic, with zero external dependencies in both layers.

### Core Concept

Users layout Go problems (黑白子布局) → set playable regions → specify target stones → run exhaustive df-pn solver → get proven optimal moves. Two solving modes:
1. **Precomputation**: Multi-process background exhaustive search writing a binary store (mmap + 6.3x compression vs. dict)
2. **Table lookup**: Sub-millisecond queries against the binary store after precomputation completes

## Architecture & Structure

### Layers

```
┌─ Frontend (zero JS dependencies) ──────────────┐
│  index.html (two-view SPA)                     │
│  ├─ app.js: FSM state machine + decision log   │
│  ├─ board.js: minimal data carrier             │
│  ├─ api.js: fetch wrapper for JSON endpoints   │
│  ├─ renderer.js: Canvas rendering              │
│  └─ style.css: classical Chinese aesthetic     │
│                                                 │
│ Communication: JSON POST requests to /api/*    │
└─────────────────────────────────────────────────┘
                         ↕ HTTP/JSON
┌─ Backend (Python 3 stdlib only) ───────────────┐
│  server.py: HTTP service (http.server stdlib)  │
│  ├─ board.py: rules engine + undo-stack        │
│  ├─ precompute/solver.py: df-pn main loop + TT │
│  ├─ precompute/binstore.py: binary store + DiskTT │
│  ├─ precompute/coordinator.py: multi-process orchestration │
│  ├─ precompute/worker.py: stateless worker     │
│  ├─ cli_precompute.py: CLI entry point         │
│  ├─ action/: CLI subcommand classes (list/status/run) │
│  ├─ problems.py: problem CRUD (SQLite)         │
│  ├─ eyes.py: strict true-eye detection         │
│  └─ target.py: target validation               │
│                                                 │
│ Persistence: SQLite problems.db + per-job binary store in backend/store/ │
└─────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Undo-stack architecture** (`board.py::play_undoable / undo`): Play a move and capture undo state; restore board in-place rather than cloning. Essential for df-pn efficiency.

2. **Zobrist incremental hashing** (`board.py`): 64-bit hash updated via XOR; enables transposition table keys to be computed in O(1).

3. **Lazy group allocation** (`board.py::group_and_libs`): Only construct group coordinates during capture; most group queries skip allocation. 

4. **Epoch-based visited tracking**: Module-level `bytearray` + epoch counter replaces `Set` allocation per traversal.

5. **Multi-process root-splitting** (`precompute/coordinator.py` + `precompute/worker.py`): Coordinator enumerates root candidates and hands them out via a task queue; each worker solves one root → worker-local `.bin` → merge into the job's final `.bin`. Work-stealing; crash recovery via `{job_id}_pids.json` and bin status byte.

6. **Binary store format** (`precompute/binstore.py`): Sorted 12-byte records (8B key + 2B pn + 2B dn) in `.bin` files; mmap + binary search for O(log n) lookup with ~0 memory overhead.

7. **Stateless HTTP API**: Each request includes full board state (169 integers for 13×13). No per-session state on backend; enables horizontal scaling and resilience.

8. **Two-mode solving**:
   - Precompute: Exhaustive, infinite time/node budget, no cutoff
   - Lookup: Post-precompute, binary store queries only, <1ms response
   
   These modes have fundamentally different logic; lookup mode **never runs df-pn**, only queries the binary store via mmap.

### Frontend FSM States

```
layout (place stones) ──[next]──> region (set playable area) ──[next]──> pick-target
                                                                           │
                                                    user clicks stone → confirm → save → list view
```

The solver supports two phases:
- **Precompute phase**: CLI command `python3 backend/cli_precompute.py run <problem_id>` (auto-switches to pypy3)
- **Lookup phase**: After precompute done, clicking "最优解" calls `/api/solve` for instant table lookup

### Data Flow

1. User lays out stones → board_grid (169 ints: -1/0/1 for white/empty/black)
2. User sets region → region_mask (169 ints: 0/1)
3. User picks target stone → validated via `POST /api/validate_target` → target_info (group, libs, eyes, attacker_color)
4. User confirms target → auto-save → back to list
5. CLI: `cli_precompute.py run <id>` → Coordinator dispatches root moves to Workers; each worker writes a per-root sorted `.bin` → k-way merge → final `{job_id}.bin`
6. User clicks "最优解" → `POST /api/solve` mmap-opens the binary store, binary-search lookups best move
7. User plays moves → `POST /api/play` updates board, checks legality, reports captures

## Development Commands

### Run the Application

```bash
python3 backend/server.py              # Start on localhost:8080
PORT=9000 python3 backend/server.py    # Custom port
```

Then open `http://localhost:8080/` in browser.

### Precompute (CLI)

```bash
python3 backend/cli_precompute.py list                        # List all problems
python3 backend/cli_precompute.py status <problem_id>         # Check precompute status (with worker details)
python3 backend/cli_precompute.py run <problem_id>            # Run precompute (auto-switches to pypy3)
python3 backend/cli_precompute.py run <problem_id> -w 4       # Specify worker count
```

### Run Tests

```bash
python3 backend/test_solver.py         # Verification tests (correctness + speed)
```

Tests include:
- Simple 1-liberty capture
- 2-liberty multi-step kill
- Defender survival
- Performance benchmarking (nodes/sec)

### Manual Backend Testing

```bash
cd backend
python3 -c "
from board import Board, BLACK
from precompute.solver import DfpnSolver

b = Board(13)
b.set(1, 1, -1)  # White
b.set(0, 1, 1); b.set(2, 1, 1); b.set(1, 2, 1)  # Black surrounds
mask = [1] * 169
solver = DfpnSolver(b, mask, attacker_color=BLACK, kill_targets=[(1,1)], defend_targets=[])
print(solver.solve(BLACK))
"
```

### No Build Step

- Frontend: Static files (no npm, no webpack)
- Backend: Stdlib only (no pip, no setup.py)
- Direct execution: `python3 backend/server.py`

## Critical Algorithms

### Proof Number Search (df-pn)

Located in `precompute/solver.py::DfpnSolver._mid()`. Core idea:

- Maintains two numbers per position: **pn** (cost to prove attacker wins), **dn** (cost to prove defender wins)
- OR node (attacker's turn): pn = min(children.pn), dn = sum(children.dn)
- AND node (defender's turn): pn = sum(children.pn), dn = min(children.dn)
- Expands best-first (most promising pn/dn), not depth-first
- Terminates when pn=0 (attacker wins), dn=0 (defender wins), or budget exhausted
- **No pruning, no heuristic evaluation**: purely proof-theoretic

### True Eye Detection (eyes.py)

A point P is a true eye for color C iff:
1. P is empty
2. All 4 orthogonal neighbors belong to the same stone group (not just same color)
3. Diagonal rule: boundary/corner points need all diagonals to be color C; interior points need ≥3 diagonals

Critical: orthogonal neighbors must form a connected component; this prevents "4 isolated same-color stones surrounding a point" from being mistaken for an eye.

### Termination Conditions

The solver terminates a branch when:
- **Attacker wins**: Target stones captured (removed from board)
- **Defender wins**: Target has ≥2 true eyes (provably alive) OR any defend_target captured (if multi-target mode)
- **Unproven**: Time/node budget exhausted without proof

## Important Files & Their Roles

### Backend

| File | Purpose |
|------|---------|
| `server.py` | HTTP server, route handlers, binary-store lookup via BinStore for `/api/solve` |
| `board.py` | Board state, play/undo, group+libs, legal move generation |
| `precompute/solver.py` | df-pn main loop, transposition table, termination checks |
| `precompute/binstore.py` | Binary store format (.bin), BinStore mmap lookup, solve_from_store, DiskTT, k-way merge |
| `precompute/coordinator.py` | Multi-process coordinator (task queue, event loop, crash recovery) |
| `precompute/worker.py` | Stateless worker (pulls root tasks, runs df-pn with DiskTT, writes per-root .bin) |
| `cli_precompute.py` | CLI entry point; dispatches to `action/` classes |
| `action/list_action.py` | `list` subcommand |
| `action/status_action.py` | `status` subcommand (worker table + root-kid table) |
| `action/run_action.py` | `run` subcommand (auto-switches to pypy3, spawns Coordinator) |
| `action/base.py` | `Action` base class, path constants, formatters, `ensure_pypy3` |
| `problems.py` | SQLite schema + CRUD for problem persistence |
| `eyes.py` | True eye detection (group-specific) |
| `target.py` | Validate target stone → group representation |
| `test_solver.py` | Unit tests for correctness & speed |

### Frontend

| File | Purpose |
|------|---------|
| `app.js` | FSM control, event handlers, async API calls, decision log UI (no precompute UI) |
| `board.js` | ClientBoard: minimal grid container (no rules) |
| `api.js` | Fetch wrappers for /api/* endpoints |
| `renderer.js` | Canvas drawing: stones, grid, annotations, highlights |
| `region.js` | Playable region mask utilities |
| `index.html` | Two-view SPA structure |
| `style.css` | Styling (Google Fonts: Ma Shan Zheng + Noto Serif SC) |

### Documentation

- `doc/arch.md` — Complete architecture v2 (13×13, multi-target, precompute system)
- `doc/doc.md` — Historical evolution, algorithm journey (minimax → df-pn), performance optimization
- `doc/algorithms.md` — df-pn theory & implementation details
- `doc/backend.md` — API endpoints, database schema, data models
- `doc/db.md` — Binary store format (sorted records, mmap, compression)
- `doc/pre_compute01.md` — Multi-process strategy & worker coordination

## Performance Characteristics

**Precompute runs on PyPy3** (required by `cli_precompute.py run`); the HTTP server can run on either CPython or PyPy.

**Performance levers** (if optimization needed):
1. **Reduce problem size** — User shrinks playable region or picks smaller target
2. **PyPy** — Already required for precompute; `pypy3 backend/server.py` also works for the server (5–10× CPython speedup)
3. **C extensions** — Rewrite `group_and_libs` in C for another ~5× (not currently done)
4. **Multi-process precompute** — Already implemented; each worker process owns one root move, so scaling is limited by root count rather than GIL

## Persistence

### Problem Storage

SQLite at `backend/data/problems.db`:
```sql
CREATE TABLE problems (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    board_size INTEGER DEFAULT 13,
    board_grid TEXT NOT NULL,       -- JSON: 169 ints
    region_mask TEXT NOT NULL,      -- JSON: 169 ints (0/1)
    kill_targets TEXT,              -- JSON: [[x,y], ...]
    defend_targets TEXT,            -- JSON: [[x,y], ...]
    attacker_color INTEGER DEFAULT 1,
    precompute_status TEXT DEFAULT 'none',  -- 'none', 'running', 'done'
    precompute_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### Precompute Store

Per-job binary store in `backend/store/`:
- `{job_id}.bin` — Final merged TT (sorted 12-byte records: 8B key + 2B pn + 2B dn); mmap + binary search for O(log n) lookup
- `{job_id}_{x}_{y}.bin` — Per-root-move shard written by a Worker; merged into the final bin, status byte flipped to `1` when proven
- `{job_id}_worker{i}_progress.json` — Per-worker real-time stats (pid, status, current_move, total_nodes, tasks_done)
- `{job_id}_progress.json` — Overall progress snapshot (workers, root_kids, elapsed, nodes/sec)
- `{job_id}_root_moves.json` — Complete enumerated root-move list (used by `status` to show queued roots)
- `{job_id}_pids.json` — Worker PIDs, consulted on restart to kill orphan workers for crash recovery

## Testing Strategy

Use `test_solver.py` to verify correctness after algorithm changes:

```bash
python3 backend/test_solver.py
```

Tests confirm:
- Exact node counts match expected values (regression detection)
- Results are ATTACKER_WINS / DEFENDER_WINS as expected
- Speed (nodes/sec) indicates if optimization regressed

For API testing, use browser console or curl:

```bash
curl -X POST http://localhost:8080/api/play \
  -H 'Content-Type: application/json' \
  -d '{
    "board": [...169 ints...],
    "last_capture": -1,
    "x": 3, "y": 4,
    "color": 1
  }'
```

## Code Conventions

### Python (backend/)

- Type hints on all functions
- Chinese docstrings for algorithm explanation
- `__slots__` on hot classes (Board, UndoInfo)
- Zobrist hash updates via XOR (O(1))
- No external imports (stdlib only)

### JavaScript (frontend/)

- ES2020+, no frameworks
- Constants: `const BLACK=1, WHITE=-1, EMPTY=0, BOARD_SIZE=13`
- ClientBoard: pure data, no logic
- API: Promise-returning methods
- Renderer: Canvas 2D context only

### CSS

- CSS variables: `--ink-black`, `--jade`, `--vermillion`, `--parchment`
- Grid layout for responsive design
- Google Fonts for classical aesthetic

## Assumptions & Constraints

- **Fixed board size**: 13×13 (modify BOARD_SIZE constant + update Zobrist table size to change)
- **Zero dependencies**: Any new feature must use only Python stdlib + native JS
- **Stateless API**: Backend has no session state; each request is independent
- **Single-threaded frontend**: Long solves block UI (mitigated by backend precompute)
- **Simple Ko rule**: Only prevent immediate recapture; no superko path tracking
- **Single target mode dominant**: Code supports multi-target but optimized for single kill/defend
- **No network latency tolerance**: Expected use is local (same machine or LAN); remote deployments untested

## Extending the Codebase

### Adding a New API Endpoint

1. Define handler in `server.py::RequestHandler._handle_*`
2. Register route in `server.py::do_POST` dispatcher
3. Update `frontend/api.js::API` class with method wrapper
4. Call from `app.js` event handlers

Example: `POST /api/custom` handler → `API.custom()` → `App.someEventListener()`.

### Modifying the Solver

Changes to `precompute/solver.py::DfpnSolver._mid()` affect all solving (precompute + lookup). Always run `test_solver.py` after changes to catch regressions.

Key points:
- Termination checks in `_terminal()` must be exhaustive (missing case = wrong result)
- TT lookups must use exact same key generation as insertion
- Move generation must respect region_mask

### Changing Board Size

1. Update `board.py::BOARD_SIZE`
2. Regenerate `board.py::ZOBRIST` table (randomize with new size)
3. Update `frontend/app.js::BOARD_SIZE` constant
4. Update test cases in `test_solver.py`
5. Re-run full verification

## Common Pitfalls

1. **Forgetting `undo()`**: If you call `play_undoable()` but never `undo()`, board stays dirty. Always use try/finally or structured patterns.

2. **Region mask out of bounds**: Setting region_mask cells outside the board size causes indexing errors. Validate region generation.

3. **Target not in region**: If a target stone is outside the playable region, solver will never reach it. Frontend should prevent this.

4. **TT key mismatch**: If precompute uses different key generation than lookup, store lookups silently miss — `/api/solve` would return "not found" rather than wrong answers.

5. **PyPy3 required for precompute**: `cli_precompute.py run` enforces pypy3 (auto-detects and exec's). Must have pypy3 installed; no python3 fallback for precompute.

## References

- **Algorithm**: Kishimoto, Akihiro. "Threshold Proof Number Search" (2002). See `doc/algorithms.md` for full theory.
- **Project evolution**: `doc/doc.md` traces minimax → df-pn transition and performance optimization journey.
- **Architecture**: `doc/arch.md` is the canonical design document; used to bootstrap new environments.
