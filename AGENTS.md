# AGENTS.md — Agent Core development rules

> Status: **DRAFT** — persistent rules for AI agents working in this repository.
> Owner approval of this file as a whole is pending. Where this file conflicts
> with an explicit instruction from the owner in the current session, the
> owner's instruction wins.

## 1. Project state

- The project is in **Phase 0 (design)**. No production code exists and none
  may be written until the owner lifts that restriction.
- Current progress and the next task live in `docs/STATUS.md`. Read it first.
- Owner decisions that are final live in `docs/decisions/OWNER_DECISIONS.md`.
  Never contradict them; propose changes only as a question to the owner.

## 2. Hard rules (always apply)

1. Never push directly to `main`. Never merge any branch or pull request.
2. Do not implement production code. Design documents only.
3. Do not install dependencies (no package manager installs of any kind).
4. Do not call paid APIs. Real-API cost must stay at **$0 until the end of
   Phase 2** (owner decision D1). No live model credentials may exist in the
   repository, CI or runtime configuration before the Phase 3B gate.
5. Do not create, rename or change `main` or the repository default branch
   without explicit owner approval.
6. Never commit secrets, API keys, tokens or `.env`-style files.
7. The holdout evaluation set lives in a **separate private repository**
   (owner decision D3). Never copy holdout items, answers or derived
   statistics into this repository or into prompts used for development.

## 3. Current operating constraints (until the owner lifts them)

- No external technical research unless the owner asks for it.
- No subagents.
- Keep tool output small; fetch only the parts needed.
- If information cannot be obtained, record it as an **unverified item**
  instead of guessing.
- If resource limits approach, report finished results first.

## 4. Language policy (owner decision D4)

| Audience | Language | Examples |
|---|---|---|
| Humans (owner, reviewers) | Japanese | `docs/STATUS.md`, `docs/decisions/`, `docs/design/`, `docs/reviews/` |
| AI agents | English | `AGENTS.md`, `CLAUDE.md`, prompts, agent-facing specs |

Identifiers, state names, field names and code-like terms stay in English
inside Japanese documents.

## 5. Document status labels

Every design or review document must carry one of:

- **DRAFT** — written, not yet reviewed.
- **PROVISIONAL** — reviewed with conditions, or based on assumptions that are
  not yet confirmed.
- **APPROVED** — accepted by the owner without open conditions.

Mark individual sections with their own label when it differs from the
document label.

## 6. Source integrity

- Do not reconstruct missing source documents by guessing. If an original
  (for example the initial Phase 0 report) is not available, say so explicitly
  and work only from what was actually provided.
- Keep a list of unverified external specifications in each design document.

## 7. Work units for Phase 0 design revision

| Unit | Scope | Document |
|---|---|---|
| A | Architecture, Runtime, failure recovery | `docs/design/phase-0/A_RUNTIME.md` |
| B | Context Engine, Memory Engine | (not started) |
| C | Safety Engine, isolation of evaluation environment | (not started) |
| D | Evaluation Engine, statistics, Cost Engine | (not started) |
| E | Roadmap, version-based performance tracking, overall consistency | (not started) |

Work on one unit at a time. Stop after reporting a unit and wait for the owner.

## 8. Git workflow

- Work on a dedicated branch (currently `docs/phase-0-design`).
- Commit only design documents.
- Push only to the working branch the owner approved.
