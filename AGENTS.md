# AGENTS.md — Agent Core development rules

> Status: **DRAFT** — persistent rules for AI agents working in this repository.
> Owner approval of this file as a whole is pending. Where this file conflicts
> with an explicit instruction from the owner in the current session, the
> owner's instruction wins.

## 1. Where things are (read these first)

1. `docs/STATUS.md` — **the single source of truth for progress**, the current
   stage, start conditions and the next task. Read it before doing anything.
2. `docs/decisions/OWNER_DECISIONS.md` — final owner decisions. Never contradict
   them. Never edit this file without explicit owner approval; propose wording
   instead.
3. `docs/design/phase-0/ROADMAP.md` — phases, gates and start/finish conditions.
4. `docs/design/phase-0/E_INTEGRATION.md` — integrated design. Section 28 defines
   which document wins when documents conflict.
5. `docs/design/phase-0/RESOLUTION_MATRIX.md` — status of every open issue and
   the owner approvals needed per phase.
6. `docs/design/phase-0/PHASE_1_IMPLEMENTATION_PLAN.md` — Phase 1 work packages.
7. `docs/reviews/PHASE_0_REVIEW.md` and `docs/reviews/FINAL_AUDIT_CHECKLIST.md`
   — review history and the audit checklist.

This file intentionally contains no progress table. Progress lives only in
`docs/STATUS.md`.

## 2. Project summary

- Agent Core is a private AI agent platform for the owner's own use. It is not
  a commercial product. Commercialization, billing and multi-user scale are not
  required goals.
- Priorities: quality, safety, reliability, verifiability, performance, cost
  efficiency, autonomy, extensibility.
- Initial shape: single host, single owner, single agent, modular monolith,
  while keeping the safety process separation and an independent evaluation
  environment.
- Service Harness (LINE, X, YouTube, Instagram, Facebook, TikTok, Gmail,
  Discord, ...) is a separate sub-feature after the core is complete. It is not
  part of the core completion criteria. Do not design or implement it unless
  the owner asks.

## 3. Hard rules (always apply)

1. Never push directly to `main`. Never merge any branch or pull request. Never
   force-push.
2. Do not implement production code until the owner starts Phase 1 (see
   `docs/STATUS.md`). Until then, design documents only.
3. Do not install dependencies (no package manager installs of any kind)
   without owner approval.
4. Do not call paid APIs. Real-API cost must stay at **$0 until the end of
   Phase 2** (owner decision D1). LiveProvider stages L2–L4 (build inclusion,
   credential injection, network path) are allowed only after the Phase 3A
   gates and owner approval (`E_INTEGRATION.md` section 31).
5. Do not create, rename or change `main` or the repository default branch
   without explicit owner approval.
6. Never commit secrets, API keys, tokens or `.env`-style files.
7. The holdout evaluation set lives in a **separate private repository**
   (owner decision D3, `ken1700-mizui/agent-core-holdout`). Do not create,
   connect to or read it from a development session. Never copy holdout items,
   answers or derived statistics into this repository or into prompts.
8. Do not mark an issue as resolved by moving it to a future phase. Distinguish
   design resolution from implementation verification.

## 4. Current operating constraints (until the owner lifts them)

- No external technical research unless the owner asks for it.
- No subagents.
- Keep tool output small; fetch only the parts needed.
- If information cannot be obtained, record it as an **unverified item**
  instead of guessing.
- If resource limits approach, report finished results first.

## 5. Language policy (owner decision D4)

| Audience | Language | Examples |
|---|---|---|
| Humans (owner, reviewers) | Japanese | `docs/STATUS.md`, `docs/decisions/`, `docs/design/`, `docs/reviews/` |
| AI agents | English | `AGENTS.md`, `CLAUDE.md`, prompts, agent-facing specs |

Identifiers, state names, field names and code-like terms stay in English
inside Japanese documents.

## 6. Document status labels

Every design or review document must carry one of:

- **DRAFT** — written, not yet reviewed.
- **PROVISIONAL** — reviewed with conditions, or based on assumptions that are
  not yet confirmed.
- **APPROVED** — accepted by the owner without open conditions.

Mark individual sections with their own label when it differs from the
document label.

## 7. Source integrity

- Do not reconstruct missing source documents by guessing. The full initial
  Phase 0 report has **not** been received in Claude Code sessions; only parts
  relayed by the independent reviewer were used. Do not claim a full comparison
  with it.
- Keep a list of unverified external specifications in each design document.
- Invariants are referenced with document prefixes (`A.I1`, `B1.C1`, `B2.M1`,
  `C1.S1`, `C2.X1`, `D.V1`, `E.Z1`).

## 8. Git workflow

- Work on the dedicated branch the owner approved (currently
  `docs/phase-0-design`; Draft PR #1).
- Before working, confirm the remote HEAD matches the SHA the owner gives.
- Commit only what the current task allows (currently design documents only).
- Push only to the approved working branch with a normal push.
