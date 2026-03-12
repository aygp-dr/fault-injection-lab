# Conjecture C-003 Research: TCP Slicer Toxic Preserves JSON Validity

**Conjecture**: TCP slicer toxic preserves JSON validity after reassembly at 1-byte average chunk size.

**Falsification criterion**: Send 50 requests through slicer(avg_size=1); all must parse as valid JSON with identical schema.

**Date**: 2026-03-11

---

## 1. How the Slicer Toxic Works Internally

### Source Code Analysis (toxics/slicer.go)

The slicer toxic has been verified by reading the actual Go source code from the Shopify/toxiproxy repository. The implementation is straightforward and critically important for this conjecture:

**Struct fields:**
- `AverageSize int` -- average number of bytes to slice at
- `SizeVariation int` -- +/- bytes to vary sliced amounts (must be less than AverageSize)
- `Delay int` -- microseconds to delay each packet

**The `chunk()` algorithm** uses recursive bisection to produce a list of offset pairs. For example, for a 100-byte packet it might produce `[]int{0, 18, 18, 43, 43, 67, 67, 77, 77, 100}`, yielding five chunks. It randomizes split points using `SizeVariation` to avoid predictable chunk boundaries.

**The `Pipe()` method** is the critical path:

1. Reads a `StreamChunk` from `stub.Input` (this is a `byte[]` plus a timestamp).
2. Computes chunk offsets via `t.chunk(0, len(c.Data))`.
3. For each chunk pair `[i-1, i]`, creates a new `StreamChunk` with `Data: c.Data[chunks[i-1]:chunks[i]]` and sends it to `stub.Output`.
4. Between chunks, sleeps for `t.Delay` microseconds (interruptible).
5. On interrupt, flushes the remaining undelivered data (`c.Data[chunks[i]:]`) to output before returning.

### Key Finding: No Data Modification

**The slicer never modifies, drops, reorders, or duplicates any bytes.** It takes the original `c.Data` byte slice and creates sub-slices pointing into the same underlying array. The data content is passed through verbatim -- only the timing and segmentation of delivery changes. This is confirmed by the Toxiproxy documentation (CREATING_TOXICS.md), which explicitly states:

> "It is important that all data read from `stub.Input` is passed along to `stub.Output`, otherwise the stream will be missing bytes and become corrupted."

The slicer implementation follows this rule. Even during interrupt handling, it flushes remaining bytes before returning.

### StreamChunk Pipeline

Toxiproxy's architecture passes data through a chain of `ToxicStub` instances connected by Go channels. Each `StreamChunk` contains a `byte[]` of stream data and a timestamp. The slicer sits in this pipeline and produces multiple smaller `StreamChunk`s from each input chunk, but the concatenation of all output chunks is byte-identical to the concatenation of all input chunks.

---

## 2. TCP Reassembly Guarantees

### TCP Fundamentals

TCP (RFC 793) provides a **reliable, in-order byte stream**. Its guarantees are:

- **Reliability**: Every byte sent is either delivered or the connection is terminated with an error. Corrupted segments are detected via checksums and retransmitted.
- **Ordering**: Bytes are delivered to the application in the exact order they were sent, regardless of how IP packets are routed, reordered, or retransmitted at the network layer.
- **No duplication**: Sequence numbers prevent the same data from being delivered twice.
- **No corruption**: TCP checksums detect bit errors; corrupted segments are discarded and retransmitted.

### Through a Proxy

Toxiproxy creates two independent TCP connections: client-to-proxy and proxy-to-upstream. Each connection independently provides the above guarantees. The slicer toxic operates between these two connections at the application layer -- it reads from one TCP stream and writes to the other. Since TCP guarantees in-order delivery on each connection, and the slicer writes bytes in the same order it reads them, the end-to-end byte stream is preserved.

**There is no scenario where TCP reassembly reorders data through a Toxiproxy slicer.** The proxy reads application-layer data (already reassembled by the kernel's TCP stack), slices it, and writes the slices to another TCP connection. The receiving kernel's TCP stack on the client side will deliver those bytes in order.

### Edge Case: Proxy-Mediated Connections

Unlike a transparent network-layer proxy, Toxiproxy terminates TCP at both ends. This means:
- The client's TCP stack talks to Toxiproxy, not the upstream server.
- Toxiproxy's TCP stack talks to the upstream server.
- Data integrity depends on Toxiproxy's application-level forwarding, not on TCP sequence number continuity across the proxy.

This is actually *more* reliable than a transparent proxy for data integrity, because each TCP segment is fully reassembled before the slicer processes it.

---

## 3. HTTP Client Library Considerations

### Python `requests` Library

The project uses Python's `requests` library (built on `urllib3`, built on `http.client`). Several considerations are relevant:

#### 3a. Normal (Non-Streaming) Requests

When `stream=False` (the default, and what the test code uses), `requests` reads the entire response body before returning. The `response.json()` method decodes the complete body. Under the hood:
1. `urllib3` reads from the socket until it has received the number of bytes specified by `Content-Length`, or until the chunked transfer encoding terminator is received.
2. TCP fragmentation is invisible at this layer -- `socket.recv()` returns whatever bytes are available, and `urllib3` loops until the response is complete.

#### 3b. The Incomplete Read Problem

A well-documented issue (Petr Zemek, 2018) shows that `requests` 2.x **silently accepts incomplete responses** when a server closes the connection before sending all bytes promised by `Content-Length`. The library does not raise an exception -- it returns truncated data. However, this occurs when **the connection is closed prematurely**, not when data arrives slowly in small fragments.

The slicer toxic does *not* close the connection prematurely. It delivers all bytes, just in small pieces with optional delays. As long as the connection remains open and all bytes are delivered, `requests` will read the complete response.

#### 3c. ChunkedEncodingError

If the TCP connection is reset or closed mid-transfer, `requests` raises `ChunkedEncodingError` or `ConnectionError`. This would manifest as an exception, not as silently corrupted JSON. The test would see an exception rather than a successful `response.json()` call returning wrong data.

#### 3d. Timeout Interaction

The `requests` `timeout` parameter controls:
- **Connect timeout**: time to establish the TCP connection.
- **Read timeout**: time to wait for the *next chunk of data from the socket*, not total response time.

With slicer(avg_size=1, delay_us=500), a 10KB response body would require approximately 10,000 chunks with 500us between each, totaling about 5 seconds of additional delay. The test code in spec.org uses `timeout=10` or `timeout=30`, which should be sufficient -- but only if the read timeout is not exceeded between individual chunks. Since `delay_us=500` means 0.5ms between chunks, and the default socket-level read timeout in `requests` is the value passed to `timeout=`, individual chunks will arrive well within even a 1-second read timeout.

**Risk**: If `delay_us` were extremely large (e.g., 30,000,000 for 30 seconds), it could exceed the read timeout, causing a `ReadTimeoutError`. But at `delay_us=500` (0.5ms), this is not a concern.

---

## 4. HTTP Framing Interactions

### 4a. HTTP Headers vs Body

The slicer operates at the TCP level, below HTTP. It does not distinguish between HTTP headers and body -- it slices the entire TCP stream including HTTP framing bytes (`HTTP/1.1 200 OK\r\n...`).

However, this does not cause problems because:
- The HTTP parser in `http.client` (Python's stdlib) reads from a buffered socket. It calls `readline()` for headers and `read(n)` for the body.
- These calls block until the requested data is available.
- TCP delivers bytes in order, so the HTTP parser sees a valid byte stream regardless of how it was segmented at the TCP level.

### 4b. Chunked Transfer Encoding

Some HTTP servers use `Transfer-Encoding: chunked` instead of `Content-Length`. With chunked encoding, the body is sent as a series of chunks, each prefixed with its hex length. The slicer could theoretically split a chunk length prefix across two TCP segments:

```
Original: "1a\r\n" + [26 bytes of data] + "\r\n"
Sliced:   "1" | "a\r" | "\n" | [1 byte] | [1 byte] | ...
```

This is not a problem because the HTTP chunked decoder reads the stream byte-by-byte until it sees `\r\n`, then reads the specified number of bytes. TCP delivers all bytes in order, so the decoder will correctly parse the chunked encoding regardless of TCP segmentation.

### 4c. Header Parsing Edge Case

Similarly, HTTP header parsing reads until `\r\n\r\n` (the empty line separating headers from body). Even if the slicer splits this sequence across multiple TCP segments, the buffered reader will accumulate bytes until the complete header terminator is found.

---

## 5. Known Issues

### 5a. Slicer Panic (Issue #178)

A panic was reported in Toxiproxy 2.1.1 where the slicer toxic caused "Source terminated" warnings with "use of closed network connection" errors. This was a crash bug, not a data corruption bug. When it occurred, the connection would fail entirely (raising an exception on the client), not produce corrupted data.

### 5b. Toxic Removal Hanging (Issue #412)

Toxiproxy can hang when removing toxics during active traffic. This is a control-plane issue, not a data-plane issue. It would not cause data corruption.

### 5c. Toxicity=0 Residual Effect (Issue #603)

A toxic with toxicity set to 0 can still affect the proxy. This is not relevant to the slicer with default toxicity (1.0).

### 5d. No Known Data Corruption Issues

After searching Toxiproxy's issue tracker and general web resources, **no reports of the slicer toxic causing data corruption were found**. All reported slicer issues relate to panics (crashes) or performance, not to data being modified in transit.

---

## 6. Edge Cases and Risks

### 6a. Very Large Responses

For a very large JSON response (e.g., PokéAPI's `/api/v2/pokemon?limit=100` which can be 100KB+), slicing into 1-byte chunks creates approximately 100,000 chunks. Each chunk is a separate Go channel send/receive and a separate `time.After` call. This creates significant CPU and memory overhead in Toxiproxy but does not affect data integrity.

**Risk**: Toxiproxy could run out of memory or goroutine resources under extreme load. This would cause a connection failure (exception), not silent data corruption.

### 6b. Interrupt During Slicing

If a toxic is updated or removed while the slicer is mid-delivery, the interrupt handler in `Pipe()` flushes remaining bytes:

```go
case <-stub.Interrupt:
    stub.Output <- &stream.StreamChunk{
        Data:      c.Data[chunks[i]:],
        Timestamp: c.Timestamp,
    }
    return
```

This ensures no data is lost during interrupt. The remaining undelivered bytes are sent as a single chunk.

### 6c. Concurrent Requests

Each connection through Toxiproxy gets its own pair of goroutines and its own toxic pipeline. Concurrent requests do not interfere with each other's data streams. The slicer's `Pipe()` function is called once per connection instance.

### 6d. delay_us and Client Timeouts

With `avg_size=1` and `delay_us=500` (as used in spec.org's Ollama test), the per-byte delay is 0.5ms. For a typical JSON API response of 1-10KB:
- 1KB response: ~500ms additional delay (1000 chunks x 0.5ms)
- 10KB response: ~5s additional delay (10000 chunks x 0.5ms)
- 100KB response: ~50s additional delay (100000 chunks x 0.5ms)

With `delay_us=0` (as used in the PokéAPI test and the C-003 falsification test), there is no artificial delay -- only the natural overhead of Go channel operations, which is on the order of nanoseconds per chunk.

**For the C-003 test specifically** (`avg_size=1, delay_us=0`), even a 100KB response should complete in well under a second of additional overhead. Timeouts are not a concern.

### 6e. size_variation=0 with avg_size=1

When `size_variation=0` (as configured in `add_slicer`), the `chunk()` function's base case `(end-start)-t.AverageSize <= t.SizeVariation` simplifies to `(end-start)-1 <= 0`, meaning it returns immediately for any chunk of size 1 or less. This means every byte is delivered individually -- truly 1-byte chunks with no randomization. This is the most extreme case and is fully deterministic.

---

## 7. Verdict: LIKELY TRUE

The conjecture "TCP slicer toxic preserves JSON validity after reassembly at 1-byte average chunk size" is **likely true**. The reasoning:

1. **The slicer does not modify data.** Source code confirms it only sub-slices the original byte array. No bytes are added, removed, reordered, or altered.

2. **TCP guarantees in-order, reliable delivery.** Both the client-to-proxy and proxy-to-upstream connections are independent TCP streams with full reassembly guarantees.

3. **The HTTP client (`requests`) handles TCP fragmentation correctly.** It reads from buffered sockets and loops until the complete response is received. Fragmentation is invisible at the HTTP layer.

4. **No known bugs cause data corruption.** Known slicer issues are crashes or hangs, not data modification. Both failure modes would produce exceptions, not invalid JSON.

5. **HTTP framing (headers, chunked encoding) is resilient to TCP segmentation.** The HTTP parser reads a byte stream, not discrete packets, so arbitrary segmentation boundaries are handled correctly.

### Conditions for Falsification

The conjecture could be falsified if:

- **Client timeout is too short**: If `delay_us` causes the total response time to exceed the client's read timeout, the request fails with `ReadTimeoutError`. This is an exception, not corrupted JSON, so the falsification test should count this as a failure mode distinct from data corruption. With `delay_us=0`, this is not a risk.
- **Toxiproxy crashes (Issue #178 regression)**: A panic in the slicer would close the connection, causing `ConnectionError` in the client. Again, this is an exception, not corrupted JSON.
- **Memory exhaustion**: Under extreme load, Toxiproxy could OOM. This would kill the proxy process entirely.

None of these produce *invalid JSON that parses successfully with wrong data*. They all produce exceptions. Therefore, the falsification test as stated (50 requests must parse as valid JSON with identical schema) should pass, assuming:
- The client timeout is generous enough (10s+ with `delay_us=0`)
- Toxiproxy is running a recent, stable version
- No concurrent toxic removal operations during the test

### Recommended Instrumentation

To validate the conjecture empirically:

```python
import requests
import json

def test_c003_slicer_preserves_json(toxi):
    """C-003: 50 requests through slicer(avg_size=1) must all parse as valid JSON."""
    toxi.add_slicer("pokeapi", avg_size=1, delay_us=0)

    baseline = requests.get("http://localhost:9003/api/v2/pokemon/pikachu", timeout=30).json()
    baseline_keys = sorted(baseline.keys())

    failures = []
    for i in range(50):
        try:
            r = requests.get("http://localhost:9003/api/v2/pokemon/pikachu", timeout=30)
            assert r.status_code == 200
            data = r.json()  # Raises ValueError if invalid JSON
            assert sorted(data.keys()) == baseline_keys, f"Schema mismatch on request {i}"
        except Exception as e:
            failures.append((i, str(e)))

    assert len(failures) == 0, f"Failures: {failures}"
```

This test would:
1. Establish a baseline schema from a clean (or sliced) request
2. Send 50 requests through the slicer
3. Verify each response is valid JSON with the same top-level schema
4. Report any exceptions (timeout, connection error) or schema mismatches

---

## Sources

- [Toxiproxy slicer.go source](https://github.com/Shopify/toxiproxy/blob/master/toxics/slicer.go)
- [Toxiproxy CREATING_TOXICS.md](https://github.com/Shopify/toxiproxy/blob/main/CREATING_TOXICS.md)
- [Toxiproxy README](https://github.com/Shopify/toxiproxy)
- [Slicer panic - Issue #178](https://github.com/Shopify/toxiproxy/issues/178)
- [Toxic removal hanging - Issue #412](https://github.com/Shopify/toxiproxy/issues/412)
- [Toxicity=0 residual effect - Issue #603](https://github.com/Shopify/toxiproxy/issues/603)
- [Incomplete HTTP Reads in Python requests (Petr Zemek)](https://blog.petrzemek.net/2018/04/22/on-incomplete-http-reads-and-the-requests-library-in-python/)
- [requests incomplete read issue #6512](https://github.com/psf/requests/issues/6512)
- [TCP Reliable Byte Stream (Systems Approach)](https://book.systemsapproach.org/e2e/tcp.html)
- [Toxiproxy DeepWiki](https://deepwiki.com/Shopify/toxiproxy/1-introduction-to-toxiproxy)
