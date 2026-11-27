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
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import StringConstraints

from agent_core.adapters.file_broker import (
    SyntheticFileBroker,
    create_synthetic_workspace,
    open_private_dir,
    read_private_file,
    replace_private_file,
    write_private_file,
)
from agent_core.adapters.sqlite_journal import SqliteJournal
from agent_core.adapters.system import OsRandomSource
from agent_core.identity.generator import IdGenerator
from agent_core.ports.clock import Clock
from agent_core.ports.files import FileOperationPort
from agent_core.ports.model import ModelPort
from agent_core.runtime.agent_loop import AgentLoop, LoopConfig, LoopDeps, LoopOutcome, TaskProfile
from agent_core.runtime.context_assembly import RuleCatalog
from agent_core.runtime.execution_gate import SyntheticExecutionGate
from agent_core.runtime.recovery import (
    Reconciled,
    RecoveryDeps,
    RecoveryReport,
    TemporaryRecovery,
    assess,
    declare_unresolved,
    reconcile,
    recover,
    recover_temporaries,
)
from agent_core.runtime.service import RuntimeStateMachine
from agent_core.runtime.verification import CriteriaCatalog
from agent_core.safety.approval import OwnerApprovalChannel
from agent_core.safety.workspace import manifest_digest, manifest_key
from agent_core.tool.registry import ToolRegistry
from agent_core.types.base import FrozenModel
from agent_core.types.context import SyntheticTexts
from agent_core.types.cost import BudgetScopeType, CounterKind
from agent_core.types.errors import (
    RuntimeProfileError,
    StoredFormatUnsupportedError,
    WorkspaceRejectedError,
)
from agent_core.types.ids import (
    ApprovalId,
    AttemptId,
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
from agent_core.types.model_errors import ModelNotSentError
from agent_core.types.profile import PHASE1_SYNTHETIC, require_phase1_profile
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
from agent_core.types.transitions import Termination, TerminationReason
from agent_core.types.values import Digest, EngineName, EngineVersion, sha256_digest
from agent_core_cli import demo

KEY_FILE: Final = "owner.key"
SEQ_FILE: Final = "owner.seq"
CONFIG_FILE: Final = "config.json"
JOURNAL_FILE: Final = "journal.db"
AREA_DIR: Final = "area"
ENGINE: Final = EngineVersion(engine=EngineName.RUNTIME, version="0.1.0")
_FILE_FLAGS: Final = os.O_NOFOLLOW | os.O_CLOEXEC


# P-6 compatibility: the fixed guidance for a stored format this version does not
# support (no automatic migration; status, stop and recovery stay available).
FORMAT_GUIDANCE = (
    "a stored Policy or RunGrant predates this version: new Runs are refused; "
    "status, stop, recover and reconcile still work; there is no automatic "
    "migration: start new work in a new home (init)"
)


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


# ---------------------------------------------------------------- the home (WP10-R01)

READY_FILE: Final = "home.ready"
READY_CONTENT: Final = b"agent-core-cli home v1\n"
MAX_FILE: Final = 4_096
MAX_SEQ: Final = 2**63 - 1
_SEQ_TEXT: Final = re.compile(rb"^(0|[1-9][0-9]{0,18})\n$")
_HEX_KEY: Final = r"^[0-9a-f]{64}$"


class Home:
    """The home directory, opened once from "/" with the WP-08 checks (trusted
    ancestors, a private directory of this user). Its files are read and written
    relative to that verified fd (no path re-resolution); every file is looked at
    before it is opened (no FIFO or device side effect), must be one link, ours,
    0600, and is read with a bound enforced while reading."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists() and not path.is_symlink():
            raise CliError("HOME_MISSING")
        try:
            self.fd = open_private_dir(path.absolute())
        except (WorkspaceRejectedError, OSError):
            raise CliError("HOME_UNSAFE") from None

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> Home:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def read(self, name: str, limit: int = MAX_FILE) -> bytes:
        try:
            return read_private_file(self.fd, name, limit=limit)
        except LookupError as exc:
            if exc.args and exc.args[0] == "MISSING":
                raise CliError("HOME_INCOMPLETE") from None
            raise CliError("HOME_UNSAFE") from None
        except OSError:
            raise CliError("HOME_UNSAFE") from None

    def create(self, name: str, data: bytes) -> None:
        write_private_file(self.fd, name, data)

    def replace(self, name: str, data: bytes) -> None:
        replace_private_file(self.fd, name, data, tag=secrets.token_hex(8))

    def require_ready(self) -> None:
        """A home whose init did not finish is never opened (and never deleted)."""
        if self.read(READY_FILE) != READY_CONTENT:
            raise CliError("HOME_UNSAFE")


def _strict_json(data: bytes) -> dict[str, Any]:
    """One JSON object, no duplicate keys, UTF-8."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in items]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate key")
        return dict(items)

    value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


class _KeyFile(FrozenModel):
    owner: Annotated[str, StringConstraints(strict=True, pattern=_HEX_KEY)]
    permit: Annotated[str, StringConstraints(strict=True, pattern=_HEX_KEY)]


class _ConfigFile(FrozenModel):
    version: Literal[1]
    tenant_id: Annotated[str, StringConstraints(strict=True, max_length=128)]
    owner_id: Annotated[str, StringConstraints(strict=True, max_length=128)]
    workspace_ref: Digest
    # P-6 (F07-1): the start profile. Missing reads as None and is refused (no
    # default); a value of another type is a malformed file (HOME_UNSAFE).
    profile: Annotated[str, StringConstraints(strict=True, max_length=64)] | None = None


def _load_keys(home: Home) -> LedgerKeys:
    try:
        data = _KeyFile.model_validate(_strict_json(home.read(KEY_FILE)))
        return LedgerKeys(
            owner=SecretKey(bytes.fromhex(data.owner)),
            permit=SecretKey(bytes.fromhex(data.permit)),
        )
    except (ValueError, TypeError, UnicodeDecodeError):  # pydantic's error is a ValueError
        raise CliError("HOME_UNSAFE") from None


@dataclass(frozen=True)
class HomeConfig:
    tenant_id: TenantId
    owner_id: UserId
    workspace_ref: str
    profile: str = PHASE1_SYNTHETIC

    @classmethod
    def load(cls, home: Home) -> HomeConfig:
        """The home's configuration, checked before the journal is opened: a home
        whose profile is not ``phase1_synthetic`` is refused (PROFILE_REFUSED)
        before any owner epoch or other state changes (P-6, F07-1)."""
        try:
            data = _ConfigFile.model_validate(_strict_json(home.read(CONFIG_FILE)))
            config = cls(
                tenant_id=TenantId(data.tenant_id),
                owner_id=UserId(data.owner_id),
                workspace_ref=data.workspace_ref,
                profile=data.profile if data.profile is not None else "",
            )
        except (ValueError, TypeError, UnicodeDecodeError):
            raise CliError("HOME_UNSAFE") from None
        try:
            require_phase1_profile(config.profile)
        except RuntimeProfileError:
            raise CliError("PROFILE_REFUSED") from None
        return config


def parse_seq(data: bytes) -> int | None:
    """The saved counter, or None if it is not exactly one decimal in range."""
    if _SEQ_TEXT.fullmatch(data) is None:
        return None
    value = int(data)
    return value if value <= MAX_SEQ else None


class OwnerSequence:
    """The owner's own record of its last batch number (WP09-R05, WP10-R02).

    Saved before each signing (a new temporary file written in full and fsynced,
    renamed over the old one, the directory fsynced): a crash leaves the old or
    the new number, never a part. A number above the journal's last applied one
    is a gap (a crash after saving, or a refused batch) and is fine; a number
    below it would sign batches the ledger already applied: owner operations
    stop until the owner looks (never set back or forward by guessing)."""

    def __init__(self, home: Home, last_applied: int | None) -> None:
        self._home = home
        self.problem: str | None = None
        value = parse_seq(home.read(SEQ_FILE, limit=64))
        if value is None:
            self.problem = "OWNER_SEQUENCE_INVALID"
        elif last_applied is not None and value < last_applied:
            self.problem = "OWNER_SEQUENCE_STALE"

    def next(self) -> int:
        if self.problem is not None:
            raise CliError(self.problem)
        current = parse_seq(self._home.read(SEQ_FILE, limit=64))
        if current is None or current >= MAX_SEQ:
            self.problem = "OWNER_SEQUENCE_INVALID"
            raise CliError(self.problem)
        self._home.replace(SEQ_FILE, f"{current + 1}\n".encode())
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
        trusted_synthetic_tasks=demo.trusted_task_refs(),
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
    """A new home: keys, counter, config, the work tree, the journal with the
    owner's price table, policy and wide caps, and last the ready mark. Refuses
    an existing path. A crash part way leaves a home without the mark, which is
    never opened and never deleted (the owner looks at it)."""
    if home.exists() or home.is_symlink():
        raise CliError("HOME_EXISTS")
    if not home.parent.is_dir():
        raise CliError("HOME_PARENT_MISSING")
    home.mkdir(mode=0o700)
    home.chmod(0o700)
    with Home(home) as h:
        keys = LedgerKeys(
            owner=SecretKey(secrets.token_bytes(32)), permit=SecretKey(secrets.token_bytes(32))
        )
        h.create(
            KEY_FILE,
            json.dumps(
                {"owner": keys.owner.reveal().hex(), "permit": keys.permit.reveal().hex()}
            ).encode(),
        )
        h.create(SEQ_FILE, b"0\n")
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
        h.create(
            CONFIG_FILE,
            json.dumps(
                {
                    "version": 1,
                    "profile": PHASE1_SYNTHETIC,
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
            owner = _Owner(OwnerApprovalChannel(keys.owner), OwnerSequence(h, None))
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
        h.create(READY_FILE, READY_CONTENT)


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
    home_dir: Home | None = None
    report_key: SecretKey | None = None
    broker: SyntheticFileBroker | None = None
    runs: dict[RunId, _RunEntry] = field(default_factory=dict)
    current: RunId | None = None
    closed: bool = False

    # -------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the journal (releasing the lock) and the home fd. A failure of the
        first does not leave the second open."""
        if self.closed:
            return
        self.closed = True
        try:
            self.journal.close()
        finally:
            if self.home_dir is not None:
                self.home_dir.close()

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
        # It plans nothing (no task profile) and has no model: it never stands in
        # for the earlier session's transcript or script (WP10-R04).
        task = self.sm.state.tasks[self.sm.state.runs[run_id].task_id]
        loop = self._make_loop(None, task, _NoModel(), plan=False)
        self.runs[run_id] = _RunEntry(loop, "earlier")
        return loop

    def _make_loop(
        self, task_demo: demo.DemoTask | None, task: Task, model: ModelPort, *, plan: bool = True
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
            tasks=(TaskProfile(goal_ref=task.goal_ref, complexity="simple"),) if plan else (),
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
        # A grant refusal that can be seen now is refused before anything is created.
        self._check_authority(name)
        task = self.sm.create_task(
            goal_ref=sha256_digest(task_demo.goal.encode()),
            done_criteria=(criterion.ref(),),
            budget_total=demo.xtu(demo.TASK_CAP),
            caps_total=demo.task_caps(),
        )
        run = self.sm.start_run(task, run_grant_ref=self.ids.new(RunGrantId))
        run = self.sm.set_run_status(run, RunStatus.QUEUED)
        run = self.sm.set_run_status(run, RunStatus.RUNNING)
        try:
            self._install_run(task, run, name)
        except Exception as failed:
            # Unexpected: the Run exists without its authority. End it at once so it
            # never runs; if that fails too, the owner's recovery ends it.
            try:
                self._end_unauthorized(run)
            except Exception as also:
                raise CliError("RUN_NOT_AUTHORIZED", "RUN_LEFT_FOR_RECOVERY") from also
            if isinstance(failed, CliError):
                raise  # a refusal with its own fixed code (for example the owner sequence)
            raise CliError("RUN_NOT_AUTHORIZED", "GRANT_NOT_INSTALLED") from failed
        loop = self._make_loop(task_demo, task, demo.ScriptedFakeModel(list(task_demo.replies)))
        self.runs[run.run_id] = _RunEntry(loop, name)
        self.current = run.run_id
        return task, run, loop.run(run.run_id)

    def _check_authority(self, name: str) -> None:
        """P-6 compatibility: the checks the ledger makes when the grant is
        installed, made before the Task and the Run exist (the ledger still makes
        them). An unsupported stored Policy is refused with the fixed guidance."""
        definition = demo.task_definition(name)
        try:
            policy = self.journal.policy(str(self.config.tenant_id))
        except StoredFormatUnsupportedError:
            raise CliError("STORED_FORMAT_UNSUPPORTED", FORMAT_GUIDANCE) from None
        if policy is None:
            raise CliError("RUN_NOT_AUTHORIZED", "NO_POLICY")
        if definition is None or definition.ref() not in policy.trusted_synthetic_tasks:
            raise CliError("RUN_NOT_AUTHORIZED", "SYNTHETIC_TASK_UNTRUSTED")

    def _end_unauthorized(self, run: Run) -> None:
        current = self.sm.state.runs[run.run_id]
        termination = Termination(
            kind="termination",
            reason=TerminationReason.INTERNAL_ERROR,
            evidence_ref=sha256_digest(b"agent-core: run grant not installed"),
        )
        self.sm.set_run_status(current, RunStatus.FAILED, termination)

    def _install_run(self, task: Task, run: Run, name: str) -> None:
        now = self.clock.now_ms()
        definition = demo.task_definition(name)
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
            synthetic_task_ref=None if definition is None else definition.ref(),
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

    # -------------------------------------------------------- recovery (WP-12)

    def recovery_deps(self) -> RecoveryDeps:
        return RecoveryDeps(
            sm=self.sm,
            ledger=self.journal,
            owner=self._owner_ops,
            clock=self.clock,
            report_key=self.report_key,
            journal=self.journal,
            active=self.continues,
        )

    def recoverable(self) -> list[RunId]:
        """The Runs a recovery may name: those the assessment lists that are not
        live in this session (WP12-R01)."""
        plan = assess(self.sm.state, self.journal)
        return [r.run_id for r in plan.runs if not self.continues(r.run_id)]

    def recover(self, run_ids: list[RunId]) -> RecoveryReport:
        """The owner's explicit recovery of the named Runs (never started by a
        status view; a Run live in this session is refused)."""
        return recover(self.recovery_deps(), deadlines=demo.RECOVERY_DEADLINES, run_ids=run_ids)

    def reconcile(self, attempt_ref: str) -> Reconciled:
        attempt_id = self._attempt(attempt_ref)
        return reconcile(self.recovery_deps(), attempt_id, None).outcome

    def declare_unresolved(self, run_id: RunId) -> Run:
        return declare_unresolved(self.recovery_deps(), run_id)

    def recover_temporaries(self) -> TemporaryRecovery:
        if self.broker is None:
            raise CliError("RECOVERY_REFUSED")
        return recover_temporaries(
            self.recovery_deps(), self.broker, workspace_ref=self.config.workspace_ref
        )

    def _attempt(self, ref: str) -> AttemptId:
        for attempt_id in self.sm.state.attempts:
            if str(attempt_id) == ref:
                return attempt_id
        raise CliError("ATTEMPT_NOT_FOUND")

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


class _NoModel:
    """The model of a loop for a Run of an earlier session: never sends anything."""

    def complete(self, request: Any) -> Any:
        raise ModelNotSentError("no model for a Run of an earlier session")


@dataclass
class Snapshot:
    """A read-only view of the home's Runtime (WP10-R03): the journal opened
    read-only under the OS lock, the runtime state rebuilt from it. No owner
    epoch, no revocation, no recovery, no loop, no gate."""

    journal: SqliteJournal
    sm: RuntimeStateMachine
    config: HomeConfig

    def continues(self, run_id: RunId) -> bool:
        return False

    def pending(self) -> list[Attempt]:
        return [
            a for a in self.sm.state.attempts.values() if a.state is AttemptState.APPROVAL_PENDING
        ]

    def close(self) -> None:
        self.journal.close()


def _checked_home(home: Path) -> tuple[Home, LedgerKeys, HomeConfig]:
    h = Home(home)
    try:
        h.require_ready()
        keys = _load_keys(h)
        config = HomeConfig.load(h)
        h.read(SEQ_FILE, limit=64)  # present and safe (its value is checked by the owner path)
    except BaseException:
        h.close()
        raise
    return h, keys, config


def _load_state(
    journal: SqliteJournal, clock: Clock, config: HomeConfig
) -> tuple[IdGenerator, RuntimeStateMachine]:
    ids = IdGenerator(demo.SITE, clock, OsRandomSource())
    sm = RuntimeStateMachine.load(
        journal,
        ids=ids,
        clock=clock,
        verifier=AcceptSyntheticEvidence(),
        tenant_id=config.tenant_id,
        engine=ENGINE,
        policy_version=demo.POLICY_VERSION,
        profile=config.profile,
    )
    return ids, sm


def open_snapshot(home: Path, *, clock: Clock) -> Snapshot:
    """Read the home's state without taking ownership (``status``)."""
    h, keys, config = _checked_home(home)
    try:
        journal = SqliteJournal.open_read_only(home / JOURNAL_FILE, clock=clock, keys=keys)
    finally:
        h.close()
    try:
        _, sm = _load_state(journal, clock, config)
    except BaseException:
        journal.close()
        raise
    return Snapshot(journal=journal, sm=sm, config=config)


def open_session(
    home: Path,
    *,
    clock: Clock,
    monotonic: Callable[[], int],
    hooks: Hooks | None = None,
) -> Session:
    """Open the home's Runtime: the journal takes the OS lock and a new owner epoch."""
    h, keys, config = _checked_home(home)
    try:
        journal = SqliteJournal.open(
            home / JOURNAL_FILE, clock=clock, random=OsRandomSource(), keys=keys
        )
    except BaseException:
        h.close()
        raise
    try:
        sequence = OwnerSequence(h, journal.last_owner_seq())
        ids, sm = _load_state(journal, clock, config)
        gate = SyntheticExecutionGate(
            runtime=sm,
            ledger=journal,
            keys=keys,
            pep_instance_id=ids.new(PepInstanceId),
            clock=clock,
        )
        broker = SyntheticFileBroker(
            area=home.absolute() / AREA_DIR,
            workspace_id=demo.WORKSPACE_ID,
            key=manifest_key(keys.owner),
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
            home_dir=h,
            report_key=keys.permit,
            broker=broker,
        )
        if session.hooks.files_wrapper is not None:
            session.files = session.hooks.files_wrapper(broker, session)
        return session
    except BaseException:
        h.close()
        journal.close()
        raise
