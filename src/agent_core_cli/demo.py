"""The synthetic demo catalog of the CLI (Phase 1: synthetic data only).

Everything here is fixed synthetic configuration: the two builtin file tools,
the owner's synthetic price table and policy, the model profile of the Fake
provider, the loop limits, the owner's caps, and three demo tasks whose model
answers are scripted (the Fake provider computes the usage and the cost):

- ``report``: write the report, then claim it is done (one approval);
- ``repair``: write a draft, claim it is done, see the verification fail,
  replace the file with the right content, claim it is done (two approvals,
  one repair round);
- ``falseclaim``: claim it is done without writing anything (the verification
  fails every time; the Run fails at the repair limit).

No real model, no real API, no real data. The model never decides anything:
the loop verifies the files itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from agent_core.adapters.fake_model import (
    FakeModelProvider,
    FakePrice,
    FakeReply,
    FakeScript,
    request_fingerprint,
)
from agent_core.adapters.file_broker import BUILTIN_FILE_TOOLS, builtin_artifact_hash
from agent_core.context.estimator import SYNTHETIC_ESTIMATOR
from agent_core.runtime.agent_loop import LoopLimits
from agent_core.runtime.verification import FileCriterion
from agent_core.safety.descriptor import pin_of
from agent_core.safety.workspace import file_digest
from agent_core.tool.registry import ToolRegistry, TrustedToolCatalog, TrustedToolEntry
from agent_core.types.context import ContextProfile
from agent_core.types.cost import PriceItem, PriceTable
from agent_core.types.ids import SiteId
from agent_core.types.model import (
    CacheCapability,
    Deployment,
    GenerationParams,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProfileRef,
    ProviderKind,
    ReasoningMode,
    StopReason,
    TextBlock,
    TokenAccounting,
    TokenizerMethod,
    ToolUseBlock,
    WindowCounts,
)
from agent_core.types.safety import ToolPin
from agent_core.types.tools import (
    ApplyField,
    ApprovalCondition,
    ExternalGuarantee,
    FieldSpec,
    FieldType,
    ObjectSchema,
    ResourceAccess,
    ResourceSpec,
    ToolEffectClass,
    ToolKind,
    ToolManifest,
    ToolManifestBody,
    ToolPermission,
    ToolRegistration,
    canonical_json,
)
from agent_core.types.values import Budget, Currency, NonMonetaryCaps, sha256_digest

SITE: Final = SiteId("devcli1")
POLICY_VERSION: Final = "policy-1"
PERIOD: Final = "synthetic-2026-11"  # one fixed synthetic budget period (Phase 1)
WORKSPACE_ID: Final = "tree"
WORKSPACE_FILES: Final[dict[str, bytes]] = {
    "workspace/README.txt": b"synthetic workspace of the Agent Core CLI demo\n",
    "workspace/out/README.txt": b"synthetic output area\n",
}
GOOD: Final = "synthetic report v1\n"
DRAFT: Final = "synthetic report draft\n"
REQUEST: Final = "Please create the report file."

# Owner's synthetic caps (micro XTU) and counters.
COST_CAP_PER_ACTION: Final = 5_000
STEP_CAP: Final = 10_000
RUN_CAP: Final = 100_000
TASK_CAP: Final = 1_000_000
WIDE_CAP: Final = 100_000_000  # tenant, user, period
COUNTER_CAP: Final = 100
RUN_WINDOW_MS: Final = 3_600_000
GRANT_TTL_MS: Final = 3_600_000

LIMITS: Final = LoopLimits(
    max_repairs=3,  # provisional test value, not an owner-approved operating limit
    max_model_calls=10,
    max_tool_calls=10,
    max_iterations=20,
    max_elapsed_ms=600_000,
)

MODEL_PROFILE: Final = ModelProfile(
    provider=ProviderKind.FAKE,
    model_id="scripted-model-small",
    deployment=Deployment.LOCAL,
    context_window=30_000,
    max_output=1_000,
    token_accounting=TokenAccounting(
        window_counts=WindowCounts.INPUT_PLUS_OUTPUT, reasoning_mode=ReasoningMode.NONE
    ),
    tokenizer_method=TokenizerMethod.ESTIMATE,
    cache_capability=CacheCapability(prefix_cache=False, min_cacheable_tokens=0, max_breakpoints=0),
    billing_scheme_ref=sha256_digest(b"cli-synthetic-billing-v1"),
    profile_version="0.1.0",
)
PROFILE_REF: Final = ProfileRef(model_id=MODEL_PROFILE.model_id, profile_version="0.1.0")
CONTEXT_PROFILE: Final = ContextProfile(
    model=MODEL_PROFILE,
    per_message_overhead=4,
    tool_def_overhead=16,
    safety_margin_percent=15,
    safety_margin_min=32,
    estimator=SYNTHETIC_ESTIMATOR,
)
PARAMS: Final = GenerationParams(max_output_tokens=200, temperature_milli=0, timeout_ms=30_000)
FAKE_PRICE: Final = FakePrice(
    input_micro_xtu_per_mtok=1_000_000, output_micro_xtu_per_mtok=4_000_000
)
PRICE_TABLE: Final = PriceTable(
    price_table_id="cli_synthetic",
    version="1.0.0",
    status="test",
    items=(
        (
            MODEL_PROFILE.model_id,
            PriceItem(input_micro_per_mtok=1_000_000, output_micro_per_mtok=4_000_000),
        ),
    ),
    final_usage_is_complete=True,
)


def xtu(micro: int) -> Budget:
    return Budget(currency=Currency.XTU, micro_units=micro)


def task_caps() -> NonMonetaryCaps:
    return NonMonetaryCaps(
        model_calls=200,
        tool_calls=300,
        context_rebuilds=50,
        replans=10,
        memory_operations=20,
        retries=30,
        total_operations=600,
        wall_clock_ms=7_200_000,
    )


# ---------------------------------------------------------------- tools


def _read_body() -> ToolManifestBody:
    return ToolManifestBody(
        tool_id="read_file",
        version="1.0.0",
        kind=ToolKind.BUILTIN,
        input_schema=ObjectSchema(
            fields=(
                FieldSpec(
                    name="path",
                    type=FieldType.STRING,
                    required=True,
                    max_length=256,
                    resource=ResourceAccess.READ,
                ),
                FieldSpec(name="max_bytes", type=FieldType.INTEGER, required=False, maximum=65_536),
            ),
            max_bytes=1_024,
        ),
        output_schema=ObjectSchema(
            fields=(
                FieldSpec(name="content", type=FieldType.STRING, required=True, max_length=65_536),
                FieldSpec(name="truncated", type=FieldType.BOOLEAN, required=True),
            ),
            max_bytes=70_000,
        ),
        effect_class=ToolEffectClass.READ_ONLY,
        external_guarantee=ExternalGuarantee.NONE,
        required_permissions=(ToolPermission.FILE_READ,),
        resources=(ResourceSpec(path_pattern="workspace/**", access=ResourceAccess.READ),),
        destinations=(),
        timeout_ms=5_000,
        max_output_bytes=70_000,
        cost_estimate=xtu(0),
        approval_conditions=(),
        sandbox_profile=None,
    )


def _write_body() -> ToolManifestBody:
    return _read_body().model_copy(
        update={
            "tool_id": "write_file",
            "input_schema": ObjectSchema(
                fields=(
                    FieldSpec(
                        name="content",
                        type=FieldType.STRING,
                        required=True,
                        max_length=65_536,
                        apply=ApplyField.CONTENT,
                    ),
                    FieldSpec(
                        name="expected",
                        type=FieldType.STRING,
                        required=True,
                        max_length=71,
                        apply=ApplyField.EXPECTED_STATE,
                    ),
                    FieldSpec(
                        name="path",
                        type=FieldType.STRING,
                        required=True,
                        max_length=256,
                        resource=ResourceAccess.WRITE,
                    ),
                ),
                max_bytes=70_000,
            ),
            "effect_class": ToolEffectClass.IRREVERSIBLE,
            "required_permissions": (ToolPermission.FILE_WRITE,),
            "resources": (ResourceSpec(path_pattern="workspace/**", access=ResourceAccess.WRITE),),
            "approval_conditions": (ApprovalCondition.ON_WRITE,),
        }
    )


def registry() -> tuple[ToolRegistry, tuple[ToolRegistration, ...]]:
    """The trusted catalog of the two builtin file tools (their executable is the
    file broker's code, pinned by its artifact hash) and the registry."""
    manifests = (ToolManifest.seal(_read_body()), ToolManifest.seal(_write_body()))
    artifact = builtin_artifact_hash()
    for sealed in manifests:
        if (sealed.tool_id, sealed.version) not in BUILTIN_FILE_TOOLS:
            raise ValueError("the demo tools are the builtin file tools")
    catalog = TrustedToolCatalog(
        entries=tuple(
            TrustedToolEntry(
                tool_id=m.tool_id,
                version=m.version,
                manifest_hash=m.manifest_hash,
                artifact_hash=artifact,
                approval_ref=sha256_digest(b"owner-approval-cli-synthetic-tools-v1"),
            )
            for m in manifests
        )
    )
    reg = ToolRegistry(catalog)
    for sealed in manifests:
        reg.register(sealed, artifact_hash=artifact)
    tools = (reg.get("read_file", "1.0.0"), reg.get("write_file", "1.0.0"))
    return reg, tools


def pins(tools: tuple[ToolRegistration, ...]) -> tuple[ToolPin, ...]:
    return tuple(sorted((pin_of(t) for t in tools), key=lambda p: (p.tool_id, p.version)))


# ---------------------------------------------------------------- demo tasks


def _write(content: str, path: str, expected: str, use_id: str) -> FakeReply:
    arguments = canonical_json({"content": content, "expected": expected, "path": path}).decode()
    return FakeReply(
        kind="reply",
        content=(ToolUseBlock(type="tool_use", id=use_id, name="write_file", arguments=arguments),),
        stop_reason=StopReason.TOOL_USE,
        input_tokens=100,
        output_tokens=20,
        elapsed_ms=10,
    )


def _done(text: str = "The report is written. Done.") -> FakeReply:
    return FakeReply(
        kind="reply",
        content=(TextBlock(type="text", text=text),),
        stop_reason=StopReason.END_TURN,
        input_tokens=100,
        output_tokens=10,
        elapsed_ms=10,
    )


@dataclass(frozen=True)
class DemoTask:
    name: str
    path: str
    replies: tuple[FakeReply, ...]

    @property
    def goal(self) -> str:
        return f"Create {self.path} with the synthetic report."

    @property
    def criterion(self) -> FileCriterion:
        return FileCriterion(
            kind="file_content", path=self.path, expected_digest=file_digest(GOOD.encode())
        )


DEMOS: Final = ("report", "repair", "falseclaim")


def demo_task(name: str, number: int) -> DemoTask | None:
    path = f"workspace/out/report-{number}.txt"
    if name == "report":
        return DemoTask(name, path, (_write(GOOD, path, "absent", "t1"), _done()))
    if name == "repair":
        draft = file_digest(DRAFT.encode())
        return DemoTask(
            name,
            path,
            (
                _write(DRAFT, path, "absent", "t1"),
                _done(),
                _write(GOOD, path, draft, "t2"),
                _done(),
            ),
        )
    if name == "falseclaim":
        return DemoTask(name, path, (_done("The report file exists and is verified."),))
    return None


@dataclass
class ScriptedFakeModel:
    """ModelPort: the next scripted reply for whatever request comes, through the
    real Fake provider (usage and cost computed by it). After the script, it
    answers that it has nothing more to do (a claim the loop verifies)."""

    replies: list[FakeReply]
    requests: int = field(default=0)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests += 1
        reply = self.replies.pop(0) if self.replies else _done("No further action.")
        script = FakeScript.of({request_fingerprint(request): (reply,)})
        provider = FakeModelProvider(profile=MODEL_PROFILE, script=script, price=FAKE_PRICE)
        return provider.complete(request)
