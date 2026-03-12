"""GitHub API fault scenarios via Toxiproxy."""
import time
import pytest
import requests

PROXY_BASE = "http://localhost:9001"


def gh(path: str) -> requests.Response:
    return requests.get(
        f"{PROXY_BASE}{path}",
        headers={"Accept": "application/vnd.github+json"},
        timeout=10,
    )


def test_baseline_repo_fetch():
    """C-01 equivalent: clean path, expect 200."""
    r = gh("/repos/octocat/Hello-World")
    assert r.status_code == 200
    assert r.json()["full_name"] == "octocat/Hello-World"


def test_latency_degrades_response_time(toxi):
    """Inject 300ms latency; confirm wall-clock shift."""
    t0 = time.monotonic()
    gh("/repos/octocat/Hello-World")
    baseline = time.monotonic() - t0

    toxi.add_latency("github", latency_ms=300, jitter_ms=20)

    t1 = time.monotonic()
    r = gh("/repos/octocat/Hello-World")
    degraded = time.monotonic() - t1

    assert r.status_code == 200
    delta_ms = (degraded - baseline) * 1000
    assert 250 < delta_ms < 400, f"Expected ~300ms delta, got {delta_ms:.0f}ms"


def test_bandwidth_throttle_slows_large_response(toxi):
    """1 KB/s cap; list endpoint should take >2s."""
    toxi.add_bandwidth("github", rate_kbps=1)
    t0 = time.monotonic()
    r = gh("/repos/octocat/Hello-World/commits")
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert elapsed > 2.0, f"Expected throttled response, got {elapsed:.2f}s"


def test_timeout_raises(toxi):
    """500ms timeout toxic; client should get a connection error."""
    toxi.add_timeout("github", timeout_ms=500)
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"{PROXY_BASE}/repos/octocat/Hello-World", timeout=2)


def test_connection_down_raises(toxi):
    """Disable proxy entirely; all requests fail."""
    toxi.disable("github")
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"{PROXY_BASE}/repos/octocat/Hello-World", timeout=2)
