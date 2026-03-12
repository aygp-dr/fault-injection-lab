# Conjecture C-005 Research: Bandwidth Throttle Proportionality

**Conjecture**: Bandwidth throttle effect on response time scales proportionally
with payload size (2x payload ~ 2x elapsed).

**Falsification criterion**: Compare elapsed time for 1KB vs 10KB vs 100KB
payloads at fixed 1KB/s cap; ratio should be within 20% of payload size ratio.

---

## 1. How the Bandwidth Toxic Works

Source: [`toxics/bandwidth.go`](https://github.com/Shopify/toxiproxy/blob/main/toxics/bandwidth.go)

The bandwidth toxic is **not** a token bucket or leaky bucket. It is a
**sleep-based fixed-rate limiter** that operates on application-level byte
streams (not raw TCP segments).

### Algorithm (from source)

```go
sleep += time.Duration(len(p.Data)) * time.Millisecond / time.Duration(t.Rate)
```

For each `StreamChunk` received on the input channel:

1. **Accumulate sleep**: `sleep += len(chunk_bytes) * 1ms / rate_KB_s`. At
   rate=1 (1 KB/s), each byte adds 1ms of sleep. 1024 bytes adds 1.024s.

2. **Large-chunk splitting**: If `len(p.Data) > rate * 100`, the chunk is split
   into sub-chunks of `rate * 100` bytes, each sent after a 100ms delay. This
   prevents a single enormous chunk from causing one very long sleep.

3. **Sleep correction**: After `time.After(sleep)` fires, the actual elapsed
   time is measured and the overshoot is subtracted from the next sleep:
   `sleep -= time.Since(start)`. This compensates for `time.After`'s ~1ms
   precision on most OSes.

4. **No global clock**: The sleep accumulator is per-connection, not shared
   across connections to the same proxy.

### Key insight: application-level, not TCP-level

Toxiproxy operates as a **TCP proxy** that reads bytes from the source socket
via `io.Copy` into a `ChanWriter`, which packages them into `StreamChunk`
structs and sends them through a channel pipeline. The bandwidth toxic sees
these `StreamChunk` values -- raw byte slices that `io.Copy` read from the TCP
socket -- **not** individual TCP segments.

From `link.go`:
```
io.Copy(link.input, source)   // source is a net.Conn (TCP socket)
```

Go's `io.Copy` uses a 32KB default buffer. Each `ChanWriter.Write()` call
creates a `StreamChunk` with however many bytes were read (up to 32KB). The
bandwidth toxic then throttles delivery of these chunks.

**This means the bandwidth toxic meters application-layer bytes only.** It does
not see or account for:
- TCP headers (20 bytes per segment, 40+ with options)
- IP headers (20 bytes per packet)
- TCP ACKs (40-byte packets flowing in the reverse direction)
- TCP handshake packets (SYN, SYN-ACK, ACK)
- TLS handshake bytes (if applicable)

## 2. Sources of Non-Proportionality

### 2.1 TCP Connection Setup (Constant Overhead)

Every HTTP request over Toxiproxy requires a TCP handshake between the client
and Toxiproxy, and a separate TCP handshake between Toxiproxy and the upstream:

| Phase                        | Time          | Depends on payload? |
|------------------------------|---------------|---------------------|
| Client -> Toxiproxy TCP SYN  | ~0.1ms local  | No                  |
| Toxiproxy -> Upstream TCP    | 50-200ms WAN  | No                  |
| HTTP request send            | ~1ms local    | No (small GET)      |
| Upstream processing          | Variable      | No                  |
| **Total constant overhead**  | **50-400ms**  | **No**              |

For a 1KB payload at 1KB/s, the throttled transfer takes ~1s. The connection
setup overhead of 50-400ms represents 5-40% of total time. For a 100KB payload
at 1KB/s, the throttled transfer takes ~100s, and the same overhead is only
0.05-0.4%.

**Impact**: Devastating for small payloads, negligible for large ones. This
alone breaks proportionality for the 1KB case.

### 2.2 HTTP Protocol Overhead

HTTP response bytes that are not "payload" but are metered by the bandwidth
toxic:

| Component              | Typical size | Notes                              |
|------------------------|-------------|-------------------------------------|
| Status line            | ~15 bytes   | `HTTP/1.1 200 OK\r\n`              |
| Headers                | 200-2000 bytes | Content-Type, Date, Server, etc. |
| Chunked encoding marks | ~20 bytes/chunk | `\r\nSIZE\r\n...\r\n0\r\n\r\n` |
| Compression headers    | ~30 bytes   | If gzip/br negotiated               |

For a real API like PokéAPI or GitHub, response headers are typically
300-800 bytes. This overhead is **metered by the bandwidth toxic** because it
appears in the byte stream read from the upstream socket.

At 1KB/s:
- 1KB payload + 500 bytes headers = 1.5KB total -> 1.5s (50% overhead)
- 10KB payload + 500 bytes headers = 10.5KB total -> 10.5s (5% overhead)
- 100KB payload + 500 bytes headers = 100.5KB total -> 100.5s (0.5% overhead)

**Impact**: Significant for 1KB, minor for 10KB, negligible for 100KB.

### 2.3 TCP Overhead Is NOT Metered (But Matters at Wire Level)

Since the bandwidth toxic operates on application bytes, TCP framing overhead is
invisible to it. However, if the test is measuring **wall-clock time**, the
actual wire-level bandwidth consumption matters because:

- TCP headers add ~3-5% overhead for typical MSS (1460 bytes)
- TCP ACKs consume bandwidth in the reverse direction
- Nagle's algorithm and delayed ACKs can add small delays

Since Toxiproxy throttles **only** the application bytes, TCP overhead does not
directly affect the metered rate. However, it could cause slight timing
variations due to kernel socket buffer interactions.

### 2.4 Chunked Delivery and Sleep Precision

The bandwidth toxic's sleep correction logic (`sleep -= time.Since(start)`)
compensates for timer imprecision, but:

- `time.After` has ~1ms precision (documented in the source code comments)
- For rate=1 KB/s and a 1KB chunk, sleep is ~1000ms -- the 1ms error is 0.1%
- For rate=1 KB/s and a 32KB chunk (io.Copy buffer), the chunk-splitting logic
  kicks in (32KB > 1*100=100 bytes), splitting into 100-byte sub-chunks with
  100ms sleeps
- Sleep accumulation error compounds over many small sleeps

The Toxiproxy test suite uses a tolerance of only 10ms for a 480KB transfer
at 1000 KB/s (expected ~480ms), suggesting the mechanism is quite precise for
large payloads:

```go
AssertDeltaTime(t, "Bandwidth", time.Since(start),
    time.Duration(len(writtenPayload))*time.Second/time.Duration(rate*1000),
    10*time.Millisecond)
```

### 2.5 Upstream Response Time (Constant per Request)

The upstream server's processing time is independent of payload size for a fixed
endpoint. If the upstream takes 100ms to generate any response, that 100ms
appears in all measurements equally:

- 1KB test: 100ms + 1s throttle = 1.1s
- 10KB test: 100ms + 10s throttle = 10.1s
- Ratio: 10.1/1.1 = 9.18 (expected 10.0) -- 8.2% deviation

For 1KB vs 100KB: 100.1/1.1 = 91.0 (expected 100.0) -- 9% deviation.

### 2.6 Response Compression

If the upstream serves gzip-compressed responses and the client accepts them,
the bytes on the wire (which are what the toxic sees) may be much smaller than
the logical payload size. A 100KB JSON response might compress to 15KB. This
would destroy any proportionality calculation based on logical payload size.

This is particularly relevant for the PokéAPI (which serves gzip by default)
and GitHub API (which also compresses responses).

## 3. Expected Deviation from Perfect Proportionality

### Model

Let `T(n)` = total wall-clock time for a payload of `n` KB at rate `R` KB/s:

```
T(n) = T_setup + T_upstream + (n + H) / R
```

Where:
- `T_setup` = TCP handshake + proxy overhead (constant, ~50-200ms for WAN)
- `T_upstream` = upstream processing time (constant per endpoint)
- `H` = HTTP header overhead in KB (~0.3-0.8 KB)
- `R` = throttle rate (1 KB/s in our test)

For perfect proportionality, `T(k*n) / T(n)` should equal `k`.

### Predicted Ratios at R = 1 KB/s

Assumptions: T_setup = 100ms, T_upstream = 150ms, H = 0.5KB

| Payload | T(n) predicted | Ratio to 1KB | Ideal ratio | Deviation |
|---------|---------------|--------------|-------------|-----------|
| 1 KB    | 0.25 + 1.5    = 1.75s  | 1.0   | 1.0  | 0%    |
| 10 KB   | 0.25 + 10.5   = 10.75s | 6.14  | 10.0 | -38.6% |
| 100 KB  | 0.25 + 100.5  = 100.75s| 57.6  | 100.0| -42.4% |

Wait -- the ratios are off in the wrong direction. Let me recalculate.

The ratio `T(10KB) / T(1KB)` = 10.75 / 1.75 = 6.14. The ideal is 10.0.
Deviation: (6.14 - 10.0) / 10.0 = -38.6%.

**This is far outside the 20% tolerance.** The constant overhead dominates
the 1KB case, making the 1KB measurement disproportionately long relative
to what pure bandwidth throttling would predict.

### Better comparison: using the elapsed time ratio vs payload size ratio

If we instead compare 10KB vs 100KB (avoiding the tiny-payload problem):

`T(100KB) / T(10KB)` = 100.75 / 10.75 = 9.37. Ideal is 10.0.
Deviation: (9.37 - 10.0) / 10.0 = -6.3%. **Within 20%.**

### What if we use a local upstream (minimal setup time)?

Assumptions: T_setup = 5ms, T_upstream = 5ms, H = 0.3KB

| Payload | T(n) predicted | Ratio to 1KB | Ideal ratio | Deviation |
|---------|---------------|--------------|-------------|-----------|
| 1 KB    | 0.01 + 1.3    = 1.31s  | 1.0    | 1.0  | 0%    |
| 10 KB   | 0.01 + 10.3   = 10.31s | 7.87   | 10.0 | -21.3% |
| 100 KB  | 0.01 + 100.3  = 100.31s| 76.6   | 100.0| -23.4% |

Still outside 20% for 1KB baseline, even with minimal overhead, because
HTTP headers add ~30% to a 1KB payload.

## 4. Known Non-Linear Behavior

No specific Toxiproxy issues report non-linear bandwidth behavior. The mechanism
is mathematically linear in application bytes: `sleep = bytes / rate`. The
non-linearity arises entirely from:

1. **Constant additive overheads** (connection setup, upstream processing)
2. **Per-request protocol overhead** (HTTP headers) that scales sub-linearly
   with payload size
3. **Timer precision** (~1ms, irrelevant at 1KB/s rates)
4. **Response compression** (if active, makes wire bytes non-proportional to
   logical payload size)

The Toxiproxy test suite only tests bandwidth with a single large payload
(480KB at 1000KB/s) and does not test proportionality across payload sizes.

## 5. Recommended Test Design

### 5.1 Control for confounding variables

1. **Use a local upstream** (Prism mock or a simple Python HTTP server) to
   minimize T_setup and T_upstream variance.

2. **Disable response compression** by not sending `Accept-Encoding: gzip` or
   using an upstream that does not compress.

3. **Measure payload bytes on the wire**, not logical payload size. Use
   `len(response.content)` in Python to get the actual bytes received.

4. **Measure HTTP header size separately** by inspecting
   `len(response.raw.headers)` or similar, to compute the total bytes the
   toxic actually metered.

5. **Subtract constant overhead**: Run each endpoint once without the bandwidth
   toxic to measure baseline time. Then:
   ```
   throttled_transfer_time = total_time - baseline_time
   ```

### 5.2 Adjusted proportionality test

Instead of comparing raw elapsed times, compare **throttled transfer times**
after subtracting baseline:

```python
ratio = (T_throttled_100KB - T_baseline) / (T_throttled_10KB - T_baseline)
# Should be close to total_bytes_100KB / total_bytes_10KB
# (where total_bytes includes HTTP headers)
```

### 5.3 Payload size selection

- Avoid 1KB payloads. At 1KB/s, HTTP headers (~300-800 bytes) represent
  30-80% of the total bytes. The "payload ratio" test becomes a test of
  header overhead, not bandwidth proportionality.
- Use 10KB, 50KB, 100KB as test points. Header overhead drops to 3-8% for
  10KB and <1% for 100KB.
- If 1KB must be included, adjust the expected ratio to account for header
  bytes: `expected_ratio = (payload + headers) / (small_payload + headers)`.

### 5.4 Practical test outline

```python
# Pseudo-code for adjusted proportionality test
RATE = 1  # KB/s
SIZES = [10_000, 50_000, 100_000]  # payload bytes (use endpoint that returns these sizes)

# Step 1: Measure baseline (no toxic) for each endpoint
baselines = {}
for size in SIZES:
    baselines[size] = measure_time(endpoint_for_size(size))

# Step 2: Add bandwidth toxic at RATE KB/s
toxi.add_bandwidth("proxy", rate_kbps=RATE)

# Step 3: Measure throttled time
throttled = {}
for size in SIZES:
    throttled[size] = measure_time(endpoint_for_size(size))

# Step 4: Compute transfer times and ratios
for size in SIZES:
    transfer_time[size] = throttled[size] - baselines[size]

# Step 5: Check proportionality (use total wire bytes, not payload)
for i in range(1, len(SIZES)):
    ratio = transfer_time[SIZES[i]] / transfer_time[SIZES[0]]
    expected = wire_bytes[SIZES[i]] / wire_bytes[SIZES[0]]
    assert abs(ratio - expected) / expected < 0.20
```

## 6. Verdict

**Likely FALSE as stated.** The conjecture will fail the 20% tolerance test
for the specified payload sizes (1KB, 10KB, 100KB) because:

1. **The 1KB payload is the critical failure point.** HTTP response headers
   (300-800 bytes) represent 30-80% of the bytes the toxic actually meters for
   a 1KB payload, but only 3-8% for 10KB and <1% for 100KB. The elapsed time
   for 1KB will be 1.3-1.8x what pure payload-proportional would predict.

2. **Connection setup and upstream processing time are constant**, adding a
   fixed offset that disproportionately affects small payloads. Even with a
   local upstream (5-10ms overhead), the 1KB vs 10KB ratio will deviate by
   ~20% from ideal.

3. **The bandwidth toxic itself is mathematically linear** in application
   bytes. The non-proportionality is not a Toxiproxy bug -- it is an inherent
   property of HTTP-over-TCP: small payloads have proportionally more overhead.

### Conditions under which C-005 could pass

- If the test uses **adjusted transfer times** (subtracting baseline) and
  **total wire bytes** (including headers) instead of raw elapsed times and
  logical payload sizes.
- If the 1KB test point is excluded, and only 10KB/100KB are compared.
- If the upstream is local and response compression is disabled.

### Recommendation

Refine the conjecture to test what the bandwidth toxic actually controls:
**application-byte throughput is capped at the configured rate, independent of
payload size.** The test should verify that `actual_throughput = wire_bytes /
transfer_time` is within 20% of the configured rate for all payload sizes,
rather than testing proportionality of elapsed times.
