"""Shared utilities for benchmark scripts."""
import sys
import os
import json
import time
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from fault_control.toxiproxy import ToxiproxyController

PROXY_BASE = "http://localhost:9002"
GEN_MODEL = "qwen3:0.6b"
EMBED_MODEL = "nomic-embed-text:v1.5"
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results")

toxi = ToxiproxyController()


def ensure_results_dir():
    os.makedirs(RESULTS_DIR, exist_ok=True)


def save_json(data, filename):
    ensure_results_dir()
    path = os.path.join(RESULTS_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {path}")


def ollama_embed(text, timeout=10):
    return requests.post(
        f"{PROXY_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=timeout,
    )


def ollama_generate(prompt, timeout=120):
    return requests.post(
        f"{PROXY_BASE}/api/generate",
        json={"model": GEN_MODEL, "prompt": prompt, "stream": False},
        timeout=timeout,
    )


def timed_embed(text="East Boston waterfront", timeout=10):
    t0 = time.monotonic()
    r = ollama_embed(text, timeout=timeout)
    elapsed_ms = (time.monotonic() - t0) * 1000
    return elapsed_ms, r


def timed_generate(prompt="list the first ten prime numbers", timeout=120):
    t0 = time.monotonic()
    r = ollama_generate(prompt, timeout=timeout)
    elapsed_s = time.monotonic() - t0
    return elapsed_s, r


def percentile(data, p):
    s = sorted(data)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    c = f + 1 if f + 1 < len(s) else f
    return s[f] + (k - f) * (s[c] - s[f])


def warmup_embed(n=3):
    print("Warming up embedding model...")
    for _ in range(n):
        ollama_embed("warmup")
    print("Warm.")


def warmup_generate(n=1):
    print("Warming up generation model...")
    for _ in range(n):
        ollama_generate("hi", timeout=60)
    print("Warm.")
