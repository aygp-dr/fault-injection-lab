# Meta-Prompt v0 Review

**Reviewed**: 2026-03-11
**Source**: docs/meta-prompt-v0.md (identical to CLAUDE.md at time of review)

## Critical (blockers)

- [x] **Agent knows its role** — "You are a coding agent. Your job is to implement the system described in spec.org." Clear.
- [x] **Build order has failure handler** — "If an acceptance test fails, stop. Document what failed..." Present.
- [x] **Conjectures are actionable** — Instrumentation Requirement section explicitly says "each conjecture requires a corresponding measurement hook." Present.

## Substantive

- [x] **Confirmation gate** — Present before first code write. Agent must summarize understanding.
- [x] **Anti-goals are mechanical** — Each names a specific market example (Istio, Gremlin, k6). Agent can pattern-match.
- [x] **Architectural constraint promoted** — "TLS Termination Constraint" has its own named section. Correct — this is the #1 gotcha.
- [x] **Success criteria are testable assertions** — "5/5 pass", "7/7 pass", "ConnectionError", "no hysteresis". All testable.
- [ ] **Missing: Conjecture-to-step mapping** — CLAUDE.md lists conjectures and build steps separately but doesn't say which step touches which conjecture. Agent must guess. **Fix: add mapping.**
- [ ] **Missing: pyproject.toml / dependency management** — Stack Preferences says "uv for package management" but build order doesn't include a step for `uv init` or `pyproject.toml`. **Fix: add to Step 1 or as Step 0.**
- [ ] **Weak: Ollama model name hardcoded** — `MODEL = "qwen3:0.6b"` in spec.org test. CLAUDE.md doesn't flag this as configurable or document which models must be pulled. **Fix: add prerequisite note.**

## Minor

- [ ] **External URLs not vendored** — Toxiproxy REST API, Prism docs, PokéAPI docs are all external links. Low risk (stable URLs), but the PokéAPI OpenAPI spec URL should be vendored per `scripts/fetch-specs.sh`.
- [ ] **No CONJECTURES.md referenced** — Instrumentation Requirement says "Log this in CONJECTURES.md" but the file doesn't exist yet. Not a CLAUDE.md bug, but the build order should include creating it.

## Findings to Apply in v1

1. Add conjecture-to-step mapping table
2. Add Step 0 (or expand Step 1): `pyproject.toml`, `uv init`, dependency install
3. Add prerequisite note: Ollama models must be pulled (`qwen3:0.6b`, `nomic-embed-text`)
4. Add CONJECTURES.md creation to build step 1
5. Vendor the PokéAPI OpenAPI spec URL into a note (already handled by `scripts/fetch-specs.sh`)
