# Fingerprint schema (v1)

A fingerprint is an anonymised statistical summary of a real-time
workload, sufficient to drive a generator that emits synthetic
instances statistically close to the source.

Three axes:

- **Global** — workload-level scalars and toplevel metadata.
- **Period axis** — per-period attributes (one entry per static period).
- **Variable-period axis** — non-static-period task classes
 (angle-sync, sporadic). Empty for most sources.
- **Chain axis** — chain-corpus statistics, structured into four
 chain-length buckets plus a few global chain attributes.

All counts are reported as **per-100 fractions** (per 100 tasks, per
100 chains, or per 100 transitions, depending on context) to avoid
disclosing the source population size.

```jsonc
{
 // ─── Global ──────────────────────────────────────────────────────
 "format_version": "1.0",
 "metadata": {
 "name": "fitted",
 "description": "...",
 "source_type": "fitted" | "hand-curated"
 },
 "platform": [{"cores": 3}, {"cores": 2}, {"cores": 4}],

 "periods_us": [2500, 5000, 10000, ...], // canonical order
 "jitter": {"none": 89.2, "values": [[0.1, 8.8], [0.5, 2.0]]},
 "ea_default": "none" | "varies", // optional shortcut

 "chain_count_per_100_tasks": 38, // integer
 "same_task_repeat_share": 0.75, // 0.05 grain

 // ─── Per-period (static-rate tasks) ──────────────────────────────
 "per_period": {
 "5000": {
 "task_share": 9.8, // % of all tasks
 "wcet": { ... see below ... },
 "wcet_envelope": [80, 1300], // [min,max] in us
 "pinning_probability": 0.95, // 0.05 grain
 "deadline_ratio_hist": [ // 7-bin D/T histogram
 {"bin": [0.0, 0.2], "count": 0.0}, // counts are
 {"bin": [0.2, 0.4], "count": 5.1}, // per-100-tasks
 {"bin": [0.4, 0.6], "count": 10.3}, // at THIS period
 {"bin": [0.6, 0.75], "count": 20.5},
 {"bin": [0.75, 0.9], "count": 25.6},
 {"bin": [0.9, 1.0], "count": 28.2},
 {"bin": [1.0, 1.0001], "count": 10.3}
 ],
 "ea": { // optional — omit if all-none
 "none": 50.0,
 "active_bins": [{"bin": [0.5, 0.75], "count": 25.0}, ...]
 },
 "activation": { // optional — defaults to all periodic
 "periodic": 100.0,
 "angle_sync": 0.0,
 "sporadic": 0.0
 },
 "task_visit_weights": [ // per-task chain-visit weight dist
 {"weight": 0, "share": 60.0}, // % of tasks at this period
 {"weight": 1, "share": 20.0},
 {"weight": 5, "share": 10.0},
 {"weight": 20, "share": 10.0}
 ]
 // optional WATERS extensions (omit if all-zero):
 // "bcet_envelope": [..., ...]
 // "acet_envelope": [..., ...]
 // "wcet_min_envelope": [..., ...]
 // "exec_distribution": { kind: weibull|empirical, ... }
 },
 "10000": { ... },
 ...
 },

 // ─── Variable-period tasks (optional) ────────────────────────────
 "variable_period": [
 {
 "activation": "angle_sync",
 "period_range_us": [10000, 60000], // RPM-dependent
 "task_share": 1.92, // % of all tasks
 "wcet": { ... },
 "wcet_envelope": [1, 100],
 "pinning_probability": 1.0,
 "deadline_ratio_hist": [...]
 }
 ],

 // ─── Chain corpus ────────────────────────────────────────────────
 "chains": {
 "global": {
 "asil": {"B": 69.23, "C": 10.26, "none": 20.51}, // optional
 "priority": [ // optional
 {"value": 0.40, "share": 12.8},
 {"value": 0.45, "share": 7.7},
 ...
 ],
 "rho_grid_us": 5000, // budget rounding grid; default 5000
 "bigram": { ... }, // corpus-wide bigram; fallback for sparse buckets
 "rho_decile_edges": [ ... ] // corpus-wide ρ deciles; fallback for sparse buckets
 },

 "length_buckets": {
 "very_short": { ... per-bucket fields, see below ... }, // L <= 3
 "short": { ... }, // 4..6
 "medium": { ... }, // 7..12
 "long": { ... } // >= 13
 }
 }
}
```

### Per-period `wcet` field

Each per-period `wcet` entry has exactly one of two shapes; the
consumer detects which by inspecting the key (`histogram` or
`aggregate`).

**Histogram form**:

```jsonc
"wcet": {
 "histogram": [
 {"wcet_bin": [100, 200], "count": 5.88},
 {"wcet_bin": [200, 400], "count": 3.92},
 ...
 ]
}
```

- Bin width is adaptive: `max(200 us, range/8)` per period.
- After binning, a post-pass merges any bin with `count < 1 / N_total *
 100` into its neighbour, so every bin holds ≥ 2 source tasks.
- WATERS optional extensions can attach to each bin as
 `wcet_min_bin`, `bcet_bin`, `acet_bin`, or `exec_distribution`.

**Aggregate form**:

```jsonc
"wcet": {
 "aggregate": {
 "wcet_range": [1, 37],
 "bcet_range": [0, 30],
 "acet_range": [1, 32],
 "distribution": {"kind": "weibull", "alpha": 2.0, "beta": 31.45}
 }
}
```

- Used by sources that ship a fitted per-instance execution-time
 distribution (Kramer-2015 / WATERS style).
- `bcet_range`, `acet_range`, `distribution` are optional WATERS
 extensions.

### Per-length-bucket fields (chain axis, four buckets)

A bucket is **omitted entirely** if the source has 0 chains in it.
Buckets with `< 5` source chains omit their own `bigram` and
`rho_decile_edges` and inherit them from `chains.global` at
consume time. All other per-bucket fields (`share`,
`length_quartile_bands`, `boundaries`, `same_task_repeat_positions`)
are still populated.

```jsonc
"medium": { // 7 <= L <= 12
 "share": 48.72, // % of chains
 "length_quartile_bands": [7, 9, 10, 10, 12], // {p0, p25, p50, p75, p100}
 "boundaries": { // optional, present when source has bus-PDU boundaries
 "starts_with_pdu_in": 82.0,
 "ends_with_pdu_out": 92.0
 },
 "bigram": { // OMITTED when source bucket had < 5 chains;
 "Start": {"5000": 0.86, ...}, // consumer then falls back to chains.global.bigram
 "5000": {"5000": 18.71, ...},
 "40000": {..., "End": 1.72}
 },
 "same_task_repeat_positions": [ // optional
 {"first": 1, "second": 7, "share": 35.0}, // relative positions, 0-indexed
 {"first": 2, "second": 8, "share": 28.0},
 ...
 ],
 "rho_decile_edges": [0.36, 0.49, ..., 1.93] // OMITTED for sparse buckets too
}
```

## Field-by-field semantics

### Global

| Field | Type | Required | Semantics |
|---|---|---|---|
| `format_version` | string | yes | `"1.0"` |
| `metadata.name` | string | yes | short identifier; no source IP |
| `metadata.description` | string | yes | prose summary |
| `metadata.source_type` | enum | yes | provenance type |
| `platform` | list of `{cores: int}` | yes | per-CPU core count, no role labels |
| `periods_us` | sorted list of int | yes | canonical period set in µs |
| `jitter` | object | yes | global J/T marginal |
| `ea_default` | enum | optional | `"none"` if all per-period EA blocks are all-none; allows omitting per-period `ea` |
| `chain_count_per_100_tasks` | int | yes | rounded |
| `same_task_repeat_share` | float (0.05 grain) | yes | global marginal Bernoulli rate |

### Per-period (B-axis)

| Field | Type | Required | Semantics |
|---|---|---|---|
| `task_share` | float | yes | per-100 fraction of static-period tasks at this period |
| `wcet` | object | yes | exactly one of `{histogram, aggregate}` as the inner key; consumer detects which form to dispatch |
| `wcet_envelope` | `[min, max]` | yes | hard clamp at sample time |
| `pinning_probability` | float (0.05 grain) | yes | per-period scalar |
| `deadline_ratio_hist` | list of 7 bins | yes | per-100-task at this period |
| `ea` | object | optional | omit when `ea_default = "none"` and bucket is all-none |
| `activation` | object | optional | defaults to `{periodic: 100, angle_sync: 0, sporadic: 0}` |
| `task_visit_weights` | list of `{weight, share}` | yes | per-task chain-visit weight distribution; share sums to 100 within the period |

### Variable-period (optional axis)

One entry per non-static task class (typically angle-sync):

| Field | Type | Required | Semantics |
|---|---|---|---|
| `activation` | enum | yes | `angle_sync` or `sporadic` |
| `period_range_us` | `[min, max]` | yes | period drawn uniformly in range at instance time |
| `task_share` | float | yes | per-100 of total tasks |
| `wcet`, `wcet_envelope`, `pinning_probability`, `deadline_ratio_hist` | same as per-period | yes | analogous fields |

### Chain axis

`chains.global`:

| Field | Type | Required | Semantics |
|---|---|---|---|
| `asil` | dict of label → % | optional | per-100-chain marginal; omit when all `none` |
| `priority` | list of `{value, share}` | optional | per-100-chain histogram on a discrete grid |
| `rho_grid_us` | int | yes | budget rounding grid; default 5000 |
| `bigram` | dict of from-period → dict of to-period → count | yes | corpus-wide period bigram; serves as fallback for sparse buckets |
| `rho_decile_edges` | list of 11 floats | yes | corpus-wide ρ deciles; serves as fallback for sparse buckets |

`chains.length_buckets[bucket]` (bucket ∈ {`very_short`, `short`, `medium`, `long`}, present only when source has ≥ 1 chain in bucket):

| Field | Type | Required | Semantics |
|---|---|---|---|
| `share` | float | yes | per-100-chain fraction in this bucket |
| `length_quartile_bands` | `[p0, p25, p50, p75, p100]` of L | yes | within-bucket length distribution |
| `boundaries` | `{starts_with_pdu_in, ends_with_pdu_out}` | optional | omit on non-bus sources |
| `bigram` | dict of from-period → dict of to-period → count | optional | per-100-transition shares within bucket; **omit when bucket has < 5 source chains** — consumer then reads `chains.global.bigram` |
| `same_task_repeat_positions` | list of `{first, second, share}` | optional | informational: conditional on chain having a repeat, the (first, second) index distribution observed in the *source* workload (positions 0-indexed). Not consumed by the generator — kept only as a record of the source's positional structure. |
| `rho_decile_edges` | list of 11 floats | optional | `{p0, p10, ..., p100}` of ρ within bucket; **omit when bucket has < 5 source chains** — consumer then reads `chains.global.rho_decile_edges` |

### Bucket boundaries

Fixed thresholds applied at fit time:

```
very_short : L <= 3
short : 4 <= L <= 6
medium : 7 <= L <= 12
long : L >= 13
```

A bucket with 0 source chains is omitted from `length_buckets`
entirely. A bucket with 1..4 source chains keeps its `share`,
`length_quartile_bands`, and `boundaries`, but omits the
`bigram` and `rho_decile_edges` fields — the consumer reads
`chains.global.bigram` and `chains.global.rho_decile_edges`
instead.

## Generator behaviour

Sketch of the per-instance generation algorithm:

1. **Platform**: replicate `platform` k times for scale S_k.
 Total tasks = `100k`.
2. **Static-period task pool**: sample `(1 − variable_period_share) ·
 100k` tasks; for each, draw period from `periods_us` weighted by
 `task_share`, then draw WCET / D/T / EA / pinning / activation from
 the corresponding period entry. Sample task-visit-weight from
 `task_visit_weights` and store it on the task.
3. **Variable-period task pool**: for each `variable_period` entry,
 sample `task_share · 100k / 100` tasks at that activation class,
 with period drawn uniformly from `period_range_us`.
4. **Chains**: instance has `chain_count_per_100_tasks · 100k / 100`
 chains. For each chain:
 - Pick length bucket weighted by `share`; draw `L_src` from the
 bucket's `length_quartile_bands` (uniform within a quartile band).
 Final `L = k · L_src` under the default `--length-scaling scale`,
 so each chain integrates k× more end-to-end functionality at
 scale `S_k` (matching the platform-replication factor); pass
 `--length-scaling source` to opt out and keep `L = L_src`.
 - Pick chain-position periods by walking the bucket's `bigram`
 (or the global bigram, when `fallback = "global"`) from `Start`
 for L steps. The walk can terminate earlier when the row offers
 only an `End` continuation (preserves natural termination).
 - For each chain position, draw a task from the period-matching pool
 weighted by the task's `task_visit_weight`. Identities are
 unique within the chain (no repeats during the walk).
 - Optionally honour `boundaries`: if `starts_with_pdu_in` fires,
 mark position 0 as a boundary task; same for the end.
 - **Same-task reuse** (separate post-pass): with probability
 `same_task_repeat_share` per chain, pick two distinct positions
 uniformly at random in `[0, L-1]` and copy the task at the first
 into the second. Multiplicity is capped at 2 by construction.
 The optional `same_task_repeat_positions` field is *not* consumed
 by the generator — it is informational: it records the
 bracketing / non-adjacent position distribution observed in the
 source workload. The synthetic preserves the marginal repeat
 rate but not the source's positional structure.
 - Draw ρ from the bucket's `rho_decile_edges` (uniform within a
 decile) and compute the budget
 `b = round_grid(ρ · Σ_{j≥2} T_j)`.
 - Assign ASIL (from `asil`) and priority (from `priority`) if
 populated.

## Validation invariants

A `validate.py` checks:

```
format_version == "1.0"
metadata.{name, description, source_type} all present
platform is a non-empty list of {cores: int}
periods_us is sorted and contains only positive ints
chains.global.bigram is non-empty (when chain_count_per_100_tasks > 0)
chains.global.rho_decile_edges has 11 sorted floats (when chain_count_per_100_tasks > 0)
sum(per_period[p].task_share) + sum(variable_period[i].task_share) == 100 ± 1.5
sum(length_buckets[b].share) == 100 ± 1.5 (when ≥ 1 bucket present)
sum(per_period[p].activation.*) == 100 ± 1.5 for each period that has activation
sum(per_period[p].task_visit_weights[].share) == 100 ± 1.5 for each period
sum(per_period[p].deadline_ratio_hist[].count) == 100 ± 1.5 for each period
length_quartile_bands within bucket's L range and sorted
each per-bucket rho_decile_edges (when present) has 11 sorted floats
all period keys in any bigram are strings of ints in periods_us
 (plus the literal "Start" / "End" absorbing states)
```
