#!/usr/bin/env python3
"""Experiment 2: Bandwidth throttle sweep on generation endpoint.
Tests C-005 (proportionality)."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statistics

from bench_common import (
    toxi, timed_generate, warmup_generate, save_json,
    ensure_results_dir, RESULTS_DIR,
)
import os

RATES = [1, 5, 10, 50, 0]  # 0 = unlimited
N = 3
PROMPT = "list the first ten prime numbers"


def collect():
    warmup_generate()
    results = {}
    for rate in RATES:
        label = f"{rate}KB/s" if rate > 0 else "unlimited"
        toxi.reset_all()
        if rate > 0:
            toxi.add_bandwidth("ollama", rate_kbps=rate)
        # warmup
        timed_generate(PROMPT)
        timings = []
        sizes = []
        for i in range(N):
            elapsed, r = timed_generate(PROMPT)
            assert r.status_code == 200, f"Failed at {label}: {r.status_code}"
            body_size = len(r.content)
            timings.append(elapsed)
            sizes.append(body_size)
            print(f"  rate={label}  sample={i+1}/{N}  elapsed={elapsed:.2f}s  body={body_size}B")
        results[rate] = {"timings": timings, "sizes": sizes}
    toxi.reset_all()
    return results


def plot(results):
    ensure_results_dir()
    rates = sorted([r for r in results.keys() if r > 0])
    unlimited = results[0]

    mean_times = [statistics.mean(results[r]["timings"]) for r in rates]
    mean_sizes = [statistics.mean(results[r]["sizes"]) for r in rates]
    throughputs = [sz / t / 1024 for sz, t in zip(mean_sizes, mean_times)]  # KB/s
    unlimited_time = statistics.mean(unlimited["timings"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Left: response time
    ax1.plot(rates, mean_times, "o-", color="#2196F3", linewidth=2, markersize=8)
    ax1.axhline(y=unlimited_time, color="#4CAF50", linestyle="--", linewidth=1, label=f"Unlimited ({unlimited_time:.1f}s)")
    ax1.set_xscale("log")
    ax1.set_xlabel("Configured Rate (KB/s)", fontsize=12)
    ax1.set_ylabel("Mean Response Time (s)", fontsize=12)
    ax1.set_title("Response Time vs Bandwidth Cap", fontsize=13)
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    for i, rate in enumerate(rates):
        ax1.annotate(f"{mean_times[i]:.1f}s", (rate, mean_times[i]),
                     textcoords="offset points", xytext=(0, 12), ha="center", fontsize=9)

    # Right: throughput
    ax2.plot(rates, throughputs, "s-", color="#FF9800", linewidth=2, markersize=8)
    ax2.plot(rates, rates, ":", color="#4CAF50", linewidth=1.5, label="Ideal (y=x)")
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("Configured Rate (KB/s)", fontsize=12)
    ax2.set_ylabel("Observed Throughput (KB/s)", fontsize=12)
    ax2.set_title("Observed vs Configured Throughput", fontsize=13)
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    fig.suptitle("Ollama Generation Under Bandwidth Throttle (qwen3:0.6b)", fontsize=15, y=1.02)
    path = os.path.join(RESULTS_DIR, "bandwidth_sweep.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"Saved {path}")
    plt.close()


if __name__ == "__main__":
    data = collect()
    save_json({str(k): v for k, v in data.items()}, "bandwidth_sweep.json")
    plot(data)
