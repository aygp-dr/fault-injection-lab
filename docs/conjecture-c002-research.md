# Conjecture C-002 Research: Ollama RTB SLA

**Conjecture**: `nomic-embed-text` via Ollama meets the <100ms RTB SLA under clean proxy conditions (no toxics).

**Falsification criterion**: Run 100 embedding requests through clean proxy; p99 latency must be <100ms.

**Date**: 2026-03-11

---

## 1. Empirical Observation

The test `test_baseline_embed_under_100ms` **passed** during the Step 5 acceptance run:

```
tests/test_ollama.py::test_baseline_embed_under_100ms PASSED
```

This confirms that at least one embedding request through the Toxiproxy clean path completed in under 100ms. The model used was `nomic-embed-text:v1.5` (pinned tag).

Additionally, `test_latency_pushes_embed_past_rtb_gate` with 100ms injected latency showed total elapsed of ~117ms, implying a baseline of ~17ms. This is well under the 100ms threshold.

## 2. Architecture

- **Model**: nomic-embed-text:v1.5 (137M parameters, F16 quantization, 274MB)
- **Runtime**: Ollama on macOS (Apple Silicon)
- **Proxy path**: Client -> Toxiproxy (:9002, Docker) -> host.docker.internal:11434 -> Ollama
- **Proxy overhead**: Toxiproxy adds <1ms for clean pass-through (documented: <100µs)

## 3. Factors Affecting Latency

### 3.1 Cold Start

Ollama lazy-loads models into memory on first request. The first embedding after model load includes:
- Model weight loading from disk (~200-500ms for 274MB model)
- GPU/Neural Engine initialization

Subsequent requests with the model already loaded are much faster. The test suite's `autouse` fixture calls `reset_all()` before each test, which hits the Toxiproxy API but does not unload Ollama models.

### 3.2 Hardware

On Apple Silicon (M1/M2/M3), nomic-embed-text runs on the Neural Engine or GPU. At 137M parameters with F16 quantization, inference for a short text prompt is typically 5-20ms.

### 3.3 Docker Networking

The proxy path crosses Docker's virtual network twice:
- Client to Toxiproxy (published port 9002)
- Toxiproxy to host.docker.internal (host networking)

Each hop adds ~0.5-2ms on macOS Docker Desktop.

## 4. Verdict: LIKELY TRUE

The conjecture is **likely true** based on:

1. **Empirical pass**: The acceptance test passed with the warm model
2. **Architecture**: 137M F16 model on Apple Silicon is well within <100ms budget
3. **Proxy overhead**: <1ms for clean Toxiproxy path
4. **Budget breakdown**: ~17ms model inference + ~3ms Docker networking + <1ms proxy = ~21ms total

### Conditions for Falsification

- **Cold start**: First request after model eviction will exceed 100ms (model loading). Tests should warm up the model first.
- **CPU-only inference**: On machines without GPU/Neural Engine, 137M F16 inference may exceed 100ms.
- **System load**: Heavy concurrent workload could push inference past 100ms.
- **Larger prompts**: Very long input text increases tokenization and inference time.

### Instrumentation Needed

The single-request test is necessary but not sufficient for p99 validation. A proper 100-request sweep should be added per the falsification criterion.
