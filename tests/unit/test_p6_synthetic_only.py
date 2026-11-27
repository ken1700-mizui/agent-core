"""P-6: Phase 1 is synthetic only, enforced at run time (PHASE_1_IMPLEMENTATION_PLAN
section 2, F07-1 and F07-3; regression tests, written first).

F07-1: the Runtime starts only under the ``phase1_synthetic`` profile. The check
is on the Runtime's start path (``RuntimeStateMachine.load``) and, before the
journal is opened, on the CLI home's configuration; anything else (another
profile, a missing one, a value of another type) is refused before any state
changes.

F07-3: the PDP allows only a Run whose owner-installed grant names a synthetic
task definition that the owner's policy trusts. The attribute comes from the
owner's signed records (policy, grant), never from the model; a missing,
unknown or untrusted definition is refused, and so is installing such a grant.
This binds the Run to a managed definition; it does not prove that the data in
it is not real data.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_core.adapters.system import OsRandomSource, SystemClock
from agent_core.identity.generator import IdGenerator
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.safety import pdp
from agent_core.types.cost import BudgetScopeType
from agent_core.types.errors import AgentCoreError, PermitRejectedError, RuntimeProfileError
from agent_core.types.ledger import InstallRunGrant
from agent_core.types.profile import PHASE1_SYNTHETIC, require_phase1_profile
from agent_core.types.safety import DenyReason, SyntheticTaskDefinition
from agent_core_cli import app, demo
from support import builders as b
from support import ledger_world as lw
from support import runtime_world as w
from support import safety_world as s
from support.fakes import InMemoryJournal

# ================================================================ F07-1 the profile


def _load(journal: Any, profile: Any) -> RuntimeStateMachine:
    return RuntimeStateMachine.load(
        journal,
        ids=IdGenerator(b.SITE, SystemClock(), OsRandomSource()),
        clock=SystemClock(),
        verifier=w.AcceptSyntheticEvidence(),
        tenant_id=b.TENANT,
        engine=b.ENGINE,
        policy_version="policy-1",
        profile=profile,
    )


def test_the_runtime_starts_under_the_synthetic_profile() -> None:
    sm = _load(InMemoryJournal(), PHASE1_SYNTHETIC)
    assert sm.state.tasks == {}


@pytest.mark.parametrize(
    "profile", ["phase2", "PHASE1_SYNTHETIC", "phase1_synthetic ", "", None, 1, b"phase1_synthetic"]
)
def test_any_other_profile_is_refused_before_anything_is_read(profile: Any) -> None:
    class Untouched(InMemoryJournal):
        def read_tenant(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the journal was read before the profile was checked")

    with pytest.raises(RuntimeProfileError):
        _load(Untouched(), profile)


def test_the_profile_check_is_one_function_for_cli_and_runtime() -> None:
    require_phase1_profile(PHASE1_SYNTHETIC)
    with pytest.raises(RuntimeProfileError):
        require_phase1_profile("phase1_synthetic_live")


class _Cli:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.clock = lw.ManualClock()

    def call(self, *argv: str) -> tuple[int, str, str]:
        import io

        out, err = io.StringIO(), io.StringIO()
        code = app.main(list(argv), stdout=out, stderr=err, reader=lambda: "", clock=self.clock)
        return code, out.getvalue(), err.getvalue()


def _config(home: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((home / "config.json").read_text())
    return data


def _write_config(home: Path, data: dict[str, Any]) -> None:
    path = home / "config.json"
    path.chmod(0o600)
    path.write_text(json.dumps(data, sort_keys=True))


def _journal_state(home: Path) -> tuple[Any, ...]:
    conn = sqlite3.connect(f"file:{home / 'journal.db'}?mode=ro", uri=True)
    try:
        owner = conn.execute("SELECT epoch, instance_id FROM runtime_owner").fetchall()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()
    finally:
        conn.close()
    return (tuple(owner), events)


def test_a_new_home_declares_the_synthetic_profile(tmp_path: Path) -> None:
    cli = _Cli(tmp_path)
    assert cli.call("init", "--home", str(cli.home))[0] == 0
    assert _config(cli.home)["profile"] == PHASE1_SYNTHETIC
    assert cli.call("status", "--home", str(cli.home))[0] == 0


@pytest.mark.parametrize("change", ["other", "missing", "wrong-type"])
@pytest.mark.parametrize("command", ["status", "shell"])
def test_a_home_without_the_synthetic_profile_is_refused_unchanged(
    tmp_path: Path, change: str, command: str
) -> None:
    cli = _Cli(tmp_path)
    assert cli.call("init", "--home", str(cli.home))[0] == 0
    data = _config(cli.home)
    if change == "other":
        data["profile"] = "phase3b_live"
    elif change == "missing":
        data.pop("profile")
    else:
        data["profile"] = 1
    _write_config(cli.home, data)
    before = _journal_state(cli.home)
    code, _out, err = cli.call(command, "--home", str(cli.home))
    assert code != 0
    assert ("PROFILE_REFUSED" in err) if change != "wrong-type" else ("HOME_UNSAFE" in err)
    assert _journal_state(cli.home) == before  # no new owner epoch, nothing written


# ================================================================ F07-3 trusted synthetic tasks


TRUSTED = s.SYNTHETIC_TASK.ref()
OTHER = SyntheticTaskDefinition(kind="synthetic_task_definition", name="other", version=1).ref()


def _decide(**grant: Any) -> Any:
    at = s.ref()
    return pdp.decide(
        policy=s.policy(),
        grant=s.grant(at, **grant),
        descriptor=s.read_descriptor(at),
        registration=s.READ_TOOL,
        context=s.context(),
    )


def test_a_trusted_synthetic_task_is_allowed() -> None:
    assert _decide().allowed


@pytest.mark.parametrize("ref", [None, OTHER], ids=["missing", "untrusted"])
def test_a_missing_or_untrusted_task_definition_is_refused(ref: Any) -> None:
    decision = _decide(synthetic_task_ref=ref)
    assert not decision.allowed
    assert DenyReason.SYNTHETIC_TASK_UNTRUSTED in decision.reasons


def test_the_definition_must_be_trusted_by_the_current_policy() -> None:
    at = s.ref()
    decision = pdp.decide(
        policy=s.policy(trusted_synthetic_tasks=()),
        grant=s.grant(at),
        descriptor=s.read_descriptor(at),
        registration=s.READ_TOOL,
        context=s.context(),
    )
    assert DenyReason.SYNTHETIC_TASK_UNTRUSTED in decision.reasons


def test_the_demo_tasks_are_managed_definitions_the_cli_policy_trusts() -> None:
    definitions = [demo.task_definition(name) for name in demo.DEMOS]
    refs = {d.ref() for d in definitions if d is not None}
    assert set(demo.trusted_task_refs()) == refs
    assert len(refs) == len(demo.DEMOS)
    assert demo.task_definition("unknown") is None


@pytest.mark.parametrize("ref", [None, OTHER], ids=["missing", "untrusted"])
def test_a_grant_for_an_untrusted_task_is_not_installed(tmp_path: Path, ref: Any) -> None:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    at = lw.ref_of(st, st.sm.create_attempt(step))
    grant = s.grant(at, synthetic_task_ref=ref)
    with pytest.raises(PermitRejectedError, match="SYNTHETIC_TASK_UNTRUSTED"):
        st.owner(InstallRunGrant(op="install_run_grant", grant=grant))
    assert st.journal.run_grant(str(scopes.run_id)) is None


def test_after_a_refusal_nothing_runs_and_nothing_is_spent(tmp_path: Path) -> None:
    st = lw.setup(tmp_path)
    step = lw.step_in(st)
    scopes = lw.scopes_of(st.sm, step)
    at = s.ref(
        tenant_id=scopes.tenant_id,
        task_id=scopes.task_id,
        run_id=scopes.run_id,
        step_id=scopes.step_id,
    )
    with pytest.raises(AgentCoreError):
        st.owner(
            InstallRunGrant(op="install_run_grant", grant=s.grant(at, synthetic_task_ref=OTHER))
        )
    before = set(st.sm.state.attempts)
    with pytest.raises(AgentCoreError):
        lw.fixed_attempt(st, step, scopes)  # no grant: not even a reservation ID
    [attempt_id] = set(st.sm.state.attempts) - before
    assert st.sm.state.attempts[attempt_id].state.value == "VALIDATED"  # nothing fixed or run
    usage = st.journal.scope_usage(BudgetScopeType.RUN, str(scopes.run_id))
    assert (usage.spent_micro, usage.hold_micro) == (0, 0)
