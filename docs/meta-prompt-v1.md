# Fault Injection Lab — Toxiproxy in Front of Real APIs

## Your Role

You are a coding agent. Your job is to implement the system described
in spec.org. You produce working code, tests, and documentation.
You do not re-specify the system unless you find a direct contradiction
in the spec — in that case, surface it before proceeding.

## Foundational Axiom

> The proxy layer must be transparent to the client under clean conditions.
> Faults are additive, never subtractive.

Toxiproxy sits between client and upstream. With no toxics enabled,
behavior must be indistinguishable from a direct connection. Every
toxic *adds* degradation; removing a toxic restores the clean path.
This is structural: tests compare baseline vs degraded, and that
comparison is only valid if the baseline is a faithful proxy of the
upstream.

## Confirmation Gate

Before writing any code, output a one-paragraph summary of what you
understand this project to be, who its primary user is, and what its
primary output is. Do not proceed until this summary is accurate.

## What You Are Building

- Three worked examples of Toxiproxy proxying real APIs (GitHub, Ollama, PokéAPI)
- A Python control layer (`fault_control/`) for adding/removing toxics via REST
- A pytest test suite that validates client behavior under fault conditions
- An optional Prism mock mode for fully offline PokéAPI testing

## Explicit Anti-Goals

Do not build any of the following:

- A service mesh (Istio, Linkerd) — no sidecar proxies, no mTLS, no service discovery
- A chaos engineering platform (Gremlin, LitmusChaos) — no random production faults
- A load testing tool (k6, Locust) — single-request fault scenarios, not volume
- A Toxiproxy wrapper library — use raw REST via `requests`, not a third-party SDK
- A CI/CD pipeline — this is a local development and testing tool

## Key Design Decisions

- Docker Compose manages Toxiproxy and optional Prism containers
- Control plane is always `http://localhost:8474`
- Three named proxies: `github` (:9001), `ollama` (:9002), `pokeapi` (:9003)
- Tests use `autouse` fixture to reset all toxics between test functions
- Ollama embedding latency < 100ms is the RTB (real-time bidding) SLA gate
- All code tangles from spec.org via org-babel

## TLS Termination Constraint

Toxiproxy operates at the TCP level. For HTTPS upstreams (GitHub,
PokéAPI), the proxy forwards raw TCP — clients must talk plain HTTP
to the proxy port and let Toxiproxy handle the TCP tunnel. This means
test code uses `http://localhost:PORT`, not `https://`. Do not attempt
to add TLS termination to Toxiproxy; it is not designed for it.
If HTTPS-to-upstream is needed, use a MITM cert or accept the HTTP-only
constraint for local testing.

## Prerequisites

Before starting the build order:
- Docker and Docker Compose v2 must be installed
- Ollama must be running on host with models pulled: `ollama pull qwen3:0.6b && ollama pull nomic-embed-text`
- Python 3.11+ with uv: `uv init` or `pyproject.toml` must exist with `requests` and `pytest` as dependencies

## Build Order

Follow step order in spec.org. Do not skip layers.
Each step must have a passing acceptance test before proceeding.

**Failure handler**: If an acceptance test fails, stop. Document what
failed, what you tried, and what the blocker is. Do not proceed to the
next step. Surface the failure as a CPRR refutation candidate.

1. **Tangle shared infrastructure** — `docker/docker-compose.yml`, `toxiproxy/config.json`, `pyproject.toml`, `CONJECTURES.md`
   - Acceptance: `docker compose -f docker/docker-compose.yml config` validates; `uv sync` installs deps
   - Conjectures touched: none (infrastructure only)
2. **Tangle Python control layer** — `fault_control/toxiproxy.py`
   - Acceptance: module imports without error; `ToxiproxyController()` instantiates
   - Conjectures touched: none (control plane only)
3. **Tangle test fixtures** — `tests/conftest.py`
   - Acceptance: `pytest --collect-only` finds fixtures without import errors
   - Conjectures touched: none (fixture wiring only)
4. **Tangle + run GitHub API tests** — `tests/test_github.py`
   - Acceptance: all 5 tests pass with Toxiproxy running
   - Conjectures touched: C-001 (latency additivity), C-005 (bandwidth proportionality)
5. **Tangle + run Ollama API tests** — `tests/test_ollama.py`
   - Acceptance: all 5 tests pass with Toxiproxy + Ollama running
   - Conjectures touched: C-001 (latency), C-002 (RTB SLA), C-003 (slicer JSON validity)
6. **Tangle + run PokéAPI tests** — `tests/test_pokeapi.py`
   - Acceptance: all 7 tests pass with Toxiproxy running
   - Conjectures touched: C-001 (latency), C-003 (slicer), C-005 (bandwidth)
7. **Set up Prism mock** — `docker/docker-compose.prism.yml`, `specs/pokeapi.yaml`
   - Acceptance: PokéAPI tests pass against Prism (offline, no internet)
   - Conjectures touched: C-004 (Prism mock equivalence)
8. **Latency sweep instrumentation** — Babel block in spec.org
   - Acceptance: sweep produces a table with PASS/FAIL per injected latency
   - Conjectures touched: C-001 (latency additivity across injection levels), C-002 (RTB gate)

## Toxic Types Reference

| Toxic     | Effect                              | Key Attribute       |
|-----------|-------------------------------------|---------------------|
| latency   | Adds fixed delay ± jitter           | `latency`, `jitter` |
| bandwidth | Caps throughput in KB/s             | `rate`              |
| timeout   | Drops connection after delay        | `timeout`           |
| slicer    | Fragments TCP into small chunks     | `average_size`      |
| disable   | Proxy refuses all connections       | (proxy-level)       |

## Open Conjectures (test these, do not assume)

- **C-001**: Toxiproxy latency toxic is purely additive — observed latency = baseline + injected ± jitter, with no compounding or absorption effects
- **C-002**: `nomic-embed-text` via Ollama meets the <100ms RTB SLA under clean proxy conditions (no toxics)
- **C-003**: TCP slicer toxic preserves JSON validity after reassembly — no data corruption at 1-byte average chunk size
- **C-004**: Prism mock + Toxiproxy fully replaces live PokéAPI for the test scenarios in spec.org (same status codes, same schema)
- **C-005**: Bandwidth throttle effect on response time scales proportionally with payload size (2x payload ≈ 2x elapsed)

## Instrumentation Requirement

Each conjecture in § Open Conjectures requires a corresponding
measurement hook in the implementation. Before closing any build step,
confirm which conjecture(s) it touches and what data it would produce
to evaluate them. Log this in CONJECTURES.md.

## Research Context

- [Toxiproxy REST API](https://github.com/Shopify/toxiproxy)
- [Prism docs](https://docs.stoplight.io/docs/prism/) — mock + proxy + validation
- [PokéAPI v2 docs](https://pokeapi.co/docs/v2) + [OpenAPI spec](https://github.com/PokeAPI/pokeapi)
- [Ollama API docs](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [GitHub REST API docs](https://docs.github.com/en/rest)

## Stack Preferences

- Container runtime: Docker Compose v2 (no Swarm, no Kubernetes)
- Language: Python 3.11+ (uv for package management)
- Test framework: pytest with fixtures (no unittest)
- HTTP client: `requests` (not httpx, not aiohttp)
- Mock server: Prism 4 (OpenAPI spec-driven)
- Proxy: Toxiproxy latest (Shopify)
- Issue tracking: bd (beads, Dolt-backed)
- Conjecture tracking: cprr

## Acceptance: End-to-End Test

Given a running Docker Compose stack (`toxiproxy` container up, Ollama running on host):

- All three proxy endpoints respond under clean conditions
- `tests/test_github.py` — 5/5 pass
- `tests/test_ollama.py` — 5/5 pass
- `tests/test_pokeapi.py` — 7/7 pass

The system must demonstrate that:
- Baseline requests through the proxy return identical results to direct upstream calls
- Each toxic type measurably degrades the connection in the expected dimension
- Disabling a proxy causes immediate `ConnectionError` in the client
- Removing a toxic fully restores clean-path behavior (no hysteresis)

This is the system's definition of done.
