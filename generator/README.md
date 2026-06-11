# Generator — Cause-Effect Chain Benchmark

A pure-sampling generator that draws synthetic task-set instances
from an anonymised fingerprint.  Every released instance is drawn
fresh from the per-dimension distributions in `profile.json`; no
source task or chain is ever copied verbatim.

## Layout

The artifact is organised by use-case:

```
artifact/
├── README.md
├── LICENSE
├── data/<USECASE>/<bucket>/                # pre-generated instances
├── fingerprints/<USECASE>/profile.json     # the fingerprint to sample from
└── generator/
    ├── generate.py        # this module
    ├── check.py           # per-instance sanity checker
    ├── validate.py        # fingerprint schema validator
    ├── generate_all.sh    # regenerate every bucket of a use-case
    └── README.md          # you are here
```

Two anonymised fingerprints ship with this release: `TrustMotion` (the
TrustMotion-provided ADAS controller, the companion-paper anchor) and
`WATERS-Bosch` (a WATERS-inspired engine-controller fingerprint
included as a contrasting reference).  The generator is
fingerprint-agnostic; the `<USECASE>` slot is just a directory
key.  Pre-generated synthetic instances ship only for `TrustMotion`
(under `../data/TrustMotion/`); for `WATERS-Bosch` generate as many as
you need from the included fingerprint.

## Quick start

Regenerate the entire `TrustMotion` dataset (5 buckets × 100 instances)
into `../data/TrustMotion/`:

```bash
./generate_all.sh
```

Generate a single bucket explicitly:

```bash
python3 generate.py \
    --profile ../fingerprints/TrustMotion/profile.json \
    --scale   1 \
    --count   100 \
    --seed    43 \
    --out     ../data/TrustMotion/100pct
```

Switch use-case or change the per-bucket count:

```bash
./generate_all.sh -u WATERS-Bosch -n 100 -s 42
```

Requires Python 3 — standard library only, no external packages.

## What the generator samples

Each instance contains $100k$ tasks at scale $S_k$.  Every per-task
attribute is drawn from the fingerprint distribution that owns
that axis:

| step | source distribution | preserved property |
|---|---|---|
| **period $T$** | per-period task share (sums to 100 across `periods_us`); each fingerprint declares its own period grid | marginal period distribution, within $< 1\,\%$ per bin |
| **execution-time record** | per-period WCET data in one of two forms: `histogram` (binned, adaptive bin width) or `aggregate` (envelope plus optional Weibull / empirical distribution).  `bcet = 0` marks "source did not measure BCET"; optional `wcet_min`, `acet`, `exec_distribution` propagate through when populated | per-task execution-time landmarks sampled together so BCET/WCET correlation is preserved; the per-slot WCET envelope flows through as the optional `wcet_min` field |
| **deadline $D$** | per-period $D/T$ histogram, 7 bins | ratio distribution |
| **EA / jitter** | per-period `ea` distribution (optional); global jitter marginal | none-vs-active mix and the active-bin shape |
| **pinning flag** | per-period pinning probability rounded to 0.05 | per-task `pinned` boolean (workload property); released JSON ships every task with `core_id = null` and the evaluator runs the actual allocation at evaluation time |
| **activation class** | per-period periodic / angle-sync / sporadic share (optional) | per-task `activation` discriminator; absent ⇔ `"periodic"` |
| **variable-rate tasks** | optional `variable_period` section for angle-sync or sporadic classes; tasks have a period range rather than a fixed period | per-task `activation` with random in-range period (e.g. WATERS-Bosch's RPM-driven angle-sync tasks) |
| **chain length $L$** | per-bucket quartile bands `{p0, p25, p50, p75, p100}` of $L$ within each of four length buckets (very-short / short / medium / long) | length marginal, multi-modality preserved across buckets |
| **chain shape** | per-bucket period-transition bigram; corpus-wide `chains.global.bigram` serves as fallback for sparse buckets | period transitions per length class |
| **chain participation** | per-period `task_visit_weights` distribution biases chain-position task selection toward hub tasks | hub-and-spoke chain structure |
| **same-task reuse** | `Bernoulli(p = same_task_repeat_share)` per chain; on hit, two distinct chain positions are picked uniformly at random and the task at the first is copied into the second.  Multiplicity is capped at 2 by construction (the natural walk produces all-distinct identities) | repeated-task marginal at fingerprint target |
| **chain budget $b$** | per-bucket 11-decile edges of $\rho = b / T_{\mathrm{th}}$; formula $b = \mathrm{round}_{5\,\mathrm{ms}}(\rho \cdot (1+\delta) \cdot \sum_{j \ge 2} T_j)$ | budget tightness distribution per chain length class |

## Feasibility

Every released instance ships with all `core_id` fields set to
`null`; the evaluator runs a best-fit-decreasing allocation
across each CPU's cores before evaluation.  The generator
guarantees a feasible allocation exists by running the same BFD
algorithm internally during sampling and rejecting any draw where
some task fails to fit under $u_{\mathrm{core}} \le 1$.  In
addition, the generator enforces a $5\,\%$ total-capacity safety
margin: an instance is accepted only if $\sum u_i \le 0.95 \cdot$
(total cores), where the per-bucket core count is the
fingerprint's `platform` core total replicated $k$ times at scale
$k$ (e.g. $9k$ for the TrustMotion 3-CPU/9-core layout, $4k$ for the
WATERS-Bosch 4-CPU/4-core layout).  Every released chain is
feasible by the trivial sum-WCET check.

## CLI

```
generate.py --profile PROFILE [--scale 1..5] [--count N]
            [--seed SEED] [--out DIR]
            [--fingerprint-noise EPS]
            [--length-scaling {scale,source}]
```

- `--profile`            the fingerprint JSON to sample from
- `--scale`              bucket scale $k$ (instance size is $100k$ tasks)
- `--count`              number of instances to emit
- `--seed`               RNG seed (reproducibility)
- `--out`                output directory (one JSON per instance)
- `--fingerprint-noise`  $\varepsilon$, default $0.10$; applies
                         $\pm \varepsilon$ multiplicative jitter to
                         WCET / `wcet_min` / `acet` and to per-chain
                         $\rho$ (clamped to the per-period envelope)
- `--length-scaling`     how chain length $L$ scales with `--scale`.
                         `scale` (default) multiplies the source
                         per-bucket $L$ draws by $k$, so a $k\times$
                         larger platform integrates $k\times$ more
                         end-to-end functionality per chain.  `source`
                         keeps $L$ identically distributed to the
                         source use-case regardless of `--scale`.

## Profile schema (summary)

`fingerprints/<USECASE>/profile.json` is a fingerprint organised
on three axes (global / per-period / chain-axis).  It carries
only aggregate distributions; no source runnable, chain, or
signal name appears anywhere.  Top-level keys:

- `format_version` — `"1.0"`.
- `metadata` — `name`, `description`, `source_type`.
- `platform` — flat list of CPU descriptors `[{cores: N}, ...]`.
- `periods_us` — sorted list of period values (µs) used as the
  axis for the `per_period` table below.
- `jitter` — global jitter marginal.
- `chain_count_per_100_tasks` — integer chains per 100 tasks
  (zero when the source has no chain corpus).
- `same_task_repeat_share` — target share of chains with at least
  one repeated task.
- `per_period` — one entry per period in `periods_us`, with
  `task_share`, `wcet` (histogram or aggregate), `wcet_envelope`,
  `deadline_ratio_hist`, `pinning_probability`, optional `ea` and
  `activation`, and `task_visit_weights`.
- `variable_period` (optional) — non-static-period task classes
  (angle-sync / sporadic) with a period range and an analogous
  attribute set.
- `chains.global` — `asil`, `priority`, `rho_grid_us`, and the
  corpus-wide `bigram` + `rho_decile_edges` (fallback for sparse
  buckets).
- `chains.length_buckets` — per-bucket `share`, `length_quartile_bands`,
  optional `boundaries`, optional bucket-specific `bigram` and
  `rho_decile_edges`, optional `same_task_repeat_positions`.

Run `python3 validate.py PROFILE.json` to check every invariant
declared in `../fingerprints/SCHEMA.md`.

## Notes

- The fitter that produces `profile.json` is **not** released:
  it needs access to the proprietary source XMLs, which are not
  part of this benchmark.  The artifact ships only the fingerprints
  it produced.
