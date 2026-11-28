# Phase 2 implementation instructions for AI agents

> Status: **DRAFT** — not approved. Phase 2 implementation has **not** started and
> must not start until the owner approves it (see `PHASE_2_START_READINESS.md`, O-1).
> Human-facing design: `PHASE_2_IMPLEMENTATION_PLAN.md`, `PHASE_2_EVALUATION_CONTRACT.md`,
> `PHASE_2_SANDBOX_DESIGN.md` (Japanese). `AGENTS.md` still applies in full.

## 1. Scope and order

1. Implement only the work packages the owner's current instruction authorizes
   (WP2-01 to WP2-15), on the Phase 2 branch the owner approves
   (proposed: `impl/phase-2-evaluation`, from `92c18c4`). Never change
   `impl/phase-1-foundation` (the Phase 1 completion point) or `main`.
2. Follow the dependency order in `PHASE_2_IMPLEMENTATION_PLAN.md` section 1.
   **No work package may execute untrusted code** (anything a model, a task or a
   test fixture under evaluation produces) before WP2-02 to WP2-04 are done,
   SG-1 (independent sandbox safety check) has passed and the owner has approved
   running untrusted code.
3. In WP2-03 and WP2-04, only the project's own fixed diagnostic program and
   fixed test scenarios may run inside a container.

## 2. Safety rules (never relax)

1. No fallback to running code on the host. If the sandbox self-diagnosis fails
   (any of the nine checks), every code-executing tool is refused
   (`SANDBOX_UNAVAILABLE`).
2. P1 (runtime) never holds the Docker API, network access or credentials and
   never spawns processes. Only P2 (`agent_core_broker`) talks to Docker, over
   AF_UNIX. No AF_INET/AF_INET6/DNS anywhere. Changing the import contracts needs
   the owner's approval (S-5); keep any exception as narrow as approved.
3. Containers: non-root UID, `--cap-drop=ALL`, `no-new-privileges`, default
   seccomp, read-only root, size-limited tmpfs, `--network none`, no Docker
   socket, no privileged/host namespaces, resource limits, image pinned by
   digest, allow-listed environment variables only. Check the effective settings
   of **each** container (inspect) before start; mismatch destroys it.
4. Do not run untrusted code on a host without AppArmor/SELinux unless the owner
   explicitly decides otherwise (S-4).
5. Unknown outcomes stay `OUTCOME_UNKNOWN`, keep their cost hold and are never
   retried automatically. A stop request is not a confirmed stop.
6. Never claim complete isolation from standard Docker features, and never
   report Fake-provider results as real-model performance gains.

## 3. Evaluation integrity

1. Never change task sets, graders, scoring rules, analysis plans, margins or
   the holdout. Never read, create or connect the holdout repository (D3).
2. Use the `task_outcome` values of D 7.1 unchanged; record safety separately
   (`violations`, `blocked_attempts`). Extension fields only as approved (O-3).
3. The agent under evaluation never writes evaluation records, never classifies
   its own trials and never sees hidden tests. Grading runs in a separate grading
   sandbox after the agent's run.
4. Freeze an `ExperimentSpec` (`spec_hash`) before running it; refuse changes after.
5. Record identifiers and hashes, never prompt bodies, file contents, model
   output bodies or credentials (A.I11).

## 4. Compatibility and quality

1. Keep all existing tests (2340 at `93435c8`) passing; never skip, disable or
   delete a test. Write or update tests first (TDD) and confirm red.
2. Journal event `schema_version` 3 must stay readable; Rebuild identity
   (Phase 1 S1 tests and `tools/perf/state_identity.py`) must hold.
3. Keep the provisional operating limits (50,000 events, 160 MiB, start-up 60 s);
   the Benchmark Runner splits work across homes instead of deleting or
   truncating any journal.
4. Tests that need Docker use the `sandbox` marker and fail (never skip) as
   `SANDBOX_NOT_RUN` where Docker is unavailable.
5. No new dependency without an ADR and the owner's approval. Statistics use the
   standard library unless the owner approves otherwise (O-6).
6. Run the eight quality checks before every commit; one work package per
   commit series; normal pushes only; update `docs/STATUS.md` at the end of
   every session.
