"""P-1 (owner decision D9): measure opening a synthetic Journal (development tool).

Not part of Agent Core: a measurement script for the procedure registered in
``docs/design/phase-1/P-1_JOURNAL_MEASUREMENT.md`` before it was run.

- ``generate --events N --out PATH``: a new synthetic journal of about N events
  written through the real Runtime path (``RuntimeStateMachine`` on a
  ``SqliteJournal``, the ``phase1_synthetic`` profile): one Task and Run, then
  Steps that are created, started and cancelled (3 events each). Synthetic
  identifiers and digests only; no file, model, tool or network.
- ``measure --journal PATH``: in this process, on a copy made by the caller,
  time (monotonic) each part of opening: the database checks, the hash-chain
  verification of every tenant, the ledger's consistency check (read-only, the
  journal's own functions), then the normal open (all of the above plus a new
  owner epoch) and the Rebuild of the runtime state; print one JSON line with
  the times, the file size and the peak resident memory (``ru_maxrss``).
- ``run --sizes ... --repeats R --work DIR``: generate each size once, then for
  each repetition copy the journal and measure it in a fresh child process;
  print the median and the maximum of each figure.

Uses only the standard library and Agent Core itself (no new dependency).
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
from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.types.ids import RunGrantId, TenantId
from agent_core.types.profile import PHASE1_SYNTHETIC
from agent_core.types.records import RunStatus, StepKind, StepStatus
from agent_core.types.safety import LedgerKeys, SecretKey
from agent_core.types.values import Budget, Currency, sha256_digest
from agent_core_cli import demo
from agent_core_cli.environment import ENGINE, AcceptSyntheticEvidence

KEYS_SEED = b"p1-measurement-synthetic-keys-not-secret"


def _keys() -> LedgerKeys:
    """Fixed synthetic keys (measurement only; they protect nothing real)."""
    return LedgerKeys(
        owner=SecretKey(hashlib.sha256(KEYS_SEED + b"owner").digest()),
        permit=SecretKey(hashlib.sha256(KEYS_SEED + b"permit").digest()),
    )


def _machine(journal: SqliteJournal, tenant: TenantId) -> RuntimeStateMachine:
    return RuntimeStateMachine.load(
        journal,
        ids=IdGenerator(demo.SITE, SystemClock(), OsRandomSource()),
        clock=SystemClock(),
        verifier=AcceptSyntheticEvidence(),
        tenant_id=tenant,
        engine=ENGINE,
        policy_version=demo.POLICY_VERSION,
        profile=PHASE1_SYNTHETIC,
    )


def generate(events: int, out: Path) -> dict[str, Any]:
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    clock = SystemClock()
    journal = SqliteJournal.open(out, clock=clock, random=OsRandomSource(), keys=_keys())
    ids = IdGenerator(demo.SITE, clock, OsRandomSource())
    tenant = ids.new(TenantId)
    started = time.monotonic()
    try:
        sm = _machine(journal, tenant)
        task = sm.create_task(
            goal_ref=sha256_digest(b"p1 synthetic measurement task"),
            done_criteria=(sha256_digest(b"p1 synthetic criterion"),),
            budget_total=Budget(currency=Currency.XTU, micro_units=1_000_000),
            caps_total=demo.task_caps(),
        )
        run = sm.start_run(task, run_grant_ref=ids.new(RunGrantId))
        run = sm.set_run_status(run, RunStatus.QUEUED)
        run = sm.set_run_status(run, RunStatus.RUNNING)
        written = 4
        while written + 3 <= events:
            step = sm.create_step(run, StepKind.TOOL_CALL)
            step = sm.set_step_status(step, StepStatus.IN_PROGRESS)
            sm.set_step_status(step, StepStatus.CANCELLED)
            written += 3
    finally:
        journal.close()
    return {
        "events": _count(out),
        "seconds": round(time.monotonic() - started, 3),
        "bytes": out.stat().st_size,
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
        t1 = time.monotonic()
        sqlite_journal._verify_tenant(cur, tenant)
        t2 = time.monotonic()
        sqlite_ledger.verify_consistency(cur)
        t3 = time.monotonic()
        cur.execute("ROLLBACK")
    finally:
        conn.close()
    t4 = time.monotonic()
    journal = SqliteJournal.open(path, clock=SystemClock(), random=OsRandomSource(), keys=_keys())
    t5 = time.monotonic()
    try:
        sm = _machine(journal, tenant)
        t6 = time.monotonic()
        steps = len(sm.state.steps)
    finally:
        journal.close()
    return {
        "events": events,
        "bytes": size,
        "steps": steps,
        "check_database_s": t1 - t0,
        "chain_verify_s": t2 - t1,
        "ledger_consistency_s": t3 - t2,
        "verify_total_s": t3 - t0,
        "open_s": t5 - t4,
        "rebuild_s": t6 - t5,
        "startup_total_s": t6 - t4,
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def run(sizes: list[int], repeats: int, work: Path) -> dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for size in sizes:
        base = work / f"journal-{size}.db"
        made = generate(size, base)
        rows = []
        for i in range(repeats):
            copy = work / f"journal-{size}-run{i}.db"
            shutil.copyfile(base, copy)
            out = subprocess.run(  # noqa: S603 (this script, a fixed argument list)
                [sys.executable, __file__, "measure", "--journal", str(copy)],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ},
            )
            rows.append(json.loads(out.stdout.strip().splitlines()[-1]))
            copy.unlink()
        keys = [k for k in rows[0] if k.endswith("_s") or k == "max_rss_kib"]
        results[str(size)] = {
            "generated": made,
            "events": rows[0]["events"],
            "bytes": rows[0]["bytes"],
            "median": {k: statistics.median(r[k] for r in rows) for k in keys},
            "max": {k: max(r[k] for r in rows) for k in keys},
            "runs": rows,
        }
        print(json.dumps({str(size): results[str(size)]["median"]}), file=sys.stderr, flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--events", type=int, required=True)
    g.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("measure")
    m.add_argument("--journal", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--sizes", type=int, nargs="+", required=True)
    r.add_argument("--repeats", type=int, default=5)
    r.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        print(json.dumps(generate(args.events, args.out)))
    elif args.command == "measure":
        print(json.dumps(measure(args.journal)))
    else:
        print(json.dumps(run(args.sizes, args.repeats, args.work)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
