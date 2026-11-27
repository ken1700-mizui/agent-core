"""QR-04 (independent quality review, MEDIUM): the order of a Run's writes and its
verification reads is taken from the journal, never from the order of the
decisions (regression tests, written first).

The journal records, for each Attempt, when it was started (DISPATCH_STARTED,
before the operation runs) and when it ended (after the operation returned).
A write provably committed before a verification read when its end precedes
the read's start; it provably came after the read when its start follows the
read's end. Anything else (overlap, an unknown outcome, a write still running)
is not a proven order, and the Run is not completed on it. The journal's order
is never taken as the file system's commit order beyond these bounds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters import sqlite_ledger
from agent_core.adapters.sqlite_ledger import _Span, _write_order_reasons
from agent_core.runtime import recovery as rc
from agent_core.runtime.execution_gate import SyntheticResult
from agent_core.safety.workspace import file_digest
from agent_core.types.errors import FileEffect, FileOperationFailedError
from agent_core.types.files import FileOperation, FileOutcome, TicketBinding
from agent_core.types.records import AttemptState, RunStatus, StepStatus
from agent_core.types.tools import ResourceAccess
from support import builders as b
from support import ledger_world as lw
from support import loop_world as lp
from support import safety_world as s
from support import tool_world as tw

A = AttemptState
R = RunStatus
BAD = "synthetic report draft\n"
X, Y = b.digest("content x"), b.digest("content y")
DEADLINES = rc.RecoveryDeadlines(recovering_ms=60_000, cancelling_ms=60_000)


# ================================================================ the rule, on journal positions


def _done(start: int, end: int) -> _Span:
    return _Span(state="COMPLETED", started=start, ended=end, unknown=False)


READ = _done(10, 11)


def test_a_write_ended_before_the_read_started_is_its_basis() -> None:
    assert _write_order_reasons([(X, _done(2, 3))], READ) == ([], X)


def test_the_last_write_is_the_last_by_the_journal_not_by_the_decision() -> None:
    # Listed (decided) X first, Y second; the journal shows X ended last.
    writes = [(X, _done(6, 7)), (Y, _done(2, 3))]
    assert _write_order_reasons(writes, READ) == ([], X)


def test_a_write_started_after_the_read_ended_makes_the_read_stale() -> None:
    reasons, _ = _write_order_reasons([(X, _done(2, 3)), (X, _done(12, 13))], READ)
    assert "WRITE_AFTER_VERIFICATION" in reasons


def test_a_write_overlapping_the_read_is_not_ordered() -> None:
    reasons, _ = _write_order_reasons([(X, _done(9, 12))], READ)
    assert reasons == ["WRITE_ORDER_UNPROVEN"]


def test_two_overlapping_writes_have_no_provable_last() -> None:
    reasons, _ = _write_order_reasons([(X, _done(2, 5)), (Y, _done(3, 6))], READ)
    assert reasons == ["WRITE_ORDER_UNPROVEN"]


def test_earlier_overlapping_writes_do_not_hide_a_provable_last_write() -> None:
    # The first two overlap each other; the third started after both ended, so it
    # is provably the last write (the file's content), and it ended before the read.
    writes = [(X, _done(2, 5)), (Y, _done(3, 6)), (Y, _done(7, 8))]
    assert _write_order_reasons(writes, READ) == ([], Y)


def test_the_last_write_overlapping_an_earlier_one_has_no_provable_last() -> None:
    writes = [(X, _done(2, 3)), (Y, _done(4, 7)), (X, _done(6, 8))]
    reasons, _ = _write_order_reasons(writes, READ)
    assert reasons == ["WRITE_ORDER_UNPROVEN"]


def test_an_unknown_or_late_reconciled_write_is_not_ordered() -> None:
    late = _Span(state="COMPLETED", started=2, ended=3, unknown=True)
    reasons, _ = _write_order_reasons([(X, late)], READ)
    assert reasons == ["WRITE_ORDER_UNPROVEN"]


def test_a_write_still_running_is_not_ordered() -> None:
    running = _Span(state="DISPATCH_STARTED", started=2, ended=None, unknown=False)
    reasons, _ = _write_order_reasons([(X, running)], READ)
    assert reasons == ["WRITE_IN_FLIGHT"]


def test_a_write_never_started_or_definitely_failed_wrote_nothing() -> None:
    never = _Span(state="PERMIT_ISSUED", started=None, ended=None, unknown=False)
    failed = _Span(state="FAILED_DEFINITE", started=2, ended=3, unknown=False)
    assert _write_order_reasons([(X, never), (Y, failed)], READ) == ([], None)


def test_without_any_write_the_read_alone_decides() -> None:
    assert _write_order_reasons([], READ) == ([], None)


def test_a_read_without_its_own_start_and_end_is_not_ordered() -> None:
    read = _Span(state="COMPLETED", started=None, ended=11, unknown=False)
    assert _write_order_reasons([(X, _done(2, 3))], read)[0] == ["READ_ORDER_UNPROVEN"]


# ================================================================ through the loop and the journal


class _Stand:
    """The broker's prepared write, for the gate's mechanics only: no file is touched
    (the journal's record is what the ledger orders)."""

    def __init__(self, workspace_ref: str, effect: FileEffect | None = None) -> None:
        self.workspace_ref, self._effect = workspace_ref, effect

    def write(self, authorization: Any, ticket: Any, redeem: Any, content: bytes) -> Any:
        if self._effect is not None:
            raise FileOperationFailedError("SYNTHETIC_CRASH", effect=self._effect)
        descriptor = authorization.descriptor
        (target,) = descriptor.target_resources
        change = descriptor.apply_change
        operation = FileOperation(
            access=ResourceAccess.WRITE,
            path=target.path,
            change=change,
            workspace_ref=descriptor.workspace_ref,
        )
        binding: TicketBinding = redeem(ticket, operation)
        return FileOutcome(
            binding=binding, result_digest=change.content_digest, result_size=change.content_size
        )

    def close(self) -> None:
        return None


def _decided(wd: lp.World, content: str) -> tuple[Any, Any]:
    """A write of ``content`` to the target, decided (its decision recorded) only."""
    st = wd.st
    step = lw.step_in(st, st.sm.state.runs[wd.run.run_id])
    inp = tw.write_input(lp.TARGET, content=content, expected=file_digest(lp.GOOD.encode()))
    attempt = lw.fixed_attempt(
        st,
        step,
        lw.scopes_of(st.sm, step),
        registration=s.WRITE_TOOL,
        canonical_input=inp,
        workspace_ref=wd.tree.digest,
    )
    return step, lw.reserve(st, attempt, 1_000)


def _executed(
    wd: lp.World, step: Any, attempt: Any, content: str, effect: FileEffect | None = None
) -> Any:
    """Executed by another gate (a writer that does not take part in the loop's
    hold), through the real ``execute_file`` path."""
    st = wd.st
    attempt = lw.consume(st, lw.issue(st, lw.intent(st, attempt)))
    gate = lw.gate(st)
    try:
        done = gate.execute_file(
            attempt,
            prepared=_Stand(wd.tree.digest, effect),  # type: ignore[arg-type]
            write_content=content.encode(),
            accept_read=lambda data: True,
            **lw.actual(st, attempt),
        ).attempt
    except FileOperationFailedError:
        done = st.sm.state.attempts[attempt.attempt_id]
    if done.state is A.COMPLETED:
        st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)
    return gate, done


def _drive(wd: lp.World) -> Any:
    outcome = wd.loop.run(wd.run.run_id)
    for _ in range(10):
        if outcome.status is not R.WAITING_APPROVAL or outcome.pending_action_hash is None:
            return outcome
        outcome = lp.approve_pending(wd, outcome)
    raise AssertionError("not settled")


def _before_verification(wd: lp.World, action: Any) -> None:
    real = wd.loop._verify
    done: list[bool] = []

    def wrapped(run_id: Any, session: Any) -> bool:
        if not done:
            done.append(True)
            action()
        return real(run_id, session)

    wd.loop._verify = wrapped  # type: ignore[method-assign]


# Two writes decided before either runs cannot happen through the runtime today
# (WP04-R03: no new Step while an Attempt of the Run is unresolved); the rule
# above covers that order, and the ledger no longer relies on WP04-R03 for it.


def _without_the_loops_precheck(wd: lp.World) -> None:
    """Only the journal's completion transaction decides (the loop's own check of
    the committed writes is advisory)."""
    wd.loop._committed_writes = lambda run_id: {}  # type: ignore[method-assign]


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The reasons the journal's completion transaction gives (QR-04)."""
    seen: list[str] = []
    real = sqlite_ledger._write_order_reasons

    def spy(*args: Any) -> Any:
        reasons, latest = real(*args)
        seen.extend(reasons)
        return reasons, latest

    monkeypatch.setattr(sqlite_ledger, "_write_order_reasons", spy)
    return seen


def test_the_normal_path_still_completes(tmp_path: Path) -> None:
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    assert _drive(wd).status is R.COMPLETED


def test_the_repair_loop_still_completes(tmp_path: Path) -> None:
    good = lp.write(lp.GOOD, expected=file_digest(BAD.encode()), use_id="t2")
    wd = lp.world(tmp_path, [lp.write(BAD), lp.done(), good, lp.done()])
    outcome = _drive(wd)
    assert outcome.status is R.COMPLETED
    assert outcome.repairs == 1


def test_a_write_after_the_confirmation_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _spy(monkeypatch)
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    real = wd.loop._read_all
    calls: list[int] = []

    def read_all(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:  # right after the confirmation reads: the same content again
            _executed(wd, *_decided(wd, lp.GOOD), lp.GOOD)
        return result

    wd.loop._read_all = read_all  # type: ignore[method-assign]
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.reason == "COMPLETION_REFUSED"
    assert "WRITE_AFTER_VERIFICATION" in seen


def test_a_late_reconciled_write_gives_no_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _spy(monkeypatch)
    wd = lp.world(tmp_path, [lp.write(), lp.done()])
    _without_the_loops_precheck(wd)

    def unknown_then_reconciled() -> None:
        step, attempt = _decided(wd, lp.GOOD)
        gate, done = _executed(wd, step, attempt, lp.GOOD, FileEffect.UNKNOWN)
        assert done.state is A.OUTCOME_UNKNOWN
        st = wd.st
        report = gate.execution_report(
            done, SyntheticResult(outcome="COMPLETED", usage=lw.zero_usage(), final=True)
        )
        deps = rc.RecoveryDeps(
            sm=st.sm,
            ledger=st.journal,
            owner=st.owner,
            clock=st.clock,
            report_key=st.keys.permit,
            journal=st.journal,
        )
        settled = rc.reconcile(deps, done.attempt_id, rc.ExecutionEvidence(report=report))
        assert settled.outcome is rc.Reconciled.EXECUTED
        st.sm.set_step_status(st.sm.state.steps[step.step_id], StepStatus.COMMITTED)

    _before_verification(wd, unknown_then_reconciled)
    outcome = _drive(wd)
    assert outcome.status is not R.COMPLETED
    assert outcome.reason == "COMPLETION_REFUSED"
    assert "WRITE_ORDER_UNPROVEN" in seen
