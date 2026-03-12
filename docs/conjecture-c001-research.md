# Conjecture C-001 Research: Toxiproxy Latency Toxic Is Purely Additive

**Conjecture**: Toxiproxy latency toxic is purely additive -- observed latency =
baseline + injected +/- jitter, with no compounding or absorption effects.

**Falsification criterion**: Measure baseline and degraded latency across 50
requests; delta should be within injected +/- jitter range for >95% of samples.

**Date**: 2026-03-11

---

## 1. How the Latency Toxic Works Internally

### Source Code Analysis (toxics/latency.go)

The complete implementation of the latency toxic, retrieved from
[Shopify/toxiproxy](https://github.com/Shopify/toxiproxy/blob/main/toxics/latency.go):

```go
type LatencyToxic struct {
    Latency int64 `json:"latency"`
    Jitter  int64 `json:"jitter"`
}

func (t *LatencyToxic) GetBufferSize() int {
    return 1024
}

func (t *LatencyToxic) delay() time.Duration {
    delay := t.Latency
    jitter := t.Jitter
    if jitter > 0 {
        delay += rand.Int63n(jitter*2) - jitter
    }
    return time.Duration(delay) * time.Millisecond
}

func (t *LatencyToxic) Pipe(stub *ToxicStub) {
    for {
        select {
        case <-stub.Interrupt:
            return
        case c := <-stub.Input:
            if c == nil {
                stub.Close()
                return
            }
            sleep := t.delay() - time.Since(c.Timestamp)
            select {
            case <-time.After(sleep):
                c.Timestamp = c.Timestamp.Add(sleep)
                stub.Output <- c
            case <-stub.Interrupt:
                stub.Output <- c
                return
            }
        }
    }
}
```

### Key Mechanism Details

**1. Jitter distribution**: The jitter uses a **uniform distribution** over
`[latency - jitter, latency + jitter]`. The expression `rand.Int63n(jitter*2) - jitter`
produces a value in `[-jitter, +jitter)` which is added to the base latency. This is
*not* a normal/Gaussian distribution. The randomization uses Go's `math/rand`
(non-cryptographic PRNG).

**2. Timestamp-aware delay**: The critical line is:

```go
sleep := t.delay() - time.Since(c.Timestamp)
```

Each `StreamChunk` carries a `Timestamp` field set to `time.Now()` when the data
was first read from the source socket (in `ChanWriter.Write()`). The latency
toxic computes how long the chunk has *already been waiting* in the proxy pipeline
and subtracts that from the target delay. This means:

- If a chunk has been sitting in the 1024-chunk input buffer for 50ms, and the
  target delay is 300ms, the toxic only sleeps for 250ms.
- If the chunk has already waited longer than the target delay (e.g., due to a
  preceding slow toxic or system scheduling), the sleep duration goes negative.
  `time.After` with a negative or zero duration fires immediately, so no
  additional delay is added.

**3. Timestamp propagation**: After sleeping, the toxic updates the chunk's
timestamp: `c.Timestamp = c.Timestamp.Add(sleep)`. This allows downstream
toxics in the chain to correctly account for time already spent. However, it
adds `sleep` (the computed delay), not the actual wall-clock time elapsed, so
minor imprecision in `time.After` can accumulate.

**4. Per-chunk, not per-request**: The `Pipe()` loop processes one `StreamChunk`
at a time. It delays *each chunk individually*. An HTTP response that arrives
as multiple TCP reads (and therefore multiple `StreamChunk`s) will have the
delay applied to each chunk.

**5. Buffer size**: `GetBufferSize()` returns 1024, meaning the latency toxic's
input channel can hold up to 1024 `StreamChunk`s before backpressure blocks the
upstream writer. This prevents the latency delay from throttling upstream
bandwidth.

---

## 2. Per-Chunk vs Per-Request: The Critical Distinction

### How Toxiproxy sees data

Toxiproxy operates at the TCP level. It does not parse HTTP. Data flows as:

```
Source socket -> io.Copy (32KB buffer) -> ChanWriter -> [StreamChunk channel]
  -> NoopToxic.Pipe() -> [channel] -> LatencyToxic.Pipe() -> [channel]
  -> ChanReader -> io.Copy -> Destination socket
```

Go's `io.Copy` uses a 32KB default buffer. Each `read()` from the TCP socket
produces one `StreamChunk` of up to 32KB. A small HTTP response (headers + body
under 32KB) will typically arrive as a single chunk on localhost. A large response
or one arriving over WAN may be split across multiple chunks.

### Implications for the conjecture

**Best case (single chunk per response)**: When the entire HTTP response fits in
one `StreamChunk`, the latency toxic delays it once. The observed latency delta
is `delay() - time_already_waited`, which closely approximates the configured
latency +/- jitter. **The conjecture holds.**

**Worse case (multiple chunks per response)**: When an HTTP response spans
multiple `StreamChunk`s, each chunk is delayed independently. However, the
timestamp-aware mechanism compensates: if chunks arrive in rapid succession
and queue in the 1024-element buffer, `time.Since(c.Timestamp)` grows for
later chunks, and `sleep` decreases correspondingly. Chunks that have waited
longer than the target delay pass through immediately.

**In practice for this project's test scenarios**: The typical API responses
are small:

- PokéAPI `/api/v2/pokemon/pikachu`: ~10-15KB JSON
- GitHub `/repos/octocat/Hello-World`: ~5KB JSON
- Ollama `/api/embeddings`: ~2-3KB JSON

On localhost (Toxiproxy running locally or in Docker), these responses will
almost always arrive as a single `io.Copy` read and therefore a single
`StreamChunk`. The delay is applied once.

For WAN upstreams (real GitHub API, real PokéAPI), responses may arrive in
multiple TCP segments over time. In this case:

- The first chunk is delayed by the full configured latency.
- Subsequent chunks that have already waited in the buffer may pass through
  with reduced or zero additional delay.
- **Total observed latency is dominated by the delay on the first chunk.**

This is *not* compounding. The timestamp-aware mechanism specifically prevents
compounding by subtracting time already elapsed. In fact, for multi-chunk
responses, the total wall-clock delay may be *less* than `latency +/- jitter`
if later chunks overlap with the first chunk's sleep period.

### Confirmed by Toxiproxy maintainer

In [Issue #239](https://github.com/Shopify/toxiproxy/issues/239), maintainer
Jacob Wirth noted: "Since Toxiproxy works with buffers, the chance could be
slightly higher if your HTTP request gets split up between 2 buffers." This
confirms that the toxic operates per-buffer, but the timestamp mechanism
mitigates most multi-buffer effects.

---

## 3. Toxic Pipeline Interactions

### How multiple toxics chain together

From `link.go`, toxics are connected in a pipeline:

```
Input > ToxicStub (Noop) > ToxicStub (Latency) > ToxicStub (Bandwidth) > Output
```

Each `ToxicStub` has its own input and output channel. The output of one toxic
feeds the input of the next. New toxics are always appended to the end of the
chain.

### Latency + Bandwidth interaction

When both latency and bandwidth toxics are active on the same proxy:

1. **Latency applied first, then bandwidth** (if latency is added before
   bandwidth, it gets a lower index in the chain). Chunks are delayed by the
   latency toxic, then rate-limited by the bandwidth toxic. The total delay
   is additive: `latency_delay + bandwidth_delay`.

2. **Bandwidth applied first, then latency** (if bandwidth is added first).
   The bandwidth toxic rate-limits chunk delivery. Chunks arrive at the
   latency toxic's input with timestamps reflecting when they were *originally
   received*, not when the bandwidth toxic released them. Since the latency
   toxic subtracts `time.Since(c.Timestamp)`, and the bandwidth toxic has
   already consumed that time, the latency toxic may compute a negative sleep
   and pass chunks through immediately.

**Key finding**: The ordering of toxic addition matters. If the bandwidth toxic
delays a chunk for longer than the latency toxic's configured delay, the latency
toxic becomes a no-op (absorption effect). This is a form of **absorption**, not
compounding.

### Latency + Slicer interaction

If the slicer toxic precedes the latency toxic in the chain, one input chunk
becomes many small output chunks. Each small chunk then receives an independent
delay from the latency toxic. However, the timestamp-aware mechanism compensates:
all the small chunks carry the *original* timestamp from when the data was first
read from the socket. Chunks sliced later in the sequence have a larger
`time.Since(c.Timestamp)` value and therefore shorter sleep times.

### Latency + Latency (multiple latency toxics)

If two latency toxics are chained (e.g., one upstream, one downstream, or two on
the same stream with different names), the delays are *not* simply additive. The
second latency toxic sees the chunk's timestamp as modified by the first:
`c.Timestamp = c.Timestamp.Add(sleep)`. The second toxic then computes:

```go
sleep2 = t2.delay() - time.Since(c.Timestamp_after_first_toxic)
```

Since the first toxic updated the timestamp to account for its delay, the second
toxic's `time.Since()` measures only time elapsed *after* the first toxic's delay.
If the second toxic processes the chunk immediately after the first releases it,
`time.Since()` is near zero and the second toxic applies its full delay.
**Two latency toxics in series are approximately additive.**

---

## 4. Factors That Could Cause Non-Additivity

### 4.1 TCP Buffering and Nagle's Algorithm

Nagle's algorithm coalesces small TCP writes into larger segments. Since Toxiproxy
reads from the source socket (already reassembled by the kernel TCP stack) and
writes to the destination socket, Nagle's algorithm could affect timing:

- **On the upstream connection**: Data from the upstream server may be coalesced,
  meaning it arrives at Toxiproxy as fewer, larger chunks. This reduces the
  number of chunks the latency toxic must process. For a single-chunk response,
  this is irrelevant. For multi-chunk responses, coalescing *helps* additivity
  by reducing the number of independent delay events.

- **On the downstream connection**: After the latency toxic delays and releases
  chunks, Go's TCP stack may coalesce them before sending to the client. This
  does not affect the timing as measured by the client.

**Impact on conjecture**: Nagle's algorithm may slightly *reduce* observed
latency variance by coalescing chunks, making additivity *more* likely to hold.
Not a source of non-additivity.

### 4.2 Kernel Scheduling and Timer Precision

The latency toxic uses `time.After(sleep)` which relies on Go's runtime timer,
which in turn relies on the OS kernel's timer mechanism:

- **Linux**: Default timer resolution is ~1ms (HZ=1000 on most kernels). Go's
  runtime uses `runtime.usleep` or `epoll_wait` with millisecond timeout.
- **macOS (Darwin)**: Timer resolution is ~1ms.

For a 300ms configured latency, the actual sleep will be 300ms +/- 1-2ms due to
timer imprecision and goroutine scheduling. This is well within the typical
jitter range and does not constitute non-additivity.

**Impact on conjecture**: Adds ~1-2ms of noise. With jitter=0, this is the
dominant source of measurement variance. With jitter=20ms, it is negligible.

### 4.3 Connection Pooling and HTTP Keep-Alive

Toxiproxy creates a new upstream connection for each client connection (see
`proxy.go`: `net.Dial("tcp", proxy.Upstream)` in the accept loop). Each
connection gets its own toxic pipeline instance.

Python `requests` uses `urllib3` connection pooling by default. Within a
`requests.Session`, connections are reused via HTTP keep-alive. This means:

- **First request**: TCP handshake to Toxiproxy + TCP handshake to upstream +
  request/response + latency delay. The initial handshake adds ~1-5ms on
  localhost.
- **Subsequent requests on same connection**: No TCP handshake overhead.
  Request/response + latency delay only.

**Important**: The latency toxic instance persists for the lifetime of the TCP
connection. The `Pipe()` function runs in a continuous loop, processing chunks
for all HTTP requests on that connection. The delay is applied to *each chunk*,
not per-connection. Multiple HTTP requests over a keep-alive connection each
get their own delay.

**Impact on conjecture**: The first request may show a slightly higher baseline
due to TCP handshake overhead, but subsequent requests on the same connection
will not. Using `requests.Session` (which the test code does implicitly) may
show this effect. For the falsification test, warmup requests should be used to
establish the connection before measurement begins.

### 4.4 TLS Handshake Interaction

For HTTPS upstreams (GitHub, PokéAPI), Toxiproxy proxies raw TCP. As noted in
CLAUDE.md, the project uses plain HTTP to the proxy port. If TLS were involved:

- The TLS handshake would produce multiple small TCP exchanges.
- Each exchange would be a separate `StreamChunk` in Toxiproxy.
- The latency toxic would delay each handshake message, potentially multiplying
  the handshake time by the number of round-trips (typically 2-4).

**However**, this project explicitly avoids TLS by talking HTTP to the proxy.
The TLS termination happens between Toxiproxy and the upstream, which is on the
*upstream* side of the proxy, not in the toxic pipeline. Toxiproxy's toxic chain
only processes data flowing through the proxied TCP stream.

**Impact on conjecture**: None for this project's configuration. The downstream
latency toxic does not see TLS handshake traffic.

### 4.5 Toxicity Parameter

Each toxic has a `toxicity` parameter (default 1.0). This probability is
evaluated **per-connection** in `ToxicStub.Run()`:

```go
func (s *ToxicStub) Run(toxic *ToxicWrapper) {
    randomToxicity := rand.Float32()
    if randomToxicity < toxic.Toxicity {
        toxic.Pipe(s)
    } else {
        new(NoopToxic).Pipe(s)
    }
}
```

At the default toxicity of 1.0, the toxic always applies. But if toxicity < 1.0,
some connections get the latency toxic and others get a noop. The decision is
made once per connection, not per chunk or per request.

**Impact on conjecture**: At default toxicity (1.0), no effect. If toxicity is
set below 1.0, some connections will show no latency at all while others show
the full amount -- a bimodal distribution that would appear as non-additivity
unless accounted for in the test design.

### 4.6 Go's time.After with Negative Duration

If the computed sleep is negative (chunk waited longer than the target delay),
`time.After(sleep)` fires immediately (Go's timer library treats non-positive
durations as immediate). The chunk passes through with zero additional delay.
The original timestamp is still updated: `c.Timestamp = c.Timestamp.Add(sleep)`,
where `sleep` is the negative value. This means the timestamp is moved *backward*
in time, which could cause the next toxic in the chain to add *more* delay than
expected.

**Impact on conjecture**: This is a subtle edge case. In a single-toxic
configuration (just latency), it means the delay is capped at zero from below --
never negative. This is correct behavior. In multi-toxic chains, the backward
timestamp adjustment could cause unexpected interactions. For the single-latency
test in C-001, this is not a concern.

---

## 5. Known Issues and Edge Cases

### 5.1 Issue #239: Percentage of Requests Delayed

The maintainer confirmed that latency applies per-buffer and that HTTP requests
split across multiple buffers could see "slightly higher" chance of experiencing
delay. This is relevant when `toxicity < 1.0` but does not indicate non-linear
behavior at `toxicity = 1.0`.

### 5.2 Issue #412: Toxiproxy Hangs During Toxic Removal

Removing a toxic during active traffic can cause hangs. This is a control-plane
issue. For the falsification test, toxics should be added/removed when no
requests are in flight.

### 5.3 Issue #148: Race Between Connection and Toxic Creation

If a toxic is added while a connection is being established, the toxic may not
apply to that connection. The falsification test should add the toxic, wait
briefly, then begin measurements to avoid this race.

### 5.4 Issue #254: API Hangs After Long Idle

The Toxiproxy control API can hang after extended idle periods. Not relevant
to latency measurement but could affect test setup.

### 5.5 No Known Non-Linear Latency Behavior

After searching the Toxiproxy issue tracker, web resources, and analyzing the
source code, **no reports of non-linear, compounding, or absorbing latency
behavior were found** for the single-latency-toxic case. The timestamp-aware
mechanism is specifically designed to prevent compounding.

---

## 6. Recommended Test Design for Falsification

### 6.1 Test Parameters

```
Proxy:       pokeapi (or ollama for local upstream)
Endpoint:    /api/v2/pokemon/pikachu (small, stable, cacheable)
Injected:    latency_ms = 300, jitter_ms = 0  (initially zero jitter for cleaner signal)
Samples:     50 requests
Direction:   downstream
Toxicity:    1.0 (default)
```

### 6.2 Test Procedure

```python
import time
import statistics
import requests

PROXY = "http://localhost:9003"
ENDPOINT = "/api/v2/pokemon/pikachu"
N = 50
INJECTED_MS = 300
JITTER_MS = 0

session = requests.Session()

# Phase 1: Warmup (establish connection, avoid cold-start effects)
for _ in range(5):
    session.get(f"{PROXY}{ENDPOINT}", timeout=10)

# Phase 2: Baseline measurement (no toxic)
baselines = []
for _ in range(N):
    t0 = time.monotonic()
    r = session.get(f"{PROXY}{ENDPOINT}", timeout=10)
    assert r.status_code == 200
    baselines.append((time.monotonic() - t0) * 1000)

baseline_median = statistics.median(baselines)

# Phase 3: Add latency toxic
toxi.add_latency("pokeapi", latency_ms=INJECTED_MS, jitter_ms=JITTER_MS)

# Brief pause to ensure toxic is active
time.sleep(0.1)

# Phase 4: Degraded measurement
degraded = []
for _ in range(N):
    t0 = time.monotonic()
    r = session.get(f"{PROXY}{ENDPOINT}", timeout=10)
    assert r.status_code == 200
    degraded.append((time.monotonic() - t0) * 1000)

# Phase 5: Analysis
deltas = [d - baseline_median for d in degraded]
lower = INJECTED_MS - JITTER_MS - 50  # 50ms tolerance for system noise
upper = INJECTED_MS + JITTER_MS + 50

in_range = sum(1 for d in deltas if lower <= d <= upper)
pct = in_range / len(deltas) * 100

assert pct > 95, f"Only {pct:.1f}% of deltas in range [{lower}, {upper}]ms"
```

### 6.3 Important Design Decisions

**Use `requests.Session`**: Reuses TCP connections via keep-alive, eliminating
TCP handshake variance after warmup. This isolates the latency toxic's effect.

**Subtract median baseline, not mean**: The median is robust to outlier baseline
measurements (e.g., GC pauses, DNS lookups on the first request).

**50ms tolerance band**: Even with jitter=0, system-level noise (kernel
scheduling, timer precision, Go goroutine scheduling) adds ~1-5ms of variance.
A 50ms tolerance (300 +/- 50ms for 300ms injected) provides margin without
being so loose that real non-additivity is missed.

**Use a local upstream if possible**: Prism mock (PokéAPI) or Ollama (localhost)
eliminates WAN variance. If testing against real GitHub/PokéAPI, baseline
variance will be much higher and the tolerance band may need to widen.

### 6.4 Additional Tests

**Test with jitter**: Repeat with `jitter_ms=50`. Deltas should follow a
uniform distribution over `[250, 350]` (plus system noise). A Kolmogorov-Smirnov
test against `Uniform(250, 350)` would be more rigorous than a simple range
check.

**Test with concurrent connections**: Open 5 sessions simultaneously. Each
should show independent additive latency. Verify no cross-connection
interference.

**Test delta stability over time**: Run 200 requests and plot delta over time.
Look for drift, trends, or step changes that would indicate non-additive
behavior.

**Test with large responses**: Use an endpoint returning 50KB+ to verify that
multi-chunk responses still show approximately additive latency (the
timestamp-aware mechanism should compensate).

---

## 7. Verdict: LIKELY TRUE (with caveats)

The conjecture "Toxiproxy latency toxic is purely additive" is **likely true**
for the specific test scenarios in this project. The reasoning:

### Evidence supporting additivity

1. **The source code is explicitly designed for additivity.** The
   timestamp-aware delay calculation (`t.delay() - time.Since(c.Timestamp)`)
   subtracts time already elapsed, preventing compounding. This is the core
   engineering decision that makes the toxic additive.

2. **The 1024-chunk buffer prevents latency from throttling bandwidth.** Without
   this buffer, the latency toxic would create backpressure that slows upstream
   reads, potentially causing non-linear effects. The large buffer decouples
   latency from throughput.

3. **Single-chunk HTTP responses are the common case.** For the small JSON API
   responses used in this project (<32KB), the response typically arrives as a
   single `StreamChunk` on localhost. The delay is applied exactly once.

4. **No known bugs or reports of non-additive behavior.** The issue tracker
   and community resources show no evidence of latency compounding or absorption
   in single-toxic configurations.

5. **The jitter distribution is well-defined.** Uniform distribution over
   `[latency-jitter, latency+jitter)` is simple and predictable.

### Caveats that could cause apparent non-additivity

1. **Multi-chunk responses over WAN**: If the upstream response arrives in
   multiple TCP segments with significant inter-segment delay, the latency
   toxic delays the first chunk by the full amount but may pass later chunks
   through faster (partial absorption). This makes observed latency *less than*
   injected, not more. The delta would be smaller than expected.

2. **First-request cold-start**: Without warmup, the first request includes TCP
   handshake and possible DNS resolution overhead, inflating the baseline. This
   makes the delta appear *smaller* than expected.

3. **Connection pooling effects**: If the test creates new TCP connections for
   each request (no session reuse), each request includes handshake overhead.
   This adds constant noise to both baseline and degraded measurements, making
   the delta noisier but still centered on the injected value.

4. **Extreme system load**: Under heavy CPU load, Go goroutine scheduling delays
   could add unpredictable latency. This would increase variance but not
   systematically bias the delta.

5. **Multiple toxic interaction**: If other toxics (bandwidth, slicer) are
   active simultaneously, the latency toxic's timestamp-aware mechanism can
   cause absorption (not compounding). The conjecture should be tested with
   latency as the *only* active toxic.

### Bottom Line

For a properly designed test (warmup, session reuse, local upstream, single
latency toxic, jitter=0 initially), >95% of delta measurements should fall
within `[injected - 50ms, injected + 50ms]`. The conjecture is expected to
pass falsification.

The conjecture is weakest when:
- Responses span multiple `StreamChunk`s (large responses over WAN)
- Multiple toxics are active (absorption can occur)
- The `toxicity` parameter is below 1.0 (bimodal behavior)

These edge cases are worth testing but are not the primary scenario described
in the conjecture.

---

## Sources

- [Toxiproxy latency.go source](https://github.com/Shopify/toxiproxy/blob/main/toxics/latency.go) -- full implementation reviewed via `gh api`
- [Toxiproxy toxic.go source](https://github.com/Shopify/toxiproxy/blob/main/toxics/toxic.go) -- ToxicStub, pipeline architecture, toxicity mechanism
- [Toxiproxy link.go source](https://github.com/Shopify/toxiproxy/blob/main/link.go) -- ToxicLink pipeline, connection lifecycle
- [Toxiproxy stream/io_chan.go source](https://github.com/Shopify/toxiproxy/blob/main/stream/io_chan.go) -- StreamChunk, ChanWriter timestamp assignment
- [Toxiproxy bandwidth.go source](https://github.com/Shopify/toxiproxy/blob/main/toxics/bandwidth.go) -- bandwidth toxic for interaction analysis
- [Toxiproxy CREATING_TOXICS.md](https://github.com/Shopify/toxiproxy/blob/main/CREATING_TOXICS.md) -- StreamChunk documentation, buffering, pipeline architecture
- [Toxiproxy README](https://github.com/Shopify/toxiproxy) -- <100us baseline latency, toxic descriptions
- [Issue #239: Percentage of requests delayed](https://github.com/Shopify/toxiproxy/issues/239) -- maintainer confirms per-buffer behavior
- [Issue #148: Race between connection and toxic creation](https://github.com/Shopify/toxiproxy/issues/148)
- [Issue #412: Toxiproxy hangs during toxic removal](https://github.com/Shopify/toxiproxy/issues/412)
- [Issue #254: API hangs after long idle](https://github.com/Shopify/toxiproxy/issues/254)
