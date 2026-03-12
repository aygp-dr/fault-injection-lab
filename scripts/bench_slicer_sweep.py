#!/usr/bin/env python3
"""Experiment 3: Slicer chunk size sweep on generation endpoint.
Tests C-003 (JSON validity)."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statistics

from bench_common import (
    toxi, timed_generate, warmup_generate, save_json,
    ensure_results_dir, RESULTS_DIR,
)
import os

SIZES = [1, 2, 4, 8, 16, 32, 64, 1024, None]  # None = no toxic (baseline)
N = 3
PROMPT = "count to five"


def collect():
    warmup_generate()
    results = {}
    for size in SIZES:
        label = f"{size}B" if size is not None else "baseline"
        toxi.reset_all()
        if size is not None:
            toxi.add_slicer("ollama", avg_size=size, delay_us=100)
        # warmup
        timed_generate(PROMPT)
        timings = []
        json_ok = []
        for i in range(N):
            elapsed, r = timed_generate(PROMPT)
            ok = False
            try:
                body = r.json()
                ok = r.status_code == 200 and "response" in body and len(body["response"]) > 0
            except Exception:
                ok = False
            timings.append(elapsed)
            json_ok.append(ok)
            print(f"  slicer={label}  sample={i+1}/{N}  elapsed={elapsed:.2f}s  json_valid={ok}")
        results[label] = {"timings": timings, "json_ok": json_ok}
    toxi.reset_all()
    return results


def plot(results):
    ensure_results_dir()
    sizes = [k for k in results.keys() if k != "baseline"]
    size_nums = [int(k.replace("B", "")) for k in sizes]
    baseline_time = statistics.mean(results["baseline"]["timings"])

    mean_times = [statistics.mean(results[k]["timings"]) for k in sizes]
    validity = [sum(results[k]["json_ok"]) / len(results[k]["json_ok"]) * 100 for k in sizes]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), height_ratios=[2, 1])

    # Top: response time
    ax1.plot(size_nums, mean_times, "o-", color="#2196F3", linewidth=2, markersize=8)
    ax1.axhline(y=baseline_time, color="#4CAF50", linestyle="--", linewidth=1, label=f"Baseline ({baseline_time:.1f}s)")
    ax1.set_xscale("log", base=2)
    ax1.set_xlabel("Slicer avg_size (bytes)", fontsize=12)
    ax1.set_ylabel("Mean Response Time (s)", fontsize=12)
    ax1.set_title("Response Time vs Slicer Chunk Size", fontsize=13)
    ax1.set_xticks(size_nums)
    ax1.set_xticklabels([str(s) for s in size_nums])
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Bottom: JSON validity
    colors = ["#4CAF50" if v == 100 else "#F44336" for v in validity]
    ax2.bar(range(len(sizes)), validity, color=colors, alpha=0.8)
    ax2.set_xticks(range(len(sizes)))
    ax2.set_xticklabels([str(s) for s in size_nums])
    ax2.set_xlabel("Slicer avg_size (bytes)", fontsize=12)
    ax2.set_ylabel("JSON Validity (%)", fontsize=12)
    ax2.set_title("JSON Parse Success Rate (C-003)", fontsize=13)
    ax2.set_ylim(0, 110)
    ax2.axhline(y=100, color="#4CAF50", linestyle="--", linewidth=1, alpha=0.5)
    ax2.grid(True, alpha=0.3, axis="y")

    fig.suptitle("Slicer Impact on Ollama Generation (qwen3:0.6b, delay_us=100)", fontsize=15)
    path = os.path.join(RESULTS_DIR, "slicer_sweep.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    print(f"Saved {path}")
    plt.close()


if __name__ == "__main__":
    data = collect()
    save_json(data, "slicer_sweep.json")
    plot(data)
