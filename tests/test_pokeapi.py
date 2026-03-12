"""
PokéAPI fault scenarios via Toxiproxy :9003
Works against either:
  - real pokeapi.co (config.json)
  - Prism mock (config.prism.json + docker-compose.prism.yml)
"""
import time
import pytest
import requests

PROXY_BASE = "http://localhost:9003"


def poke(path: str) -> requests.Response:
    return requests.get(f"{PROXY_BASE}{path}", timeout=10)


# -- baseline ------------------------------------------------------------------

def test_pikachu_exists():
    r = poke("/api/v2/pokemon/pikachu")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "pikachu"
    assert data["base_experience"] > 0


def test_unknown_pokemon_404():
    r = poke("/api/v2/pokemon/notapokemon")
    assert r.status_code == 404


# -- latency -------------------------------------------------------------------

def test_latency_200ms_still_returns_data(toxi):
    toxi.add_latency("pokeapi", latency_ms=200, jitter_ms=20)
    t0 = time.monotonic()
    r = poke("/api/v2/pokemon/bulbasaur")
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert r.status_code == 200
    assert elapsed_ms > 180, f"Toxic not firing? elapsed={elapsed_ms:.0f}ms"
    assert r.json()["name"] == "bulbasaur"


def test_latency_spike_scenario(toxi):
    """Simulate a CDN hiccup: brief 2s spike mid-paginated list."""
    # page 1 — clean
    r1 = poke("/api/v2/pokemon?limit=20&offset=0")
    assert r1.status_code == 200

    # spike
    toxi.add_latency("pokeapi", latency_ms=2000, jitter_ms=100)
    t0 = time.monotonic()
    r2 = poke("/api/v2/pokemon?limit=20&offset=20")
    elapsed = time.monotonic() - t0
    assert r2.status_code == 200
    assert elapsed > 1.8

    # spike clears
    toxi.remove_toxic("pokeapi", "latency_pokeapi")
    r3 = poke("/api/v2/pokemon?limit=20&offset=40")
    assert r3.status_code == 200


# -- bandwidth -----------------------------------------------------------------

def test_bandwidth_throttle_on_sprite_endpoint(toxi):
    """
    Sprite JSON includes large base64 or URLs.
    1 KB/s cap should make even small responses take > 0.5s.
    """
    toxi.add_bandwidth("pokeapi", rate_kbps=1)
    t0 = time.monotonic()
    r = poke("/api/v2/pokemon/charizard")
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert elapsed > 0.5


# -- connection fault ----------------------------------------------------------

def test_retry_logic_across_down_then_up(toxi):
    """
    Down for 1s then re-enable. Client retry should recover.
    Pattern: test your retry/backoff code, not just the happy path.
    """
    import time

    toxi.disable("pokeapi")

    # first attempt must fail
    with pytest.raises(requests.exceptions.ConnectionError):
        poke("/api/v2/pokemon/squirtle")

    # re-enable after 1s
    time.sleep(1)
    toxi.enable("pokeapi")

    # second attempt must succeed
    r = poke("/api/v2/pokemon/squirtle")
    assert r.status_code == 200
    assert r.json()["name"] == "squirtle"


def test_slicer_on_large_list(toxi):
    """
    Slicer with 2-byte chunks simulates a very chatty, fragmented TCP session.
    Response must still be valid JSON after reassembly.
    """
    toxi.add_slicer("pokeapi", avg_size=2, delay_us=100)
    r = poke("/api/v2/pokemon?limit=100")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] > 0
    assert len(data["results"]) == 100
