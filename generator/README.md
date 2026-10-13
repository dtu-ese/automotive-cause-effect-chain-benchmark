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
├── VERSION
├── LICENSE
├── data/<USECASE>/<bucket>/                # pre-generated instances
├── fingerprints/SCHEMA.md                  # fingerprint schema
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
`WATERS-Bosch` (an engine-controller fingerprint built from the
published characteristics of Kramer et al., WATERS 2015, included as a
contrasting reference; it uses the factor variant of the aggregate
execution-time form and the `waters2015` chain model, see
`../fingerprints/SCHEMA.md`).  The generator is
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
| **period $T$** | per-period task share (% of all tasks; together with the `variable_period` shares it sums to 100); each fingerprint declares its own period grid | marginal period distribution, up to the shifts caused by rejecting over-utilized draws (at most about 1.5 percentage points per bin in the TrustMotion buckets) |
| **execution-time record** | per-period WCET data in one of two forms: `histogram` (binned, adaptive bin width) or `aggregate` (envelope plus optional Weibull / empirical distribution).  `bcet = 0` marks "source did not measure BCET"; optional `wcet_min`, `acet`, `exec_distribution` propagate through when populated | envelope form: `bcet`, `acet` and `wcet_min` are drawn independently from their ranges and clamped so that BCET ≤ ACET ≤ WCET and `wcet_min` ≤ WCET (no correlation modelled); factor variant (WATERS-Bosch): the ACET is drawn first and BCET / WCET are ACET × factor, so all three are correlated |
| **deadline $D$** | per-period $D/T$ histogram, 7 bins | ratio distribution |
| **EA / jitter** | per-period `ea` distribution (optional); global jitter marginal | none-vs-active mix and the active-bin shape |
| **pinning flag** | per-period pinning probability rounded to 0.05 | per-task `pinned` boolean (workload property); the shipped `placement` is a reference allocation (see Feasibility), independent of this flag |
| **variable-rate tasks** | optional `variable_period` section for angle-sync or sporadic classes; tasks have a period range rather than a fixed period | per-task `activation` (`"angle_sync"` or `"sporadic"`) with random in-range period (e.g. WATERS-Bosch's RPM-driven angle-sync tasks); static-period tasks carry no `activation` field (periodic) |
| **chain length $L$** | per-bucket quartile bands `{p0, p25, p50, p75, p100}` of $L$ within each of four length buckets (very-short / short / medium / long) | length marginal, multi-modality preserved across buckets |
| **chain shape** | per-bucket period-transition bigram; corpus-wide `chains.global.bigram` serves as fallback for sparse buckets | period transitions per length class |
| **chain participation** | per-period `task_visit_weights` distribution biases chain-position task selection toward hub tasks | hub-and-spoke chain structure |
| **same-task reuse** | `Bernoulli(p = same_task_repeat_share)` per chain; on hit, two distinct chain positions are picked uniformly at random and the task at the first is copied into the second.  Multiplicity is capped at 2 by construction (the natural walk produces all-distinct identities) | repeated-task marginal at fingerprint target |
| **chains, `waters2015` model** (WATERS-Bosch) | 1–3 activation patterns per chain and 2–5 tasks per pattern (× k under `--length-scaling scale`), periods chosen uniformly among those with enough tasks | Kramer et al.'s chain shape; budget = sum of the patterns' periods |
| **chain budget $b$** | per-bucket 11-decile edges of $\rho = b / T_{\mathrm{th}}$; formula $b = \mathrm{round}_{g}(\rho \cdot (1+\delta) \cdot \sum_{j} T_j)$ with grid $g$ = `rho_grid_us` (smallest period capped at 5 ms; 2.5 ms for TrustMotion) | budget tightness distribution per chain length class |

## Feasibility

Sampling first runs a capacity pre-check: tasks in order of
decreasing utilization go to the least-loaded CPU and core, and
every task must fit under $u_{\mathrm{core}} \le 1$.  It also enforces a
$5\,\%$ total-capacity safety margin: an instance is accepted only
if $\sum u_i \le 0.95 \cdot$ (total cores), where the per-bucket core
count is the fingerprint's `platform` core total replicated $k$
times at scale $k$ (e.g. $9k$ for the TrustMotion 3-CPU/9-core
layout, $4k$ for the WATERS-Bosch 4-CPU/4-core layout).  Draws that
fail either condition are redrawn.  Every released chain is
feasible by the trivial sum-WCET check.

Utilization alone does not make an instance schedulable.  A
deterministic post-pass (`make_schedulable`, benchmark version 2.0)
therefore (with `earliest_activation` read as release offset,
`max_jitter` as release jitter, and deadlines relative to the
nominal release)

0. reduces offsets that reach the period modulo the period;
1. redraws the deadline of every task with `wcet >= deadline` (an
   artifact of sampling WCET and $D/T$ independently and clamping
   $D$ up to the WCET) from its period's $D/T$ histogram restricted
   to deadlines above the WCET, using a separate random stream per
   task;
2. lowers any release jitter with `max_jitter + wcet > deadline` to
   the largest fingerprint jitter ratio that fits (or removes it);
3. scales (and floors) all execution times of the instance by the
   largest $\alpha$ on the grid $\{1, 0.995, 0.990, \ldots\}$
   (scanned downwards, since placement success is not monotone in
   $\alpha$) for which the schedulability-aware placement succeeds,
   and records
   $\alpha$ in `metadata.wcet_scaling_alpha`;
4. writes that placement to every task's `placement`.

The placement visits tasks in order of decreasing density
$(\mathrm{wcet} + \mathrm{max\_jitter}) / \mathrm{deadline}$.  CPUs are
tried in order of increasing mean per-core utilization and, within
a CPU, cores in order of increasing utilization; a task goes to the
first core on which every task still satisfies $R \le D$ under
partitioned preemptive fixed-priority scheduling with
deadline-monotonic priorities (ties by period, then name), using
jitter-aware response-time analysis at the critical instant, which
bounds every offset pattern (single-job analysis, valid since
$R \le D \le T$).  `check.py`
re-verifies this for every instance.  The post-pass draws no
random numbers from the instance's streams (each instance samples
its tasks and its chains from two separate streams seeded by
(seed, scale, index)).  If it fails for a draw, i.e. no $\alpha$ on
the grid works, the draw is discarded and sampling continues on the
same streams; this never happens for the released seeds.

## CLI

```
generate.py --profile PROFILE --out DIR [--scale K] [--count N]
            [--seed SEED] [--fingerprint-noise EPS]
            [--utilization-cap CAP]
            [--length-scaling {scale,source}]
```

- `--profile`            the fingerprint JSON to sample from
- `--scale`              bucket scale $k$ (instance size is $100k$ tasks; default 1)
- `--count`              number of instances to emit (default 1)
- `--seed`               RNG seed (reproducibility; default 42)
- `--out`                output directory (one JSON per instance)
- `--fingerprint-noise`  $\varepsilon$, default $0.10$; applies
                         $\pm \varepsilon$ multiplicative perturbation of
                         the WCET (clamped to the per-period envelope)
                         and of the per-chain $\rho$; `wcet_min` and
                         `acet` are drawn from their ranges without it
- `--utilization-cap`    total-utilization cap as a fraction of the
                         core count (default $0.95$)
- `--length-scaling`     how chain length $L$ scales with `--scale`.
                         `scale` (default) multiplies the source
                         per-bucket $L$ draws by $k$, so a $k\times$
                         larger platform integrates $k\times$ more
                         end-to-end functionality per chain.  `source`
                         keeps $L$ identically distributed to the
                         source use-case regardless of `--scale`.

## Profile schema (summary)

`fingerprints/<USECASE>/profile.json` is a fingerprint organised
on four axes (global / per-period / variable-period / chain-axis).  It carries
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
