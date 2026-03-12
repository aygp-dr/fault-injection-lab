#!/usr/bin/env python3
"""Experiment 4: Embedding RTB SLA gate with escalating latency.
Tests C-001 (additivity at scale) and C-002 (RTB SLA at p99)."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statistics

from bench_common import (
    toxi, timed_embed, warmup_embed, save_json,
    ensure_results_dir, percentile, RESULTS_DIR,
)
import os

LEVELS = [0, 10, 25, 50, 75, 100]
N = 50
SLA_MS = 100


def collect():
    warmup_embed(n=5)
    results = {}
    for ms in LEVELS:
        label = f"{ms}ms"
        toxi.reset_all()
        if ms > 0:
            toxi.add_latency("ollama", latency_ms=ms)
        # warmup after toxic change
        timed_embed()
        timings = []
        for i in range(N):
            elapsed, r = timed_embed()
            assert r.status_code == 200, f"Failed at {label}: {r.status_code}"
            timings.append(elapsed)
            if (i + 1) % 10 == 0:
                print(f"  injected={label}  progress={i+1}/{N}  latest={elapsed:.1f}ms")
        results[ms] = timings
    toxi.reset_all()
    return results


def plot(results):
    ensure_results_dir()
    levels = sorted(results.keys())
    labels = [f"+{ms}ms" if ms > 0 else "baseline" for ms in levels]

    p50s = [percentile(results[l], 50) for l in levels]
    p95s = [percentile(results[l], 95) for l in levels]
    p99s = [percentile(results[l], 99) for l in levels]
    sla_pass = [sum(1 for t in results[l] if t < SLA_MS) / len(results[l]) * 100 for l in levels]

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 12))

    # Top: box plot
    box_data = [results[l] for l in levels]
    bp = ax1.boxplot(box_data, labels=labels, patch_artist=True, widths=0.6)
    colors_box = ["#4CAF50" if percentile(results[l], 99) < SLA_MS else "#FF9800"
                  if percentile(results[l], 50) < SLA_MS else "#F44336" for l in levels]
    for patch, color in zip(bp["boxes"], colors_box):
        patch.set_facecolor(color)
        patch.set_alpha(0.6)
    ax1.axhline(y=SLA_MS, color="#F44336", linestyle="--", linewidth=1.5, label="SLA (100ms)")
    ax1.set_ylabel("Latency (ms)", fontsize=12)
    ax1.set_title("Embedding Latency Distribution by Injection Level", fontsize=13)
    ax1.legend()
    ax1.grid(True, alpha=0.3, axis="y")

    # Middle: SLA pass rate
    colors_bar = ["#4CAF50" if p >= 95 else "#FF9800" if p >= 50 else "#F44336" for p in sla_pass]
    bars = ax2.bar(range(len(levels)), sla_pass, color=colors_bar, alpha=0.8)
    ax2.set_xticks(range(len(levels)))
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("Pass Rate (%)", fontsize=12)
    ax2.set_title(f"RTB SLA Pass Rate (< {SLA_MS}ms)", fontsize=13)
    ax2.set_ylim(0, 110)
    ax2.axhline(y=95, color="#FF9800", linestyle="--", linewidth=1, alpha=0.5, label="95% threshold")
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis="y")
    for bar, pct in zip(bars, sla_pass):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                 f"{pct:.0f}%", ha="center", fontsize=10, fontweight="bold")

    # Bottom: percentile lines
    ax3.plot(levels, p50s, "o-", color="#2196F3", linewidth=2, label="p50", markersize=7)
    ax3.plot(levels, p95s, "s-", color="#FF9800", linewidth=2, label="p95", markersize=7)
    ax3.plot(levels, p99s, "^-", color="#F44336", linewidth=2, label="p99", markersize=7)
    ax3.axhline(y=SLA_MS, color="#F44336", linestyle="--", linewidth=1, alpha=0.5)
    ax3.set_xlabel("Injected Latency (ms)", fontsize=12)
    ax3.set_ylabel("Observed Latency (ms)", fontsize=12)
    ax3.set_title("Latency Percentiles by Injection Level", fontsize=13)
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    fig.suptitle("Embedding RTB SLA Gate: nomic-embed-text:v1.5 via Toxiproxy",
                 fontsize=15, y=1.01)
    path = os.path.join(RESULTS_DIR, "embed_sla_gate.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    print(f"Saved {path}")
    plt.close()


if __name__ == "__main__":
    data = collect()
    save_json({str(k): v for k, v in data.items()}, "embed_sla_gate.json")
    plot(data)
