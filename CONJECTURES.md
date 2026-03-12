# Conjectures

Tracking file for open conjectures per CLAUDE.md § Instrumentation Requirement.
Each entry records the hypothesis, research verdict, and which build steps produce data to evaluate it.

## C-001: Latency Toxic Additivity

**Hypothesis**: Observed latency = baseline + injected ± jitter (purely additive, no compounding).
**Status**: OPEN — research complete, **LIKELY TRUE**
**Build steps**: 4 (GitHub), 5 (Ollama), 6 (PokéAPI), 8 (latency sweep)
**Instrumentation**: Tests measure `(degraded - baseline) * 1000` and assert within expected range.
**Research**: `docs/conjecture-c001-research.md`
**Key finding**: Source code confirms timestamp-aware delay: `sleep = delay() - time.Since(c.Timestamp)`. This explicitly prevents compounding by subtracting time already elapsed. The 1024-chunk buffer decouples latency from throughput. For single-chunk responses (<32KB, typical for this project's APIs), delay applies exactly once. **Caveats**: Multi-chunk WAN responses may show partial absorption (delta *less* than injected). Multiple toxics can interact via timestamp propagation. Test should use warmup, session reuse, and single-toxic configuration.

## C-002: Ollama RTB SLA

**Hypothesis**: `nomic-embed-text` embedding latency < 100ms through clean proxy.
**Status**: OPEN — research complete + empirical data, **LIKELY TRUE**
**Build steps**: 5 (Ollama tests), 8 (latency sweep)
**Instrumentation**: `test_baseline_embed_under_100ms` measures wall-clock; sweep produces p50/p99.
**Research**: `docs/conjecture-c002-research.md`
**Key finding**: `test_baseline_embed_under_100ms` passed empirically. Observed baseline ~17ms (inferred from RTB breach test: 100ms injected → 117ms total). Budget: ~17ms model inference (nomic-embed-text:v1.5, 137M F16 on Apple Silicon) + ~3ms Docker networking + <1ms proxy = ~21ms. **Caveat**: Cold-start (first request after model eviction) will exceed 100ms. P99 validation requires 100-request sweep.

## C-003: Slicer JSON Validity

**Hypothesis**: TCP slicer at 1-byte avg preserves JSON validity after reassembly.
**Status**: OPEN — research complete, **LIKELY TRUE**
**Build steps**: 5 (Ollama slicer test), 6 (PokéAPI slicer test)
**Instrumentation**: `test_slicer_disrupts_streaming_generation` and `test_slicer_on_large_list` parse JSON after slicer.
**Research**: `docs/conjecture-c003-research.md`
**Key finding**: Slicer only sub-slices the byte array — never modifies, drops, or reorders bytes. TCP guarantees in-order delivery. Python `requests` reads complete responses from buffered sockets. No known data corruption bugs in Toxiproxy slicer.

## C-004: Prism Mock Equivalence

**Hypothesis**: Prism mock + Toxiproxy fully replaces live PokéAPI for test scenarios.
**Status**: OPEN — research complete, **LIKELY FALSE as stated**
**Build steps**: 7 (Prism mock setup)
**Instrumentation**: Run full PokéAPI test suite against Prism; diff status codes and schemas.
**Research**: `docs/conjecture-c004-research.md`
**Key finding**: Five independent failure modes: (1) name-based assertions (`== "pikachu"`) never match Prism's schema-generated data, (2) trailing slash mismatch between spec paths and test URLs causes 404s, (3) `len(results) == 100` incompatible with Prism's array generation, (4) nullable `base_experience` returns null, (5) semantic 404 for unknown pokemon impossible. **Recommendation**: Narrow conjecture to schema-structure equivalence, or create Prism-compatible test variants with shape assertions instead of value assertions.

## C-005: Bandwidth Proportionality

**Hypothesis**: Bandwidth throttle effect scales proportionally with payload size.
**Status**: OPEN — research complete, **LIKELY FALSE as stated**
**Build steps**: 4 (GitHub bandwidth test), 6 (PokéAPI bandwidth test)
**Instrumentation**: Tests measure elapsed time under bandwidth cap.
**Research**: `docs/conjecture-c005-research.md`
**Key finding**: The bandwidth toxic itself is mathematically linear in application bytes (`sleep = bytes / rate`). However, HTTP header overhead (300-800 bytes) represents 30-80% of total metered bytes for 1KB payloads, making the 1KB baseline disproportionately slow. Connection setup time adds constant overhead. **Recommendation**: Refine to test throughput = wire_bytes / transfer_time ≈ configured rate, or exclude <10KB payloads from proportionality assertion.
