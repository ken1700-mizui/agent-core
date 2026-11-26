"""The CLI's home and session: the composition of one synthetic Runtime.

The home is a private directory (0700) that ``init`` creates and nothing else
names:

- ``owner.key`` (0600): the owner channel key and the permit key (hex). Read
  only by this module; never printed, never taken from the command line or the
  environment. Phase 1 keeps them in a file; real key management is a later
  phase (C2).
- ``owner.seq`` (0600): the owner's own batch counter (owner batches apply in
  increasing order, each once; WP09-R05). Written before each signing.
- ``config.json`` (0600): the tenant, owner and workspace identifiers.
- ``journal.db`` and its lock file: the journal. Opening it takes the
  Runtime's OS lock (one Runtime at a time); it is released on ``close`` and by
  the kernel if the process dies.
- ``area/tree``: the managed synthetic work tree (WP-08).

A ``Session`` is one open Runtime: the runtime state rebuilt from the journal,
the execution gate, the file broker and one agent loop per Run started in this
session. Its owner operations go through the owner channel and the gate; its
approvals are exact approvals of one action hash; the ledger decides.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from agent_core.adapters.file_broker import SyntheticFileBroker, create_synthetic_workspace
from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.identity.generator import IdGenerator
from agent_core.ports.clock import Clock
from agent_core.ports.files import FileOperationPort
from agent_core.ports.model import ModelPort
from agent_core.runtime.agent_loop import AgentLoop, LoopConfig, LoopDeps, LoopOutcome, TaskProfile
from agent_core.runtime.context_assembly import RuleCatalog
from agent_core.runtime.execution_gate import SyntheticExecutionGate
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.runtime.verification import CriteriaCatalog
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.safety.workspace import manifest_digest, manifest_key
from agent_core.tool.registry import ToolRegistry
from agent_core.types.context import SyntheticTexts
from agent_core.types.cost import BudgetScopeType, CounterKind
from agent_core.types.ids import (
    ApprovalId,
    PepInstanceId,
    RunGrantId,
    RunId,
    TenantId,
    UserId,
)
from agent_core.types.ledger import (
    InstallPolicy,
    InstallPriceTable,
    InstallRunGrant,
    LedgerBatch,
    LedgerOp,
    RecordApproval,
    ResumeScope,
    SetBudgetCap,
    SetCounterCap,
    SetRunWindow,
    SetStepCapDefault,
    StopScope,
)
from agent_core.types.records import Attempt, AttemptState, Run, RunStatus, Task
from agent_core.types.safety import (
    ApproverRole,
    ExactApproval,
    LedgerKeys,
    ModelProviderKind,
    PathScope,
    Policy,
    RunGrant,
    SecretKey,
    StopScopeType,
    StopStatus,
)
from agent_core.types.tools import ToolEffectClass, ToolRegistration
from agent_core.types.values import EngineName, EngineVersion, sha256_digest
from agent_core_cli import demo

KEY_FILE: Final = "owner.key"
SEQ_FILE: Final = "owner.seq"
CONFIG_FILE: Final = "config.json"
JOURNAL_FILE: Final = "journal.db"
AREA_DIR: Final = "area"
ENGINE: Final = EngineVersion(engine=EngineName.RUNTIME, version="0.1.0")
_FILE_FLAGS: Final = os.O_NOFOLLOW | os.O_CLOEXEC


class CliError(Exception):
    """A refusal with a fixed reason code (never a free message)."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Hooks:
    """Test seams: wrap the scripted model or the file port of a session."""

    model_wrapper: Callable[[ModelPort], ModelPort] | None = None
    files_wrapper: Callable[[FileOperationPort, Session], FileOperationPort] | None = None


# ---------------------------------------------------------------- private files


def _write_private(path: Path, data: bytes) -> None:
    """A new private file (0600); never follows or replaces an existing entry."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_private(path: Path, limit: int = 4_096) -> bytes:
    """A private regular file of this user (0600, one link); otherwise refused."""
    try:
        fd = os.open(path, os.O_RDONLY | _FILE_FLAGS)
    except FileNotFoundError:
        raise CliError("HOME_INCOMPLETE") from None
    except OSError:
        raise CliError("HOME_UNSAFE") from None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise CliError("HOME_UNSAFE")
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def _replace_private(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".new")
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    _write_private(temp, data)
    os.replace(temp, path)


def _check_home(home: Path) -> None:
    try:
        info = os.lstat(home)
    except FileNotFoundError:
        raise CliError("HOME_MISSING") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
    ):
        raise CliError("HOME_UNSAFE")


def _load_keys(home: Path) -> LedgerKeys:
    try:
        data = json.loads(_read_private(home / KEY_FILE))
        return LedgerKeys(
            owner=SecretKey(bytes.fromhex(data["owner"])),
            permit=SecretKey(bytes.fromhex(data["permit"])),
        )
    except CliError:
        raise
    except (ValueError, KeyError, TypeError):
        raise CliError("HOME_UNSAFE") from None


@dataclass(frozen=True)
class HomeConfig:
    tenant_id: TenantId
    owner_id: UserId
    workspace_ref: str

    @classmethod
    def load(cls, home: Path) -> HomeConfig:
        try:
            data = json.loads(_read_private(home / CONFIG_FILE))
            if data.get("version") != 1:
                raise CliError("HOME_UNSAFE")
            return cls(
                tenant_id=TenantId(data["tenant_id"]),
                owner_id=UserId(data["owner_id"]),
                workspace_ref=str(data["workspace_ref"]),
            )
        except CliError:
            raise
        except (ValueError, KeyError, TypeError):
            raise CliError("HOME_UNSAFE") from None


class OwnerSequence:
    """The owner's own record of its last batch number (WP09-R05)."""

    def __init__(self, home: Path) -> None:
        self._path = home / SEQ_FILE

    def next(self) -> int:
        try:
            current = int(_read_private(self._path).decode("ascii").strip())
        except (ValueError, UnicodeDecodeError):
            raise CliError("HOME_UNSAFE") from None
        _replace_private(self._path, f"{current + 1}\n".encode())
        return current + 1


# ---------------------------------------------------------------- policy and grants


def policy_for(config: HomeConfig, tools: tuple[ToolRegistration, ...]) -> Policy:
    return Policy(
        policy_version=demo.POLICY_VERSION,
        tenant_id=config.tenant_id,
        tools=demo.pins(tools),
        allowed_effects=(
            ToolEffectClass.READ_ONLY,
            ToolEffectClass.IRREVERSIBLE,
            ToolEffectClass.MODEL_CALL,
        ),
        approval_effects=(ToolEffectClass.IRREVERSIBLE,),
        approval_tools=(),
        model_providers=(ModelProviderKind.FAKE,),
        deny_resources=("workspace/secret/**",),
        trusted_synthetic_workspaces=(config.workspace_ref,),
        price_table_id=demo.PRICE_TABLE.price_table_id,
        price_table_version=demo.PRICE_TABLE.version,
        max_permit_ttl_ms=60_000,
        max_approval_ttl_ms=600_000,
    )


class AcceptSyntheticEvidence:
    """Phase 1 evidence acceptance for this synthetic Runtime. The checks that
    matter are elsewhere: the ledger's pairing of every transition with its
    operation, the gate's MAC-signed reports and the broker's ticket. An
    independent evidence verifier is not part of Phase 1 (unverified item)."""

    def verify(self, evidence: Any, *, subject: Any) -> None:
        return None


# ---------------------------------------------------------------- init


def init_home(home: Path, *, clock: Clock) -> None:
    """A new home: keys, config, the work tree, the journal with the owner's price
    table, policy and the wide caps. Refuses an existing path."""
    if home.exists() or home.is_symlink():
        raise CliError("HOME_EXISTS")
    if not home.parent.is_dir():
        raise CliError("HOME_PARENT_MISSING")
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    keys = LedgerKeys(
        owner=SecretKey(secrets.token_bytes(32)), permit=SecretKey(secrets.token_bytes(32))
    )
    _write_private(
        home / KEY_FILE,
        json.dumps(
            {"owner": keys.owner.reveal().hex(), "permit": keys.permit.reveal().hex()}
        ).encode(),
    )
    _write_private(home / SEQ_FILE, b"0\n")
    area = home / AREA_DIR
    area.mkdir(mode=0o700)
    area.chmod(0o700)
    signed = create_synthetic_workspace(
        area=area,
        workspace_id=demo.WORKSPACE_ID,
        files=dict(demo.WORKSPACE_FILES),
        key=manifest_key(keys.owner),
        now_ms=clock.now_ms(),
    )
    ids = IdGenerator(demo.SITE, clock, OsRandomSource())
    config = HomeConfig(
        tenant_id=ids.new(TenantId),
        owner_id=ids.new(UserId),
        workspace_ref=manifest_digest(signed.manifest),
    )
    _write_private(
        home / CONFIG_FILE,
        json.dumps(
            {
                "version": 1,
                "tenant_id": str(config.tenant_id),
                "owner_id": str(config.owner_id),
                "workspace_ref": config.workspace_ref,
            },
            sort_keys=True,
        ).encode(),
    )
    journal = SqliteJournal.open(
        home / JOURNAL_FILE, clock=clock, random=OsRandomSource(), keys=keys
    )
    try:
        _, tools = demo.registry()
        owner = _Owner(OwnerApprovalChannel(keys.owner), OwnerSequence(home))
        wide = (
            (BudgetScopeType.TENANT, str(config.tenant_id)),
            (BudgetScopeType.USER, str(config.owner_id)),
            (BudgetScopeType.PERIOD, demo.PERIOD),
        )
        owner.apply(
            journal,
            InstallPriceTable(op="install_price_table", table=demo.PRICE_TABLE),
            InstallPolicy(op="install_policy", policy=policy_for(config, tools)),
            *(
                SetBudgetCap(
                    op="set_budget_cap",
                    scope_type=kind,
                    scope_id=scope,
                    cap=demo.xtu(demo.WIDE_CAP),
                )
                for kind, scope in wide
            ),
        )
    finally:
        journal.close()


class _Owner:
    """Signs owner batches with the owner channel key and the owner's counter."""

    def __init__(self, channel: OwnerApprovalChannel, sequence: OwnerSequence) -> None:
        self.channel = channel
        self._sequence = sequence

    def sign(self, *ops: LedgerOp) -> tuple[LedgerBatch, str]:
        batch = LedgerBatch(owner_seq=self._sequence.next(), ops=ops)
        return batch, self.channel.sign_ledger(batch)

    def apply(self, journal: SqliteJournal, *ops: LedgerOp) -> None:
        batch, mac = self.sign(*ops)
        journal.apply_owner_ledger(batch, owner_mac=mac)


# ---------------------------------------------------------------- the session


@dataclass
class _RunEntry:
    loop: AgentLoop
    demo: str


@dataclass
class Session:
    home: Path
    clock: Clock
    monotonic: Callable[[], int]
    journal: SqliteJournal
    sm: RuntimeStateMachine
    gate: SyntheticExecutionGate
    files: FileOperationPort
    config: HomeConfig
    owner: _Owner
    ids: IdGenerator
    registry: ToolRegistry
    tools: tuple[ToolRegistration, ...]
    hooks: Hooks
    runs: dict[RunId, _RunEntry] = field(default_factory=dict)
    current: RunId | None = None
    closed: bool = False

    # -------------------------------------------------------- lifecycle

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.journal.close()

    # -------------------------------------------------------- owner operations

    def _owner_ops(self, *ops: LedgerOp) -> None:
        # Under the gate lock, like a stop (C1 11.1).
        self.gate.stop(self.owner.sign(*ops))

    def stopped(self, run_id: RunId | None = None) -> bool:
        scopes = {("GLOBAL", "global"), ("TENANT", str(self.config.tenant_id))}
        if run_id is not None:
            scopes.add(("RUN", str(run_id)))
        return any(
            (e.scope_type.value, e.scope_id) in scopes
            for e in self.journal.stop_entries()
            if e.status is StopStatus.STOPPED
        )

    def scope_id(self, kind: str, run_id: RunId | None) -> tuple[StopScopeType, str]:
        if kind == "global":
            return StopScopeType.GLOBAL, "global"
        if kind == "tenant":
            return StopScopeType.TENANT, str(self.config.tenant_id)
        if run_id is None:
            raise CliError("RUN_NOT_FOUND")
        return StopScopeType.RUN, str(run_id)

    def record_stop(self, kind: str, run_id: RunId | None = None) -> None:
        """Only the owner's signed stop (the loop sees it at its next check)."""
        scope_type, scope_id = self.scope_id(kind, run_id)
        self._owner_ops(StopScope(op="stop_scope", scope_type=scope_type, scope_id=scope_id))

    def stop(self, kind: str, run_id: RunId | None = None) -> list[tuple[RunId, LoopOutcome]]:
        """The owner's stop, then every affected Run is moved to its stop state."""
        targets: list[RunId] = (
            [run_id] if kind == "run" and run_id is not None else self._open_runs()
        )
        self.record_stop(kind, run_id)
        return [(r, self._loop_for(r).run(r)) for r in targets]

    def resume_scope(self, kind: str, run_id: RunId | None, stop_seq: int) -> None:
        scope_type, scope_id = self.scope_id(kind, run_id)
        self._owner_ops(
            ResumeScope(
                op="resume_scope", scope_type=scope_type, scope_id=scope_id, stop_seq=stop_seq
            )
        )

    # -------------------------------------------------------- runs

    def _open_runs(self) -> list[RunId]:
        terminal = {
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.ABORTED_SAFETY,
        }
        return [r.run_id for r in self.sm.state.runs.values() if r.status not in terminal]

    def resolve_run(self, ref: str | None) -> RunId:
        runs = self.sm.state.runs
        if ref is None:
            if self.current is None:
                raise CliError("RUN_NOT_FOUND")
            return self.current
        if ref == "last":
            if not runs:
                raise CliError("RUN_NOT_FOUND")
            return list(runs)[-1]
        for run_id in runs:
            if str(run_id) == ref:
                return run_id
        raise CliError("RUN_NOT_FOUND")

    def _loop_for(self, run_id: RunId) -> AgentLoop:
        entry = self.runs.get(run_id)
        if entry is not None:
            return entry.loop
        # A Run of an earlier session: a loop without its transcript, which can
        # only report or stop it (WP15-R01: never continued as new).
        task = self.sm.state.tasks[self.sm.state.runs[run_id].task_id]
        loop = self._make_loop(None, task, demo.ScriptedFakeModel([]))
        self.runs[run_id] = _RunEntry(loop, "earlier")
        return loop

    def _make_loop(
        self, task_demo: demo.DemoTask | None, task: Task, model: ModelPort
    ) -> AgentLoop:
        if self.hooks.model_wrapper is not None:
            model = self.hooks.model_wrapper(model)
        criteria = CriteriaCatalog(criteria=(task_demo.criterion,) if task_demo else ())
        texts = (
            SyntheticTexts.of(task_demo.goal, task_demo.criterion.text())
            if task_demo
            else SyntheticTexts(entries=())
        )
        config = LoopConfig(
            tenant_id=self.config.tenant_id,
            workspace_ref=self.config.workspace_ref,
            profile=demo.CONTEXT_PROFILE,
            params=demo.PARAMS,
            price_table=demo.PRICE_TABLE,
            rules=RuleCatalog(rules=()),
            texts=texts,
            criteria=criteria,
            tasks=(TaskProfile(goal_ref=task.goal_ref, complexity="simple"),),
            limits=demo.LIMITS,
            cost_cap_micro=demo.COST_CAP_PER_ACTION,
            user_input=demo.REQUEST,
        )
        deps = LoopDeps(
            sm=self.sm,
            ledger=self.journal,
            gate=self.gate,
            model=model,
            registry=self.registry,
            tools=self.tools,
            files=self.files,
            clock=self.clock,
            monotonic=self.monotonic,
            new_id=self.ids.new,
        )
        return AgentLoop(deps, config)

    def start(self, name: str) -> tuple[Task, Run, LoopOutcome]:
        if self.stopped():
            raise CliError("SCOPE_STOPPED")
        task_demo = demo.demo_task(name, len(self.sm.state.tasks) + 1)
        if task_demo is None:
            raise CliError("UNKNOWN_DEMO")
        criterion = task_demo.criterion
        task = self.sm.create_task(
            goal_ref=sha256_digest(task_demo.goal.encode()),
            done_criteria=(criterion.ref(),),
            budget_total=demo.xtu(demo.TASK_CAP),
            caps_total=demo.task_caps(),
        )
        run = self.sm.start_run(task, run_grant_ref=self.ids.new(RunGrantId))
        run = self.sm.set_run_status(run, RunStatus.QUEUED)
        run = self.sm.set_run_status(run, RunStatus.RUNNING)
        self._install_run(task, run)
        loop = self._make_loop(task_demo, task, demo.ScriptedFakeModel(list(task_demo.replies)))
        self.runs[run.run_id] = _RunEntry(loop, name)
        self.current = run.run_id
        return task, run, loop.run(run.run_id)

    def _install_run(self, task: Task, run: Run) -> None:
        now = self.clock.now_ms()
        grant = RunGrant(
            run_grant_id=run.run_grant_ref,
            tenant_id=self.config.tenant_id,
            user_id=self.config.owner_id,
            period_id=demo.PERIOD,
            experiment_id=None,
            task_id=task.task_id,
            run_id=run.run_id,
            tools=demo.pins(self.tools),
            files=PathScope(read=("workspace/**",), write=("workspace/out/**",)),
            model_profiles=(demo.PROFILE_REF,),
            max_cost_per_action=demo.xtu(demo.COST_CAP_PER_ACTION),
            policy_version=demo.POLICY_VERSION,
            access_epoch_at_grant=self.journal.access_epoch(str(self.config.tenant_id)),
            issued_at_ms=now,
            expires_at_ms=now + demo.GRANT_TTL_MS,
        )
        ops: list[LedgerOp] = [
            InstallRunGrant(op="install_run_grant", grant=grant),
            SetBudgetCap(
                op="set_budget_cap",
                scope_type=BudgetScopeType.RUN,
                scope_id=str(run.run_id),
                cap=demo.xtu(demo.RUN_CAP),
            ),
            SetBudgetCap(
                op="set_budget_cap",
                scope_type=BudgetScopeType.TASK,
                scope_id=str(task.task_id),
                cap=demo.xtu(demo.TASK_CAP),
            ),
        ]
        for counter in (
            CounterKind.MODEL_CALLS,
            CounterKind.TOOL_CALLS,
            CounterKind.RETRIES,
            CounterKind.WORK_UNITS,
        ):
            counter_scopes: tuple[
                tuple[Literal[BudgetScopeType.RUN, BudgetScopeType.TASK], str], ...
            ] = (
                (BudgetScopeType.RUN, str(run.run_id)),
                (BudgetScopeType.TASK, str(task.task_id)),
            )
            for kind, scope in counter_scopes:
                ops.append(
                    SetCounterCap(
                        op="set_counter_cap",
                        scope_type=kind,
                        scope_id=scope,
                        counter=counter,
                        cap=demo.COUNTER_CAP,
                    )
                )
        ops.append(
            SetRunWindow(op="set_run_window", run_id=run.run_id, max_elapsed_ms=demo.RUN_WINDOW_MS)
        )
        ops.append(
            SetStepCapDefault(
                op="set_step_cap_default", run_id=run.run_id, cap=demo.xtu(demo.STEP_CAP)
            )
        )
        self._owner_ops(*ops)

    # -------------------------------------------------------- approval

    def pending(self) -> list[Attempt]:
        state = self.sm.state
        return [a for a in state.attempts.values() if a.state is AttemptState.APPROVAL_PENDING]

    def continues(self, run_id: RunId) -> bool:
        entry = self.runs.get(run_id)
        return entry is not None and entry.loop.continues(run_id)

    def approve(self, action_hash: str) -> LoopOutcome:
        """An exact approval of the one action that waits, then the loop resumes."""
        waiting = self.pending()
        if not waiting:
            raise CliError("NO_PENDING_APPROVAL")
        matching = [a for a in waiting if a.action_hash == action_hash]
        if len(matching) != 1:
            raise CliError("APPROVAL_TARGET_MISMATCH")
        attempt = matching[0]
        run_id = self.sm.state.steps[attempt.step_id].run_id
        if not self.continues(run_id):
            raise CliError("SESSION_UNAVAILABLE")
        loop = self._loop_for(run_id)
        if self.stopped(run_id):
            return loop.run(run_id)  # the stop wins: nothing is approved
        descriptor = self.journal.recorded_descriptor(str(attempt.attempt_id))
        policy = self.journal.policy(str(self.config.tenant_id))
        if descriptor is None or policy is None:
            raise CliError("APPROVAL_TARGET_MISMATCH")
        now = self.clock.now_ms()
        if now >= descriptor.expires_at_ms:
            raise CliError("APPROVAL_EXPIRED")
        approval = ExactApproval(
            approval_version=1,
            approval_id=self.ids.new(ApprovalId),
            tenant_id=self.config.tenant_id,
            action_hash=action_hash,
            approver_id=self.config.owner_id,
            approver_role=ApproverRole.OWNER,
            granted_at_ms=now,
            expires_at_ms=min(descriptor.expires_at_ms, now + policy.max_approval_ttl_ms),
            max_uses=1,
        )
        signed = self.owner.channel.approve(approval)
        self.journal.apply_ledger((RecordApproval(op="record_approval", signed=signed),))
        return loop.run(run_id, approval_id=approval.approval_id)


def open_session(
    home: Path,
    *,
    clock: Clock,
    monotonic: Callable[[], int],
    hooks: Hooks | None = None,
) -> Session:
    """Open the home's Runtime (the journal takes the OS lock)."""
    _check_home(home)
    keys = _load_keys(home)
    config = HomeConfig.load(home)
    sequence = OwnerSequence(home)
    journal = SqliteJournal.open(
        home / JOURNAL_FILE, clock=clock, random=OsRandomSource(), keys=keys
    )
    try:
        ids = IdGenerator(demo.SITE, clock, OsRandomSource())
        sm = RuntimeStateMachine.load(
            journal,
            ids=ids,
            clock=clock,
            verifier=AcceptSyntheticEvidence(),
            tenant_id=config.tenant_id,
            engine=ENGINE,
            policy_version=demo.POLICY_VERSION,
        )
        gate = SyntheticExecutionGate(
            runtime=sm,
            ledger=journal,
            keys=keys,
            pep_instance_id=ids.new(PepInstanceId),
            clock=clock,
        )
        broker = SyntheticFileBroker(
            area=home / AREA_DIR, workspace_id=demo.WORKSPACE_ID, key=manifest_key(keys.owner)
        )
        registry, tools = demo.registry()
        session = Session(
            home=home,
            clock=clock,
            monotonic=monotonic,
            journal=journal,
            sm=sm,
            gate=gate,
            files=broker,
            config=config,
            owner=_Owner(OwnerApprovalChannel(keys.owner), sequence),
            ids=ids,
            registry=registry,
            tools=tools,
            hooks=hooks or Hooks(),
        )
        if session.hooks.files_wrapper is not None:
            session.files = session.hooks.files_wrapper(broker, session)
        return session
    except BaseException:
        journal.close()
        raise
