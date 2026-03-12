# Conjecture C-002 Research: nomic-embed-text via Ollama Meets <100ms RTB SLA

**Conjecture**: `nomic-embed-text` via Ollama meets the <100ms RTB SLA under clean proxy conditions (no toxics).

**Falsification criterion**: Run 100 embedding requests through clean proxy; p99 latency must be <100ms.

**Date**: 2026-03-11

---

## 1. Empirical Observation (Prior Art)

The test `test_baseline_embed_under_100ms` **passed** during the Step 5 acceptance run:

```
tests/test_ollama.py::test_baseline_embed_under_100ms PASSED
```

This confirms that at least one embedding request through the Toxiproxy clean path completed in under 100ms. The model used was `nomic-embed-text:v1.5` (pinned tag).

Additionally, `test_latency_pushes_embed_past_rtb_gate` with 60ms injected latency showed total elapsed of ~117ms, implying a baseline of ~17ms. This is well under the 100ms threshold but is a single data point, not the 100-request p99 required by the falsification criterion.

---

## 2. nomic-embed-text Model Characteristics

### Model Profile

| Property | Value |
|----------|-------|
| Parameters | 137M |
| Architecture | BERT-based encoder (nomic-bert) |
| Output dimensions | 768 |
| Context window | 2,048 tokens (up to 8,192 with v1.5) |
| Quantization (Ollama default) | F16 |
| File size on disk | ~274 MB |

nomic-embed-text is a small, purpose-built text embedding model. At 137M parameters, it is significantly smaller than generative LLMs (compare: Qwen3 0.6B at 600M+ params, or Llama 3 8B). Inference time for a forward pass scales roughly with parameter count and sequence length, so this model is inherently fast.

### Expected Raw Inference Latency

Based on available benchmark data:

- **llama.cpp (Metal, BERT-class model)**: A 33M F16 BERT model achieves ~1,795 tokens/sec at batch size 1 on Apple Metal. For nomic-embed-text at 137M parameters (~4x larger), a proportional estimate gives ~450 tokens/sec at batch size 1.
- **nomic-embed-text v1 Q4_0 benchmark**: 50 tokens processed in 180.80ms prompt eval time (3.62 ms/token), with 302.62ms total including load time. This was on an unspecified but likely mid-range system.
- **Apple M2 Max (96GB)**: nomic-embed-text achieves ~9,340 tokens/sec at batch size 128. At batch size 1, throughput is lower but still fast for a short prompt.
- **RTX 4090**: 12,450 tokens/sec at batch size 256 (reference ceiling for GPU inference).
- **Intel i9-13900K CPU**: 3,250 tokens/sec at batch size 32.

For a typical short embedding prompt like "East Boston waterfront" (~4 tokens), the raw model forward pass should take:
- At 450 tokens/sec (conservative batch-1 Metal estimate): ~9ms
- At 1,000 tokens/sec (optimistic warm Metal): ~4ms
- At 3,250 tokens/sec (high-end CPU): ~1.2ms

**Raw model inference for a short prompt is well under 100ms -- likely in the 2-15ms range on any modern hardware.**

### Nomic's Own Performance Claims

Nomic's published benchmarks show local inference on a 2023 MacBook Pro (16GB) is faster than remote API inference for inputs up to 1,024 tokens. This implies single-digit to low-double-digit millisecond latency for short prompts on Apple Silicon.

---

## 3. Ollama HTTP API Overhead

### Architecture

Ollama wraps llama.cpp inference in an HTTP server. The request path for `/api/embeddings` is:

```
Client HTTP request
  -> Ollama HTTP server (Go)
  -> JSON deserialization
  -> Tokenization
  -> llama.cpp model inference (Metal/CUDA/CPU)
  -> Embedding vector extraction
  -> JSON serialization (768 floats)
  -> HTTP response
```

### Measured Overhead

| Source | Measurement | Details |
|--------|------------|---------|
| Ollama issue #7400 (pre-fix) | ~700-780ms per embed call | Severe: tokenization bug caused per-item API calls |
| Ollama issue #7400 (post-fix, PR #7424) | ~14.2ms total duration | After batching optimization; 17x improvement |
| Collabnix benchmark (2025) | 15-50ms average | Local Ollama embedding, model and hardware unspecified |
| Ollama API response example | total_duration: ~351ms | From docs; likely includes cold-start load |

**Critical finding**: Ollama's embedding API performance improved dramatically after PR #7424 (merged late 2024). Prior to this fix, each embedding request triggered redundant tokenization/detokenization cycles, inflating latency by 10-17x. Post-fix, the overhead is reasonable.

### Response Timing Fields

The newer `/api/embed` endpoint returns timing data in nanoseconds:

```json
{
  "model": "nomic-embed-text",
  "embeddings": [[...]],
  "total_duration": 351441147,
  "load_duration": 1014270,
  "prompt_eval_count": 12
}
```

- `total_duration`: end-to-end time including all overhead
- `load_duration`: time to load model into memory (~1ms when already warm, seconds when cold)

**Note**: The legacy `/api/embeddings` endpoint (used in the spec.org test code) does NOT return timing fields -- only the embedding vector. The test must measure latency externally via `time.monotonic()`, which is what the spec.org code already does.

### Cold Start vs Warm Model

| Scenario | Expected Latency | Notes |
|----------|-----------------|-------|
| Cold start (model not in memory) | 2-5 seconds | Must load 274MB from disk into GPU/RAM |
| First request after load | 15-50ms | Model warm, first inference may include JIT/cache warmup |
| Subsequent requests (warm) | 5-30ms | Steady-state performance |
| After 5min idle (default keep_alive) | 2-5 seconds | Model unloaded, must reload |

The `keep_alive` parameter defaults to 5 minutes. After 5 minutes of inactivity, Ollama unloads the model, and the next request incurs a multi-second cold-start penalty. This is critical for the p99 measurement:

- **If the 100-request test runs continuously**, all requests after the first will be warm. A cold start on request #1 would be the p99 outlier at 2-5 seconds -- far exceeding 100ms.
- **If the model is pre-warmed** (e.g., by sending a throwaway request before the benchmark), all 100 requests should be in the 5-30ms range.

### Preloading Strategy

Ollama supports preloading via an empty request:
```bash
curl http://localhost:11434/api/embed -d '{"model": "nomic-embed-text", "input": "warmup", "keep_alive": -1}'
```

Setting `keep_alive` to `-1` keeps the model loaded indefinitely, eliminating cold-start risk during testing.

---

## 4. Toxiproxy Clean-Path Overhead

### Architecture

With no toxics enabled, Toxiproxy operates as a simple TCP pass-through:

```
Client -> [TCP connect] -> Toxiproxy :9002 -> [TCP connect] -> Ollama :11434
```

Data flows through two TCP connections with Go goroutines copying bytes between them. The toxic pipeline is instantiated but has no toxic handlers to process.

### Measured Performance

| Metric | Value | Source |
|--------|-------|--------|
| Pass-through latency (no toxics) | <100 microseconds | Toxiproxy README |
| Throughput (GOMAXPROCS=4, MacBook Pro) | ~1,000 MB/s | Toxiproxy README |
| Throughput (higher-end desktop) | ~2,400 MB/s | Toxiproxy README |

**Sub-100-microsecond latency for clean pass-through is negligible.** For an operation with a 100ms SLA, the proxy adds <0.1% overhead. This is well within measurement noise.

### Why the Overhead Is So Low

- Toxiproxy is written in Go, with efficient goroutine scheduling and fast `io.Copy` paths.
- On localhost (loopback), TCP avoids full network stack processing -- no ARP, no routing, no NIC interrupts.
- With no toxics, the `ToxicStub` pipeline is a trivial pass-through with no `time.Sleep`, no buffering, no packet manipulation.

### Docker Networking Consideration

In the project's architecture, Toxiproxy runs in a Docker container while Ollama runs on the host. On macOS, Docker Desktop uses a Linux VM, so the connection from Toxiproxy to Ollama traverses:

```
Client (host) -> Docker VM network -> Toxiproxy (container) -> Docker VM network -> Ollama (host)
```

This Docker networking layer adds overhead compared to pure loopback, typically 0.5-2ms per round trip on macOS Docker Desktop. Still well under 100ms, but a meaningful fraction of the ~17ms baseline observed empirically.

---

## 5. Hardware Considerations

### Apple Silicon GPU Acceleration

Ollama uses llama.cpp's Metal backend on Apple Silicon Macs, offloading matrix operations to the GPU. For a 137M parameter model:

- The entire model fits in unified memory (274MB is trivial against typical 8-32GB unified memory).
- Metal compute shaders handle the transformer forward pass.
- No CPU-GPU memory transfer overhead (unified memory architecture).

Expected performance tiers for nomic-embed-text on Apple Silicon:

| Chip | Expected Single-Request Latency | Notes |
|------|-------------------------------|-------|
| M1 (8GB) | 10-30ms | Base tier, 8-core GPU |
| M1 Pro/Max | 5-20ms | More GPU cores, higher bandwidth |
| M2 (8GB) | 8-25ms | Improved GPU architecture |
| M2 Pro/Max | 3-15ms | High-end; ~9,340 tok/s at batch 128 |
| M3 Pro/Max | 3-12ms | Latest generation |

### CPU-Only Inference

If Metal acceleration is unavailable (e.g., running Ollama inside Docker on macOS, where GPU passthrough is not supported), inference falls back to CPU:

- CPU inference for 137M parameters is still fast: Intel i9-13900K achieves 3,250 tokens/sec.
- On Apple Silicon CPUs (high-performance cores), expect similar or better throughput due to wide SIMD (NEON) support.
- Single short-prompt latency on CPU: estimated 5-40ms depending on core count and frequency.

**Important**: The Ollama FAQ explicitly states "GPU acceleration is not available for Docker Desktop in macOS." However, in the project's architecture, Ollama runs on the host (not in Docker), so Metal GPU acceleration is available.

### Memory Pressure

nomic-embed-text requires approximately:
- Model weights: 274MB
- KV cache / working memory: ~72MB
- Total: ~350MB

This is trivial on modern systems. Memory pressure is not a concern.

---

## 6. End-to-End Latency Budget

For a single embedding request through the clean proxy (warm model, Apple Silicon):

| Component | Estimated Latency | Range |
|-----------|------------------|-------|
| Client HTTP overhead (Python `requests`) | 1-3ms | JSON serialization, socket setup |
| TCP loopback to Toxiproxy | <0.1ms | Localhost TCP |
| Toxiproxy pass-through | <0.1ms | No toxics, <100us documented |
| TCP from Toxiproxy to Ollama | 0.5-2ms | Docker VM networking on macOS |
| Ollama HTTP server overhead | 1-3ms | Go HTTP handler, JSON parsing |
| Tokenization | <1ms | Short prompt, ~4 tokens |
| Model inference (Metal GPU, warm) | 3-15ms | 137M params, short sequence |
| Response serialization | 1-2ms | 768-dim float vector to JSON |
| TCP return path | 0.5-2ms | Return through proxy |
| **Total (warm, Apple Silicon GPU)** | **~8-28ms** | **Well under 100ms** |
| **Total (warm, CPU fallback)** | **~15-50ms** | **Still under 100ms** |
| **Total (cold start)** | **2,000-5,000ms** | **Fails SLA** |

The empirically observed ~17ms baseline is consistent with this budget, suggesting the model was warm and GPU-accelerated during the acceptance test.

---

## 7. Risk Factors for p99 Exceeding 100ms

### 7a. Cold Start (HIGH RISK)

If the first request in the 100-request test hits a cold model, that single request will take 2-5 seconds. Since p99 of 100 requests means the 99th percentile (effectively the single worst request), **one cold-start request would blow the p99 past 100ms**.

**Mitigation**: Pre-warm the model before the benchmark. The spec.org test code does NOT currently include a warm-up step.

### 7b. Ollama Model Scheduling (MEDIUM RISK)

If another model is loaded in Ollama (e.g., `qwen3:0.6b` from the generation tests), Ollama may need to unload it before loading nomic-embed-text. This adds seconds to the first embedding request.

**Mitigation**: Ensure nomic-embed-text is the only loaded model, or pre-warm it with `keep_alive: -1`.

### 7c. GC Pauses (LOW RISK)

Both Ollama (Go) and llama.cpp can experience garbage collection pauses. Go's GC is typically <1ms for small heaps, and Ollama manages model memory outside the GC heap. Unlikely to cause >100ms pauses.

### 7d. System Load / Thermal Throttling (LOW RISK)

On a laptop under sustained load, thermal throttling can reduce GPU/CPU clock speeds. For 100 sequential requests, the total workload is light (a few seconds), unlikely to trigger throttling.

### 7e. Docker Networking Variance (LOW RISK)

macOS Docker Desktop networking can occasionally spike to 5-10ms per hop. Even worst-case 20ms networking overhead keeps total well under 100ms.

### 7f. Ollama Embedding Regression (LOW RISK)

Issue #14314 reports embeddings getting progressively slower over many requests. This appears to be a memory leak or state accumulation bug. For a 100-request test, this is unlikely to manifest, but would be a concern for production workloads.

---

## 8. Analysis of Existing Test Code vs Falsification Criterion

The spec.org test `test_baseline_embed_under_100ms` is:

```python
def test_baseline_embed_under_100ms():
    """RTB gate: embedding latency must be < 100ms without fault."""
    t0 = time.monotonic()
    r = embed("East Boston waterfront")
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert r.status_code == 200
    assert elapsed_ms < 100, f"Embedding took {elapsed_ms:.1f}ms -- fails RTB SLA"
```

This is a **single-request test**, not the 100-request p99 test required by the conjecture's falsification criterion. The gap between "one request passed" and "p99 of 100 requests passes" is significant because:

1. A single passing observation does not establish the distribution.
2. Tail latency (p99) can be 2-5x the median.
3. The first request may or may not be a cold start depending on test ordering.

---

## 9. Verdict: LIKELY TRUE (with conditions)

The conjecture is **likely true** under the following conditions:

1. **The model is pre-warmed** (not cold-starting). Cold start latency of 2-5 seconds would trivially falsify the conjecture.
2. **Ollama is running a post-PR-#7424 version** (late 2024+). Older versions had a severe tokenization bug that inflated embedding latency by 10-17x.
3. **The host has Apple Silicon or a discrete GPU** with Metal/CUDA acceleration. CPU-only inference is slower but likely still under 100ms for short prompts.
4. **No other models are contending for GPU memory** in Ollama at test time.

**Expected p99 for 100 warm requests on Apple Silicon: 15-40ms** -- well within the <100ms SLA, with substantial headroom.

### Why "Likely True" Rather Than "Certainly True"

- The empirical observation (~17ms baseline) is a single data point, not a p99 distribution.
- Published benchmarks aggregate across hardware; no specific batch-1 Apple Silicon latency is documented for this exact configuration.
- The p99 (not mean) is what matters. Tail latency from GC pauses, scheduling jitter, or Docker networking spikes could push individual requests higher -- though likely not past 100ms given the ~80ms headroom.

### Conditions That Would Falsify

The conjecture would be **false** if:
- The test includes the cold-start request in the p99 calculation (nearly certain >100ms)
- The system uses CPU-only inference on a slow processor (e.g., low-power Intel laptop)
- Ollama is running an old version with the tokenization bug (#7400, pre-PR #7424)
- Another large model is loaded and Ollama must swap models mid-test
- The progressive slowdown bug (#14314) manifests within 100 requests

### Needs Instrumentation

The conjecture cannot be definitively confirmed or refuted without running the actual 100-request benchmark. The existing single-request test provides strong evidence but does not satisfy the stated falsification criterion.

---

## 10. Recommended Instrumentation

```python
import time
import statistics

def test_c002_embed_p99_under_100ms():
    """C-002: 100 embedding requests through clean proxy; p99 < 100ms."""
    # Warm-up: ensure model is loaded and steady-state
    for _ in range(3):
        embed("warmup")

    # Benchmark
    latencies = []
    for _ in range(100):
        t0 = time.monotonic()
        r = embed("East Boston waterfront")
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert r.status_code == 200
        latencies.append(elapsed_ms)

    latencies.sort()
    p50 = latencies[49]
    p95 = latencies[94]
    p99 = latencies[98]
    mean_val = statistics.mean(latencies)

    print(f"Latency (ms): mean={mean_val:.1f} p50={p50:.1f} p95={p95:.1f} p99={p99:.1f}")
    print(f"Min={latencies[0]:.1f} Max={latencies[99]:.1f}")

    assert p99 < 100, f"p99={p99:.1f}ms exceeds 100ms RTB SLA"
```

Additionally, consider using the newer `/api/embed` endpoint to capture Ollama's internal timing (returned as `total_duration` and `load_duration` in nanoseconds), which would help distinguish model inference time from HTTP/proxy overhead.

---

## Sources

- [Ollama API Reference (embeddings)](https://ollama.readthedocs.io/en/api/)
- [Ollama FAQ - Model Loading and keep_alive](https://docs.ollama.com/faq)
- [Ollama Embedding Capabilities](https://docs.ollama.com/capabilities/embeddings)
- [Ollama Embedding Models Blog Post](https://ollama.com/blog/embedding-models)
- [Ollama nomic-embed-text Model Page](https://ollama.com/library/nomic-embed-text)
- [Ollama Issue #7400 - REST API Embedding Slowness (pre/post fix)](https://github.com/ollama/ollama/issues/7400)
- [Ollama Issue #6401 - Embedding Model keep_alive](https://github.com/ollama/ollama/issues/6401)
- [Ollama Issue #14314 - Embeddings Getting Slower](https://github.com/ollama/ollama/issues/14314)
- [Collabnix - Ollama Embedded Models Guide (latency benchmarks)](https://collabnix.com/ollama-embedded-models-the-complete-technical-guide-to-local-ai-embeddings-in-2025/)
- [Toxiproxy Repository (performance claims)](https://github.com/Shopify/toxiproxy)
- [Nomic Embed Technical Report](https://arxiv.org/html/2402.01613v2)
- [nomic-embed-text-v1.5 GGUF (Hugging Face)](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF)
- [llama.cpp Embedding Tutorial (Discussion #7712)](https://github.com/ggml-org/llama.cpp/discussions/7712)
- [llama.cpp Apple Silicon Performance (Discussion #4167)](https://github.com/ggml-org/llama.cpp/discussions/4167)
