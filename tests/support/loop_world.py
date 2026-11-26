"""Synthetic world for WP-15 agent-loop tests.

A managed synthetic tree, a ledger whose policy trusts it, a Task whose done
criterion is a synthetic file-content specification, the owner's caps, Run grant
and default Step cap, and the Fake provider scripted in call order (each reply
is keyed to the exact request fingerprint when the call happens).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_core.adapters.fake_model import (
    FakeFault,
    FakeModelProvider,
    FakePrice,
    FakeReply,
    FakeScript,
    request_fingerprint,
)
from agent_core.context.estimator import SYNTHETIC_ESTIMATOR
from agent_core.runtime.agent_loop import (
    AgentLoop,
    LoopConfig,
    LoopDeps,
    LoopLimits,
    TaskProfile,
)
from agent_core.runtime.context_assembly import RuleCatalog
from agent_core.runtime.verification import CriteriaCatalog, FileCriterion
from agent_core.safety.workspace import file_digest
from agent_core.types.context import ContextProfile, SyntheticTexts
from agent_core.types.ledger import SetStepCapDefault
from agent_core.types.model import (
    GenerationParams,
    ModelRequest,
    ModelResponse,
    StopReason,
    TextBlock,
    ToolUseBlock,
)
from agent_core.types.records import StepStatus
from agent_core.types.tools import canonical_json
from agent_core.types.values import sha256_digest
from support import builders as b
from support import files_world as fw
from support import ledger_world as lw
from support import model_world as m
from support import runtime_world as w
from support import safety_world as s

GOAL = "Create workspace/out/report.txt with the synthetic report."
REQUEST = "Please create the report file."
TARGET = "workspace/out/report.txt"
GOOD = "synthetic report v1\n"
PRICE = FakePrice(input_micro_xtu_per_mtok=1_000_000, output_micro_xtu_per_mtok=4_000_000)
PARAMS = GenerationParams(max_output_tokens=200, temperature_milli=0, timeout_ms=30_000)
CRITERION = FileCriterion(
    kind="file_content", path=TARGET, expected_digest=file_digest(GOOD.encode())
)


def profile() -> ContextProfile:
    return ContextProfile(
        model=m.profile(context_window=30_000, max_output=1_000),
        per_message_overhead=4,
        tool_def_overhead=16,
        safety_margin_percent=15,
        safety_margin_min=32,
        estimator=SYNTHETIC_ESTIMATOR,
    )


# ---------------------------------------------------------------- scripted Fake model


def write(
    content: str = GOOD, path: str = TARGET, expected: str = "absent", use_id: str = "t1"
) -> FakeReply:
    arguments = canonical_json({"content": content, "expected": expected, "path": path}).decode()
    return tool("write_file", arguments, use_id)


def tool(name: str, arguments: str, use_id: str = "t1") -> FakeReply:
    return FakeReply(
        kind="reply",
        content=(ToolUseBlock(type="tool_use", id=use_id, name=name, arguments=arguments),),
        stop_reason=StopReason.TOOL_USE,
        input_tokens=100,
        output_tokens=20,
        elapsed_ms=10,
    )


def done(text: str = "The report is written. Done.") -> FakeReply:
    return FakeReply(
        kind="reply",
        content=(TextBlock(type="text", text=text),),
        stop_reason=StopReason.END_TURN,
        input_tokens=100,
        output_tokens=10,
        elapsed_ms=10,
    )


@dataclass
class ScriptedModel:
    """ModelPort: the next scripted outcome for whatever request comes, through the
    real Fake provider (usage and cost computed by it)."""

    outcomes: list[Any]
    requests: list[ModelRequest] = field(default_factory=list)
    responses: list[ModelResponse] = field(default_factory=list)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("the script ran out")
        outcome = self.outcomes.pop(0)
        script = FakeScript.of({request_fingerprint(request): (outcome,)})
        response = FakeModelProvider(profile=profile().model, script=script, price=PRICE).complete(
            request
        )
        self.responses.append(response)
        return response


__all__ = ["FakeFault"]


# ---------------------------------------------------------------- the world


@dataclass
class World:
    st: lw.Setup
    tree: fw.Tree
    run: Any
    task: Any
    model: ScriptedModel
    loop: AgentLoop
    config: LoopConfig


def criteria(*specs: FileCriterion) -> CriteriaCatalog:
    return CriteriaCatalog(criteria=specs or (CRITERION,))


def world(
    tmp_path: Path,
    outcomes: list[Any],
    *,
    limits: LoopLimits | None = None,
    complexity: str = "simple",
    specs: tuple[FileCriterion, ...] = (),
    step_cap: int = 10_000,
    prof: ContextProfile | None = None,
    goal: str = GOAL,
    wrap_files: Any = None,
    wrap_ledger: Any = None,
    **caps: int,
) -> World:
    tmp_path.mkdir(parents=True, exist_ok=True)
    st, tree = fw.world(tmp_path)
    catalog = criteria(*specs)
    task = st.sm.create_task(
        goal_ref=sha256_digest(goal.encode()),
        done_criteria=tuple(sorted(c.ref() for c in catalog.criteria)),
        budget_total=lw.xtu(caps.pop("task_budget", 1_000_000)),
        caps_total=b.caps(),
    )
    run = w.running_run(st.sm, task)
    first = lw.step_in(st, run)
    lw.caps(st, lw.scopes_of(st.sm, first), **caps)
    st.sm.set_step_status(first, StepStatus.CANCELLED)
    st.owner(SetStepCapDefault(op="set_step_cap_default", run_id=run.run_id, cap=lw.xtu(step_cap)))
    model = ScriptedModel(list(outcomes))
    config = LoopConfig(
        tenant_id=b.TENANT,
        workspace_ref=tree.digest,
        profile=prof if prof is not None else profile(),
        params=PARAMS,
        price_table=s.PRICE_TABLE,
        rules=RuleCatalog(rules=()),
        texts=SyntheticTexts.of(goal, *(c.text() for c in catalog.criteria)),
        criteria=catalog,
        tasks=(TaskProfile(goal_ref=task.goal_ref, complexity=complexity),),  # type: ignore[arg-type]
        limits=limits
        if limits is not None
        else LoopLimits(
            max_repairs=3,
            max_model_calls=10,
            max_tool_calls=10,
            max_iterations=20,
            max_elapsed_ms=600_000,
        ),
        cost_cap_micro=5_000,
        user_input=REQUEST,
    )
    broker = fw.broker(st.keys.owner, tree)
    deps = LoopDeps(
        sm=st.sm,
        ledger=st.journal if wrap_ledger is None else wrap_ledger(st.journal),
        gate=lw.gate(st),
        model=model,
        registry=s.REGISTRY,
        tools=(s.READ_TOOL, s.WRITE_TOOL),
        files=broker if wrap_files is None else wrap_files(broker),
        clock=st.clock,
        new_id=b.new_id,
    )
    return World(st, tree, run, task, model, AgentLoop(deps, config), config)


def approve_pending(wd: World, outcome: Any) -> Any:
    """The owner approves the waiting action (exact action hash), then the loop resumes."""
    approval_id = lw.record_approval_for(wd.st, outcome.pending_action_hash)
    return wd.loop.run(wd.run.run_id, approval_id=approval_id)
