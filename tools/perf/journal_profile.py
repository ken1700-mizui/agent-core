"""Journal performance: cause analysis (development tool, not part of Agent Core).

Small synthetic journals of three shapes, written through the real Runtime
path (``tests/support`` worlds: the real ``SqliteJournal``, ledger and gate):

- ``A``: one Run with many Steps (each created, started, cancelled);
- ``B``: many Runs with three Steps each;
- ``C``: Runs with three Steps, each Step one tool Attempt through the whole
  permit path (decision, reservation, intent, permit, consume, claim, start,
  finish with a usage report), then the Step committed.

``measure``: generate one shape and size, record the append time per quarter,
then in this process time the open (journal checks), the Rebuild
(``RuntimeStateMachine.load``) under ``cProfile`` separately, and print one JSON
line with the times, the file size, the peak memory and the top functions.
Run with ``PYTHONPATH=src:tools:tests``. Standard library only.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.types.profile import PHASE1_SYNTHETIC
from agent_core.types.records import StepKind, StepStatus
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w


def _events(st: Any) -> int:
    import sqlite3

    conn = sqlite3.connect(f"file:{st.path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


def _shape_a(st: Any, target: int, marks: list[tuple[int, float]]) -> None:
    run = w.running_run(st.sm)
    while _events(st) < target:
        for _ in range(100):
            step = st.sm.create_step(run, StepKind.TOOL_CALL)
            step = st.sm.set_step_status(step, StepStatus.IN_PROGRESS)
            st.sm.set_step_status(step, StepStatus.CANCELLED)
        marks.append((_events(st), time.monotonic()))


def _shape_b(st: Any, target: int, marks: list[tuple[int, float]]) -> None:
    while _events(st) < target:
        for _ in range(25):
            run = w.running_run(st.sm)
            for _ in range(3):
                step = st.sm.create_step(run, StepKind.TOOL_CALL)
                step = st.sm.set_step_status(step, StepStatus.IN_PROGRESS)
                st.sm.set_step_status(step, StepStatus.CANCELLED)
        marks.append((_events(st), time.monotonic()))


def _shape_c(st: Any, target: int, marks: list[tuple[int, float]]) -> None:
    usage = lw.zero_usage()
    while _events(st) < target:
        for _ in range(5):
            run = w.running_run(st.sm)
            for _ in range(3):
                step = lw.step_in(st, run)
                attempt = lw.consume(st, lw.issued(st, step))
                gate = lw.gate(st)
                started = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0]
                gate.finish(started, SyntheticResult(outcome="COMPLETED", usage=usage, final=True))
                st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
        marks.append((_events(st), time.monotonic()))


SHAPES = {"A": _shape_a, "B": _shape_b, "C": _shape_c}


def measure(shape: str, events: int, top: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="p1-profile-") as tmp:
        st = lw.setup(Path(tmp))
        start = time.monotonic()
        marks: list[tuple[int, float]] = [(_events(st), start)]
        SHAPES[shape](st, events, marks)
        generate_s = time.monotonic() - start
        written = _events(st)
        # Append cost per event in each quarter of the generation.
        quarters = []
        for q in range(1, 5):
            lo = marks[(len(marks) - 1) * (q - 1) // 4]
            hi = marks[(len(marks) - 1) * q // 4]
            if hi[0] > lo[0]:
                quarters.append(round((hi[1] - lo[1]) / (hi[0] - lo[0]) * 1000, 3))
        st.journal.close()
        size = st.path.stat().st_size
        t0 = time.monotonic()
        journal = lw.open_journal(st.path, st.clock, st.keys)
        t1 = time.monotonic()
        profiler = cProfile.Profile()
        profiler.enable()
        sm = RuntimeStateMachine.load(
            journal,
            ids=IdGenerator(b.SITE, SystemClock(), OsRandomSource()),
            clock=SystemClock(),
            verifier=w.AcceptSyntheticEvidence(),
            tenant_id=b.TENANT,
            engine=b.ENGINE,
            policy_version="policy-1",
            profile=PHASE1_SYNTHETIC,
        )
        profiler.disable()
        t2 = time.monotonic()
        entities = {
            "tasks": len(sm.state.tasks),
            "runs": len(sm.state.runs),
            "steps": len(sm.state.steps),
            "attempts": len(sm.state.attempts),
        }
        journal.close()
        buf = io.StringIO()
        stats = pstats.Stats(profiler, stream=buf)
        stats.sort_stats("tottime").print_stats(top)
        lines = [
            line.strip()
            for line in buf.getvalue().splitlines()
            if line.strip() and line.strip()[0].isdigit()
        ]
    return {
        "shape": shape,
        "events": written,
        "entities": entities,
        "bytes": size,
        "generate_s": round(generate_s, 3),
        "append_ms_per_event_by_quarter": quarters,
        "open_verify_s": round(t1 - t0, 3),
        "rebuild_s_profiled": round(t2 - t1, 3),
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "top_tottime": lines[:top],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shape", choices=sorted(SHAPES), required=True)
    parser.add_argument("--events", type=int, required=True)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(measure(args.shape, args.events, args.top)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
