#!/usr/bin/env python3
"""Experiment 1: Latency injection sweep on embedding endpoint.
Tests C-001 (additivity) and C-002 (RTB SLA)."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statistics

from bench_common import (
    toxi, timed_embed, warmup_embed, save_json,
    ensure_results_dir, percentile, RESULTS_DIR,
)
import os

LEVELS = [0, 50, 100, 200, 500]
N = 20
PROMPT = "East Boston waterfront"


def collect():
    warmup_embed()
    results = {}
    for ms in LEVELS:
        toxi.reset_all()
        if ms > 0:
            toxi.add_latency("ollama", latency_ms=ms)
        # warmup after toxic change
        timed_embed(PROMPT)
        timings = []
        for i in range(N):
            elapsed, r = timed_embed(PROMPT)
            assert r.status_code == 200, f"Failed at {ms}ms: {r.status_code}"
            timings.append(elapsed)
            print(f"  injected={ms}ms  sample={i+1}/{N}  observed={elapsed:.1f}ms")
        results[ms] = timings
    toxi.reset_all()
    return results


def plot(results):
    ensure_results_dir()
    levels = sorted(results.keys())
    means = [statistics.mean(results[l]) for l in levels]
    p95s = [percentile(results[l], 95) for l in levels]
    baseline = means[0]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(levels, means, "o-", color="#2196F3", linewidth=2, label="Mean observed", markersize=8)
    ax.plot(levels, p95s, "s--", color="#FF9800", linewidth=1.5, label="p95 observed", markersize=6)
    ax.plot(levels, [l + baseline for l in levels], ":", color="#4CAF50", linewidth=1.5, label=f"Perfect additivity (baseline={baseline:.0f}ms)")
    ax.axhline(y=100, color="#F44336", linestyle="--", linewidth=1, alpha=0.7, label="RTB SLA (100ms)")

    ax.set_xlabel("Injected Latency (ms)", fontsize=12)
    ax.set_ylabel("Observed Latency (ms)", fontsize=12)
    ax.set_title("Ollama Embedding Latency vs Injected Latency\n(nomic-embed-text:v1.5 via Toxiproxy)", fontsize=14)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    for i, ms in enumerate(levels):
        delta = means[i] - baseline
        ax.annotate(f"{means[i]:.0f}ms\n(+{delta:.0f})",
                     (ms, means[i]), textcoords="offset points",
                     xytext=(0, 15), ha="center", fontsize=8)

    path = os.path.join(RESULTS_DIR, "latency_sweep.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    print(f"Saved {path}")
    plt.close()


if __name__ == "__main__":
    data = collect()
    save_json({str(k): v for k, v in data.items()}, "latency_sweep.json")
    plot(data)
