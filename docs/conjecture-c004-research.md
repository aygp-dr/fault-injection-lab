# Conjecture C-004 Research: Prism Mock + Toxiproxy as PokéAPI Replacement

**Conjecture**: Prism mock + Toxiproxy fully replaces live PokéAPI for the test scenarios in spec.org (same status codes, same schema).

**Falsification criterion**: All PokéAPI tests pass against Prism with same status codes; diff response schemas.

**Date**: 2026-03-11

---

## 1. Prism Mock Behavior Analysis

### 1.1 Static Mode (Default)

Prism's default mock mode is **static**. It generates responses using this priority chain:

1. If a response body **example** exists in the spec, use it verbatim.
2. If no example exists, walk the schema and:
   - Use `default` values if present.
   - Use `examples` array values (first element) if present.
   - Use `null` for nullable fields with no example/default.
   - Use format-aware placeholder values for non-nullable fields with `format` (e.g., a URI for `format: uri`).
   - Fall back to `"string"` for strings, `0` for numbers/integers.

**Key implication**: Static mode returns the **same data every time**. The `name` field on `PokemonDetail` has no `example` value in the PokéAPI spec, so Prism will return `"string"` for it. It will never return `"pikachu"`, `"bulbasaur"`, or `"squirtle"`.

### 1.2 Dynamic Mode (`-d` flag or `Prefer: dynamic=true`)

Dynamic mode uses **json-schema-faker** and **Faker.js** to generate random values conforming to the schema. The `name` field (type: string, maxLength: 200) would receive a random string like `"lorem ipsum"` or similar Faker output. This is even less likely to match `"pikachu"`.

Dynamic mode is useful for fuzz-style testing but makes exact-value assertions impossible.

### 1.3 x-faker Extensions

Prism supports `x-faker` annotations on schema properties to control dynamic generation (e.g., `x-faker: name.firstName`). The PokéAPI OpenAPI spec does **not** use `x-faker` annotations. Adding them would require modifying the spec.

### 1.4 Prefer Header for Response Control

Prism supports a `Prefer` header to override behavior per-request:
- `Prefer: code=404` -- force a specific status code
- `Prefer: example=keyName` -- select a named example
- `Prefer: dynamic=true` -- force dynamic generation for one request

### 1.5 Route Matching and 404 Behavior

When Prism receives a request for a **route that exists** in the spec, it returns the lowest 2XX response (typically 200). When a request hits a **route that does not exist** in the spec, Prism returns a `404 application/problem+json` error.

**Critical detail**: The PokéAPI spec does **not** define a 404 response for any endpoint. Prism's 404 behavior comes from its own routing layer (unrecognized path), not from the API spec.

---

## 2. PokéAPI OpenAPI Spec Analysis

### 2.1 Spec Basics

- Format: OpenAPI 3.1.0
- Size: ~9,470 lines, comprehensive
- Server URL: `https://pokeapi.co`
- All paths use **trailing slashes** (e.g., `/api/v2/pokemon/{id}/`, `/api/v2/pokemon/`)

### 2.2 Relevant Endpoints

| Spec Path | Test Path | Match? |
|---|---|---|
| `/api/v2/pokemon/` | `/api/v2/pokemon?limit=100` | Depends on trailing slash handling |
| `/api/v2/pokemon/{id}/` | `/api/v2/pokemon/pikachu` | **NO** -- missing trailing slash |
| `/api/v2/pokemon/{id}/` | `/api/v2/pokemon/bulbasaur` | **NO** -- missing trailing slash |
| `/api/v2/pokemon/{id}/` | `/api/v2/pokemon/charizard` | **NO** -- missing trailing slash |
| `/api/v2/pokemon/{id}/` | `/api/v2/pokemon/squirtle` | **NO** -- missing trailing slash |
| `/api/v2/pokemon/{id}/` | `/api/v2/pokemon/notapokemon` | **NO** -- missing trailing slash |

**Trailing slash mismatch**: The spec defines paths with trailing slashes (`/api/v2/pokemon/{id}/`) but tests call without them (`/api/v2/pokemon/pikachu`). Prism treats these as different routes. Requests to `/api/v2/pokemon/pikachu` would get a Prism-level 404, not a 200 from the `/api/v2/pokemon/{id}/` route.

This is fixable by either:
- Modifying the spec to remove trailing slashes
- Modifying test URLs to add trailing slashes
- Note: the real PokéAPI redirects non-slash URLs to slash URLs (301 redirect), which Prism does not replicate

### 2.3 Schema: PokemonDetail

Key fields tested by spec.org:

| Field | Type in Spec | Example in Spec? | Required? |
|---|---|---|---|
| `name` | `string` (maxLength: 200) | **No** | Yes |
| `base_experience` | `integer \| null` | **No** | **No** (not in required list) |

The `required` array for PokemonDetail includes: `abilities`, `cries`, `forms`, `game_indices`, `held_items`, `id`, `location_area_encounters`, `moves`, `name`, `past_abilities`, `past_types`, `species`, `sprites`, `stats`, `types`.

Notable: `base_experience` is **not required** and is **nullable**. Prism static mode will return `null` for it. Prism dynamic mode may return `null` or a random integer.

### 2.4 Schema: PaginatedPokemonSummaryList

Key fields tested by spec.org:

| Field | Type in Spec | Example in Spec? |
|---|---|---|
| `count` | `integer` | `example: 123` |
| `results` | `array` of `PokemonSummary` | No |

The `count` field has `example: 123`, so Prism static mode returns `123`. The `results` array has no example; Prism will generate an array with one item containing `{"name": "string", "url": "http://example.com"}` or similar.

**Critical**: The `results` array will **not** contain 100 items. Prism generates a minimal array (typically 1 element by default in static mode, or a small random count in dynamic mode). The test asserts `len(data["results"]) == 100`.

### 2.5 No 404 Response Defined

The PokéAPI OpenAPI spec defines **only 200 responses** for all endpoints. There is no 404 response schema. Prism will still return 404 for unrecognized routes, but it will be a `application/problem+json` format (Prism's own error format), not PokéAPI's native 404 format.

---

## 3. Test-by-Test Compatibility Assessment

### test_pikachu_exists

```python
r = poke("/api/v2/pokemon/pikachu")
assert r.status_code == 200
assert data["name"] == "pikachu"
assert data["base_experience"] > 0
```

| Assertion | Prism Static | Prism Dynamic | Verdict |
|---|---|---|---|
| `status_code == 200` | **FAIL** (404 due to trailing slash mismatch) | **FAIL** | Path mismatch |
| `data["name"] == "pikachu"` | **FAIL** (returns `"string"`) | **FAIL** (random string) | Never matches |
| `data["base_experience"] > 0` | **FAIL** (returns `null`, nullable field) | **MAYBE** (random int or null) | Nullable field |

**Verdict: FAIL** -- Three independent failure modes.

### test_unknown_pokemon_404

```python
r = poke("/api/v2/pokemon/notapokemon")
assert r.status_code == 404
```

| Assertion | Prism Static | Prism Dynamic | Verdict |
|---|---|---|---|
| `status_code == 404` | **PASS*** (Prism returns 404 for unrecognized route) | **PASS*** | Passes accidentally |

*Note: This "passes" because `/api/v2/pokemon/notapokemon` (no trailing slash) doesn't match any route in the spec, so Prism returns its own 404. However, this also means `/api/v2/pokemon/pikachu` gets 404 for the same reason -- all name-based lookups get 404 because of the trailing slash mismatch. If the trailing slash issue is fixed, then `notapokemon` would match `/api/v2/pokemon/{id}/` and return 200, making this test **FAIL**.

**Verdict: FRAGILE** -- Passes only because of the trailing slash bug, and fixing that bug breaks this test.

### test_latency_200ms_still_returns_data

```python
r = poke("/api/v2/pokemon/bulbasaur")
assert r.status_code == 200
assert r.json()["name"] == "bulbasaur"
```

**Verdict: FAIL** -- Same issues as test_pikachu_exists (trailing slash + name assertion).

### test_latency_spike_scenario

```python
r1 = poke("/api/v2/pokemon?limit=20&offset=0")
assert r1.status_code == 200
r2 = poke("/api/v2/pokemon?limit=20&offset=20")
assert r2.status_code == 200
r3 = poke("/api/v2/pokemon?limit=20&offset=40")
assert r3.status_code == 200
```

| Assertion | Prism Static | Verdict |
|---|---|---|
| `status_code == 200` | **MAYBE** -- depends on whether Prism matches `/api/v2/pokemon/` with query params when called as `/api/v2/pokemon?...` (no trailing slash) | Path mismatch risk |

**Verdict: LIKELY FAIL** -- The list endpoint in the spec is `/api/v2/pokemon/` (trailing slash). The test calls `/api/v2/pokemon?limit=20&offset=0` (no trailing slash before query string). Prism may not match this.

### test_bandwidth_throttle_on_sprite_endpoint

```python
r = poke("/api/v2/pokemon/charizard")
assert r.status_code == 200
```

**Verdict: FAIL** -- Same trailing slash issue.

### test_retry_logic_across_down_then_up

```python
r = poke("/api/v2/pokemon/squirtle")
assert r.status_code == 200
assert r.json()["name"] == "squirtle"
```

**Verdict: FAIL** -- Same trailing slash issue + name assertion.

### test_slicer_on_large_list

```python
r = poke("/api/v2/pokemon?limit=100")
assert r.status_code == 200
data = r.json()
assert data["count"] > 0
assert len(data["results"]) == 100
```

| Assertion | Prism Static (if path matches) | Verdict |
|---|---|---|
| `status_code == 200` | MAYBE (trailing slash) | Path risk |
| `data["count"] > 0` | **PASS** (example value is 123) | OK |
| `len(data["results"]) == 100` | **FAIL** (Prism generates ~1 item) | Array length mismatch |

**Verdict: FAIL** -- Prism does not respect `limit` query parameter to control array length. It generates a minimal array from the schema, ignoring query params entirely.

---

## 4. Key Risks Summary

### Risk 1: Name-based assertions (CRITICAL)

Tests assert `data["name"] == "pikachu"`, `== "bulbasaur"`, `== "squirtle"`. Prism is a **schema-driven mock**, not a **data-driven mock**. It does not know that the path parameter `pikachu` should map to a response with `name: "pikachu"`. In static mode it returns `"string"`; in dynamic mode it returns random text. There is no Prism feature that echoes path parameters into response fields.

Affected tests: `test_pikachu_exists`, `test_latency_200ms_still_returns_data`, `test_retry_logic_across_down_then_up`

### Risk 2: Trailing slash mismatch (CRITICAL)

The PokéAPI spec defines all paths with trailing slashes (`/api/v2/pokemon/{id}/`). Tests omit trailing slashes (`/api/v2/pokemon/pikachu`). Prism performs exact route matching and will return 404 for the non-slash variants.

Affected tests: All 7 tests.

### Risk 3: Array length assertions (HIGH)

`test_slicer_on_large_list` asserts `len(data["results"]) == 100`. Prism ignores the `limit` query parameter -- it generates arrays based on schema structure, not query semantics. Default static mode produces 1-element arrays.

Affected tests: `test_slicer_on_large_list`

### Risk 4: Nullable field assertions (MEDIUM)

`base_experience` is nullable and not required in the schema. Prism static mode returns `null`. The test asserts `data["base_experience"] > 0`, which will raise `TypeError` on comparison with `null`/`None`.

Affected tests: `test_pikachu_exists`

### Risk 5: 404 for unknown pokemon (SUBTLE)

If the trailing slash issue is fixed, `/api/v2/pokemon/notapokemon/` matches `/api/v2/pokemon/{id}/` and Prism returns 200 (the only defined response). The test expects 404. Prism has no way to know that `notapokemon` is not a valid pokemon -- it treats `{id}` as accepting any string.

Affected tests: `test_unknown_pokemon_404`

---

## 5. Prism "Example" vs "Dynamic" Mock Modes -- Which Helps?

| Mode | Name assertions | base_experience | Array length | 404 behavior |
|---|---|---|---|---|
| Static (default) | `"string"` -- FAIL | `null` -- FAIL | 1 element -- FAIL | Route-level only |
| Dynamic (`-d`) | Random -- FAIL | Random int or null -- MAYBE | Small random count -- FAIL | Route-level only |
| Custom examples in spec | Could work if crafted | Could work if crafted | Still ignores `limit` | Still no semantic 404 |

Neither mode solves the fundamental problems. Prism is designed to validate that **clients can handle the response schema**, not to replicate **specific API semantics** (name lookup, pagination limits, existence checks).

---

## 6. Recommended Approaches

### Option A: Modify tests to be Prism-compatible (RECOMMENDED)

Rewrite assertions to test schema shape rather than specific values:

```python
# Instead of: assert data["name"] == "pikachu"
# Use:        assert "name" in data and isinstance(data["name"], str)

# Instead of: assert data["base_experience"] > 0
# Use:        assert "base_experience" in data

# Instead of: assert len(data["results"]) == 100
# Use:        assert "results" in data and isinstance(data["results"], list)
```

This aligns with what Prism actually provides: schema-conformant responses.

**Drawback**: Weakens the tests. They no longer verify correct API behavior, only schema conformance.

### Option B: Create a custom PokéAPI spec with embedded examples

Add response-level examples to the OpenAPI spec for each endpoint the tests use:

```yaml
/api/v2/pokemon/{id}:  # Remove trailing slash
  get:
    responses:
      '200':
        content:
          application/json:
            examples:
              pikachu:
                value:
                  name: pikachu
                  base_experience: 112
                  id: 25
                  # ... full response
```

Then use `Prefer: example=pikachu` in test requests.

**Drawback**: Requires maintaining a parallel, hand-crafted spec. Loses the benefit of using PokéAPI's official spec. Must add trailing-slash-free paths. Tests must know to send the Prefer header.

### Option C: Use Prism in proxy+validation mode instead of mock mode

Run Prism as a **validation proxy** in front of the real PokéAPI:

```bash
prism proxy specs/pokeapi.yaml https://pokeapi.co
```

This gives real data AND schema validation. Toxiproxy sits in front of Prism.

**Drawback**: Requires internet access, defeating the "fully offline" goal.

### Option D: Use a different mock tool (WireMock, mockserver)

Use a data-driven mock (WireMock, MockServer) that supports request matching and canned responses rather than a schema-driven mock.

**Drawback**: Requires maintaining response fixtures. Loses automatic schema-based generation.

### Option E: Hybrid -- Prism mock for schema tests, custom stub for value tests

Use Prism for tests that only check schema/status codes. Use a lightweight Python HTTP stub (or WireMock) for tests requiring specific values.

---

## 7. Verdict

**LIKELY FALSE** -- Conjecture C-004 is likely false as stated.

Prism mock + Toxiproxy **cannot** fully replace live PokéAPI for the test scenarios in spec.org without modifying either the tests or the OpenAPI spec. The five independent failure modes are:

1. **Name-based assertions** (`== "pikachu"`) will never match Prism's generated data -- this is a fundamental architectural mismatch between schema-driven mocking and data-driven testing.
2. **Trailing slash mismatch** between spec paths and test URLs causes Prism to 404 on all detail endpoints.
3. **Array length assertion** (`len(results) == 100`) is incompatible with Prism's schema-based array generation.
4. **Nullable field assertion** (`base_experience > 0`) fails because Prism returns null for nullable fields without examples.
5. **Semantic 404** for unknown pokemon cannot be replicated -- Prism matches routes by path template, not by data existence.

The conjecture would need to be **narrowed** to be testable: "Prism mock produces responses with the same **schema structure** (field names, types) as live PokéAPI" -- which is likely true and useful, but different from "same status codes, same schema [with passing tests]."

### What would make C-004 true?

The conjecture could be made true by:
1. Fixing trailing slashes (modify spec or test URLs)
2. Replacing value assertions with schema assertions in tests
3. Replacing array-length assertions with array-presence assertions
4. Adding response examples to the spec for nullable fields
5. Accepting that 404-for-unknown-entity cannot be replicated (or using `Prefer: code=404`)

This represents significant test modification, which means the **original conjecture as stated is false**: the existing tests do not pass unmodified against Prism.

### Instrumentation needed

To confirm empirically:
1. Download the PokéAPI spec, fix trailing slashes, start Prism
2. Run the 7 tests against Prism and record which assertions fail
3. Diff the response schemas (field names + types) between live PokéAPI and Prism
4. Measure whether schema structure (ignoring values) matches
