"""Ollama fault scenarios via Toxiproxy :9002 -> localhost:11434."""
import time
import json
import pytest
import requests

PROXY_BASE = "http://localhost:9002"
MODEL = "qwen3:0.6b"   # adjust to what's pulled locally


def generate(prompt: str, stream: bool = False) -> requests.Response:
    return requests.post(
        f"{PROXY_BASE}/api/generate",
        json={"model": MODEL, "prompt": prompt, "stream": stream},
        timeout=30,
    )


def embed(text: str) -> requests.Response:
    return requests.post(
        f"{PROXY_BASE}/api/embeddings",
        json={"model": "nomic-embed-text:v1.5", "prompt": text},
        timeout=10,
    )


def test_baseline_embed_under_100ms():
    """RTB gate: embedding latency must be < 100ms without fault."""
    t0 = time.monotonic()
    r = embed("East Boston waterfront")
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert r.status_code == 200
    assert elapsed_ms < 100, f"Embedding took {elapsed_ms:.1f}ms — fails RTB SLA"


def test_latency_pushes_embed_past_rtb_gate(toxi):
    """50ms injected latency should push embed past 100ms threshold."""
    toxi.add_latency("ollama", latency_ms=100, jitter_ms=5)
    t0 = time.monotonic()
    r = embed("East Boston waterfront")
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert r.status_code == 200
    assert elapsed_ms > 100, f"Expected RTB breach, got {elapsed_ms:.1f}ms"


def test_slicer_disrupts_streaming_generation(toxi):
    """
    Slicer breaks TCP stream into 1-byte chunks with 500us delay between each.
    Client must reassemble correctly; validate response is still parseable JSON.
    """
    toxi.add_slicer("ollama", avg_size=1, delay_us=500)
    r = generate("count to three", stream=False)
    assert r.status_code == 200
    body = r.json()
    assert "response" in body
    assert len(body["response"]) > 0


def test_timeout_mid_generation_raises(toxi):
    """2000ms timeout toxic; long generation should not complete."""
    toxi.add_timeout("ollama", timeout_ms=2000)
    with pytest.raises(requests.exceptions.ConnectionError):
        generate("write me a sonnet about FreeBSD jails", stream=False)


def test_bandwidth_cap_slows_large_response(toxi):
    """10 KB/s cap on a verbose generation; must take > 1s."""
    toxi.add_bandwidth("ollama", rate_kbps=10)
    t0 = time.monotonic()
    r = generate("list every US state capital", stream=False)
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert elapsed > 1.0, f"Expected throttled response, got {elapsed:.2f}s"
