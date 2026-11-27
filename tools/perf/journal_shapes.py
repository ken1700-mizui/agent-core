"""P-7 S5: measure start-up, Rebuild, memory and append for journal shapes A, B, C.

Development tool, not part of Agent Core. Normal timing only (no profiler).

- ``generate --shape S --events N --out PATH``: a synthetic journal of about N
  events of shape ``A`` (one Run, many Steps), ``B`` (many Runs, three Steps
  each) or ``C`` (Runs whose Steps each run one tool Attempt through the whole
  permit path), written through the real Runtime path (``tests/support``
  worlds) with fixed synthetic keys. Prints the event count, the time, and the
  append time per event in each tenth of the generation.
- ``measure --journal PATH``: on a copy made by the caller, in this process,
  time the journal checks (read-only), the normal open and the Rebuild
  (``RuntimeStateMachine.load``), record the peak memory after the Rebuild,
  then time appending about 150 events to the loaded journal (a new Task and
  Run with Steps). Prints one JSON line.
- ``run --shapes ... --sizes ... --repeats R --work DIR``: generate each
  journal once, then measure it R times, each in a fresh child process with a
  time limit; a measurement over the limit is recorded as not measured.
  Prints the median, the maximum and every run.

``measure`` only uses functions that the version before P-7 also has, so the
same journals can be measured with the earlier code (``PYTHONPATH`` pointing at
its ``src`` and ``tests``). Run with ``PYTHONPATH=src:tools:tests``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_core.adapters import sqlite_journal, sqlite_ledger
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.types.ids import TenantId
from agent_core.types.records import StepKind, StepStatus
from agent_core.types.safety import LedgerKeys, SecretKey
from support import ledger_world as lw
from support import runtime_world as w

TENTHS = 10


def _keys() -> LedgerKeys:
    def key(label: str) -> SecretKey:
        return SecretKey(hashlib.sha256(f"p7-shapes-synthetic-{label}".encode()).digest())

    return LedgerKeys(owner=key("owner"), permit=key("permit"))


def _seq(st: Any) -> int:
    head = st.journal.head(st.sm.tenant_id)
    return 0 if head is None else int(head.tenant_seq)


def _steps_a(sm: Any, run: Any, count: int) -> None:
    for _ in range(count):
        step = sm.create_step(run, StepKind.TOOL_CALL)
        step = sm.set_step_status(step, StepStatus.IN_PROGRESS)
        sm.set_step_status(step, StepStatus.CANCELLED)


def _chunk_a(st: Any, state: dict[str, Any]) -> None:
    if "run" not in state:
        state["run"] = w.running_run(st.sm)
    _steps_a(st.sm, state["run"], 50)


def _chunk_b(st: Any, _state: dict[str, Any]) -> None:
    for _ in range(10):
        _steps_a(st.sm, w.running_run(st.sm), 3)


def _chunk_c(st: Any, _state: dict[str, Any]) -> None:
    usage = lw.zero_usage()
    for _ in range(2):
        run = w.running_run(st.sm)
        for _ in range(3):
            step = lw.step_in(st, run)
            attempt = lw.consume(st, lw.issued(st, step))
            gate = lw.gate(st)
            started = gate.start(gate.claim(attempt, **lw.actual(st, attempt)))[0]
            gate.finish(started, SyntheticResult(outcome="COMPLETED", usage=usage, final=True))
            st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)


CHUNKS = {"A": _chunk_a, "B": _chunk_b, "C": _chunk_c}


def generate(shape: str, events: int, out: Path) -> dict[str, Any]:
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    st = lw.setup(out.parent, out.name, keys=_keys())
    marks = [(_seq(st), time.monotonic())]
    state: dict[str, Any] = {}
    try:
        while marks[-1][0] < events:
            CHUNKS[shape](st, state)
            marks.append((_seq(st), time.monotonic()))
    finally:
        st.journal.close()
    tenths = []
    for t in range(1, TENTHS + 1):
        lo = marks[(len(marks) - 1) * (t - 1) // TENTHS]
        hi = marks[(len(marks) - 1) * t // TENTHS]
        if hi[0] > lo[0]:
            tenths.append(round((hi[1] - lo[1]) / (hi[0] - lo[0]) * 1000, 3))
    return {
        "shape": shape,
        "events": _count(out),
        "seconds": round(marks[-1][1] - marks[0][1], 3),
        "bytes": out.stat().st_size,
        "append_ms_per_event_by_tenth": tenths,
    }


def _count(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


def _tenant(path: Path) -> TenantId:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return TenantId(conn.execute("SELECT tenant_id FROM tenant_head").fetchone()[0])
    finally:
        conn.close()


def measure(path: Path) -> dict[str, Any]:
    tenant = _tenant(path)
    size = path.stat().st_size
    events = _count(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        t0 = time.monotonic()
        sqlite_journal._check_database(cur)
        sqlite_journal._verify_tenant(cur, tenant)
        sqlite_ledger.verify_consistency(cur)
        t1 = time.monotonic()
        cur.execute("ROLLBACK")
    finally:
        conn.close()
    clock = lw.ManualClock()
    t2 = time.monotonic()
    journal = lw.open_journal(path, clock, _keys())
    t3 = time.monotonic()
    try:
        sm = w.machine(journal)
        t4 = time.monotonic()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        entities = {"runs": len(sm.state.runs), "steps": len(sm.state.steps)}
        entities["attempts"] = len(sm.state.attempts)
        before = journal.head(tenant)
        t5 = time.monotonic()
        _steps_a(sm, w.running_run(sm), 50)
        t6 = time.monotonic()
        after = journal.head(tenant)
    finally:
        journal.close()
    appended = int(after.tenant_seq) - int(before.tenant_seq)  # type: ignore[union-attr]
    return {
        "events": events,
        "bytes": size,
        "entities": entities,
        "verify_s": t1 - t0,
        "open_s": t3 - t2,
        "rebuild_s": t4 - t3,
        "startup_total_s": t4 - t2,
        "max_rss_kib_after_rebuild": rss,
        "append_events": appended,
        "append_ms_per_event": (t6 - t5) / appended * 1000,
    }


def run(
    shapes: list[str], sizes: list[int], repeats: int, work: Path, limit_s: int
) -> dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for shape in shapes:
        for size in sizes:
            name = f"{shape}-{size}"
            base = work / f"journal-{name}.db"
            made = None
            if not base.exists():
                made = generate(shape, size, base)
            rows: list[dict[str, Any]] = []
            not_measured = 0
            for i in range(repeats):
                copy = work / f"journal-{name}-run{i}.db"
                shutil.copyfile(base, copy)
                try:
                    out = subprocess.run(  # noqa: S603 (this script, a fixed argument list)
                        [sys.executable, __file__, "measure", "--journal", str(copy)],
                        check=True,
                        capture_output=True,
                        text=True,
                        env={**os.environ},
                        timeout=limit_s,
                    )
                    rows.append(json.loads(out.stdout.strip().splitlines()[-1]))
                except subprocess.TimeoutExpired:
                    not_measured += 1
                finally:
                    for suffix in ("", "-wal", "-shm"):
                        Path(f"{copy}{suffix}").unlink(missing_ok=True)
            suffixes = ("_s", "_kib_after_rebuild", "_ms_per_event")
            keys = [k for k in (rows[0] if rows else {}) if k.endswith(suffixes)]
            results[name] = {
                "generated": made,
                "events": rows[0]["events"] if rows else None,
                "not_measured_over_limit": not_measured,
                "median": {k: statistics.median(r[k] for r in rows) for k in keys},
                "max": {k: max(r[k] for r in rows) for k in keys},
                "runs": rows,
            }
            print(json.dumps({name: results[name]["median"]}), file=sys.stderr, flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--shape", choices=sorted(CHUNKS), required=True)
    g.add_argument("--events", type=int, required=True)
    g.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("measure")
    m.add_argument("--journal", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--shapes", nargs="+", choices=sorted(CHUNKS), required=True)
    r.add_argument("--sizes", type=int, nargs="+", required=True)
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--work", type=Path, required=True)
    r.add_argument("--limit-s", type=int, default=900)
    args = parser.parse_args()
    if args.command == "generate":
        print(json.dumps(generate(args.shape, args.events, args.out)))
    elif args.command == "measure":
        print(json.dumps(measure(args.journal)))
    else:
        print(json.dumps(run(args.shapes, args.sizes, args.repeats, args.work, args.limit_s)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
