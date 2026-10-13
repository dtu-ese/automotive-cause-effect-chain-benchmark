# Cause-Effect Chain Benchmark — Release Artifact

This is the released benchmark and generator.  It contains the statistical
fingerprint of a real-world automotive ADAS controller, 500
pre-generated task-set instances synthesised from that fingerprint,
and a small Python generator that lets you regenerate the data or
sample fresh instances of your own.

## How to cite

If you use this benchmark or generator, please cite the Zenodo release:

Craciunas, Silviu; Becker, Matthias; Pop, Paul. *Automotive Cause-Effect Chains in the Wild: A Real-World Industrial Benchmark and Generator*. Zenodo, v1, June 2026. https://doi.org/10.5281/zenodo.20637979. Presented at the 15th International Real-Time Systems Open Problems Seminar (RTSOPS 2026).

```bibtex
@misc{CraciunasRTSOPS26,
  author       = {Craciunas, Silviu S. and Becker, Matthias and Pop, Paul},
  title        = {Automotive Cause-Effect Chains in the Wild: A Real-World Industrial Benchmark and Generator},
  month        = jun,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v1},
  doi          = {10.5281/zenodo.20637979},
  url          = {https://doi.org/10.5281/zenodo.20637979},
  note         = {Presented at the 15th International Real-Time Systems Open Problems Seminar (RTSOPS 2026)}
}
```

## Version

This is **benchmark version 2.0**, released 12 October 2026.  The
version is recorded in the `VERSION` file and in every instance's
`metadata.benchmark_version`; the companion paper's version 2.0
describes this release.  Version 1.0 instances were not schedulable under the
fixed-priority setup used in the companion paper's evaluation; see
[Changes in 2.0](#changes-in-20) below.  Version 1.0 remains
available at commit `eaac10c`.


## Layout

```
artifact/
├── README.md                           — you are here
├── VERSION                             — benchmark release version (2.0)
├── LICENSE                             — MIT
├── fingerprints/
│   ├── SCHEMA.md                       — fingerprint schema
│   ├── TrustMotion/profile.json        — TrustMotion ADAS controller fingerprint
│   └── WATERS-Bosch/profile.json       — Bosch engine-controller fingerprint (Kramer et al., WATERS 2015)
├── data/
│   └── TrustMotion/{100..500}pct/instance-Nx-NNNN.json    (100 files per bucket)
└── generator/
    ├── generate.py                     — pure-sampling generator
    ├── check.py                        — per-instance sanity checker
    ├── validate.py                     — fingerprint schema validator
    ├── generate_all.sh                 — regenerate every bucket
    └── README.md                       — generator details
```

Two anonymised fingerprints ship with this release:

- **`TrustMotion`** — derived from a TrustMotion integrated ADAS controller.
  The source workload is 102 periodic tasks on a 3-CPU, 9-core
  platform (3/2/4 cores per CPU); the chain corpus contains 39
  cause-effect chains, of which 74% contain at least one
  repeated task reference.  All companion-paper evaluation,
  tables, and figures are computed on this fingerprint.  The
  synthetic dataset under `data/TrustMotion/` is drawn from it.
- **`WATERS-Bosch`** — an engine-controller fingerprint built from
  the published characteristics of the Bosch application of Kramer
  et al., "Real World Automotive Benchmarks for Free" (WATERS 2015):
  period shares (Table III), runnable ACETs with a Weibull shape
  (Table IV), BCET/WCET factors (Table V), implicit deadlines and
  half-period deadlines for angle-synchronous runnables (Sec. II.D),
  and the cause-effect chain model (Sec. IV.E, Tables VI–VII:
  1–3 activation patterns per chain, 2–5 runnables per pattern,
  budget = sum of the patterns' periods).  Each task models one
  runnable.  Modelling choices not fixed by the paper: 4 CPUs with
  one core, angle-synchronous periods of 3–30 ms, chains over
  time-synchronous patterns only, no interrupt load.  Included as a
  contrasting reference that exercises the schema's optional WATERS
  extensions; no synthetic data is shipped — generate as many
  instances as you need via `./generate_all.sh -u WATERS-Bosch`.

Everything needed to use the benchmark for schedule synthesis or
end-to-end latency analysis is here.  Python 3 with no external
dependencies; no proprietary source data.

## Benchmark at a glance

Each `data/TrustMotion/<bucket>/instance-<scale>x-<idx>.json` is a
self-describing multi-core periodic task set with cause-effect
chains.  Bucket `100pct` has 100 tasks per instance, `500pct`
has 500.  The platform scales linearly with the task count:
scale $S_k$ replicates the source's three-CPU compute topology
$k$ times ($3k$ CPUs / $9k$ cores total).  Cause-effect chains
scale on the same axis by default: a chain that would draw
length $L_{\mathrm{src}}$ from the source distribution produces a
length-$(k \cdot L_{\mathrm{src}})$ chain at scale $S_k$, so a
$k\times$ larger platform integrates $k\times$ more end-to-end
functionality per chain rather than only $k\times$ more
independent chains.  Pass `--length-scaling source` to the
generator to opt out and keep $L = L_{\mathrm{src}}$ regardless
of scale.

Per instance:

- `platform`: CPUs and cores.  No scheduling policy is mandated —
  the benchmark is policy-agnostic; analyzers and schedulers are
  free to apply table-driven, fixed-priority, EDF, or Logical
  Execution Time semantics as they see fit.
- `tasks`: array of tasks.  Each task carries `name` (e.g.
  `t_0042`), `period`, `deadline`, `wcet`, `bcet`,
  `earliest_activation`, `max_jitter`, a boolean `pinned` flag,
  and a `placement` object with a `cpu_id` and a `core_id`.  The execution-time
  fields follow the WATERS convention (Kramer et al. 2015): every
  task carries a per-instance worst-case `wcet` and a per-instance
  best-case `bcet`; this benchmark reserves `bcet = 0` as the
  sentinel for "the source did not measure it" (then no safe positive
  lower bound on the execution time is available).  Four further optional fields
  may appear when the fingerprint populates them: `wcet_min` (lower
  bound of a per-slot WCET envelope), `acet` (per-instance
  average-case time), `exec_distribution` (per-instance
  execution-time distribution shape — Weibull α/β or empirical
  PMF), and `activation` (`"angle_sync"` or `"sporadic"`;
  absent ⇔ periodic, the only class TrustMotion uses).  `earliest_activation` is the release offset: jobs are
  released at `earliest_activation + k·period`, possibly delayed
  by up to `max_jitter` (release jitter), and `deadline` is
  relative to the nominal release (D ≤ T for every task).  This is
  the interpretation used by the reference schedule; the source's
  start-jitter / TT-slot semantics are not checked.  In the source,
  the deadline is measured from the start of the period, so
  [EA, D] is an execution window (on the time-triggered CPUs a slot
  barely larger than the WCET).  The fingerprint stores EA and D/T
  independently, so the synthetic deadlines are looser than these
  windows for tasks with EA > 0.
  `placement` is a **reference allocation**: with it,
  every task meets its deadline under partitioned preemptive
  fixed-priority scheduling with deadline-monotonic priorities
  (ties broken by period, then task name), release jitter up to
  `max_jitter`, and arbitrary release offsets, as verified by
  response-time analysis (`generator/check.py`, check 6).  Every
  task has positive slack (`wcet < deadline`).  It is a
  witness, not a constraint: researchers may re-allocate tasks or
  use another policy.  The `pinned` flag preserves the source's
  workload property of whether the architect bound this task to a
  specific core (`pinned = true`) or left it free for the OS to
  migrate (`pinned = false`).  All time values are integers in
  microseconds.
- `chains`: array of cause-effect chains.  Each chain has a `name`
  (e.g. `Chain0`), a `semantic` (`reaction-delay`), an end-to-end
  latency `budget` (integer microseconds; it can be read from the
  start of the first task's job, i.e. as a maximum reduced reaction
  time, or from the external event, i.e. as a maximum reaction time,
  which additionally contains the first task's sampling delay; see
  the companion paper), an importance `priority`
  (weight in `[0, 1]`; `0` when the fingerprint has no priority histogram), an optional ISO 26262 `asil` label
  (`"B"`, `"C"`, or `null`), and an ordered list of `tasks` (task
  names through which data propagates; tasks may repeat within a
  chain).

The full statistical fingerprint, the synthesis pipeline, and the
released-bucket characteristics are documented in the companion
paper (published separately).

## Regenerating the data

The fingerprint plus the generator is enough to reproduce the
pre-generated instances exactly, or to sample fresh instances at
different scales or with different seeds:

```bash
cd generator
./generate_all.sh                          # regenerate TrustMotion (default)
./generate_all.sh -u WATERS-Bosch          # sample instances from WATERS-Bosch
./generate_all.sh -u TrustMotion -n 30          # smaller dataset
./generate_all.sh -s 100                   # different seed base
```

See `generator/README.md` for the per-knob details.

The companion paper gives the long form.

## Validating fingerprints and instances

Two helper scripts ship alongside the generator:

```bash
# Schema-validate every shipped fingerprint
python3 generator/validate.py fingerprints/*/profile.json

# Sanity-check every shipped synthetic instance
python3 generator/check.py data/TrustMotion/
```

`validate.py` runs the invariants declared in
`fingerprints/SCHEMA.md`.  `check.py` runs four necessary
feasibility conditions (per-core $\Sigma u \le 1$, total
$\Sigma u \le$ total cores, chain task-reference integrity, chain
budget $\ge$ sum-of-WCETs lower bound), checks
`wcet < deadline <= period` and unique task names for every task,
and runs a jitter-aware deadline-monotonic response-time analysis
on every core of the reference allocation (R ≤ D for every task;
required for version 2.0 instances).  Both exit `0` on success and
`1` on failure.

## Changes in 2.0

Version 1.0 instances satisfied per-core $\Sigma u \le 1$ but not
fixed-priority schedulability: under the deadline-monotonic setup
of the companion paper's evaluation, all 500 instances had tasks
whose response time exceeded their deadline.  Version 2.0 is a new
sample (same seeds) with three changes to how it is drawn:

- **Chain statistics count compute tasks only.**  The version 1.0
  fingerprint counted the bus-message markers (`PDU_IN`/`PDU_OUT`) in
  chain lengths and period transitions but not in the budget ratio ρ,
  and the generator turned those positions into extra compute tasks,
  lengthening chains by about 1.6 tasks and budgets by about 30 % on
  average.
- **Two chain-runnable mappings corrected.**  The ASIL-B receive comm
  runnable now maps to its own component's task, and a 40 ms
  runnable missing from the schedule files becomes
  a 40 ms task of its own (it was mapped to a 20 ms task of another
  component, creating a spurious repeat).
- **Per-instance random streams.**  Each instance draws its tasks and
  its chains from two separate streams seeded by (seed, scale,
  index), so every instance is reproducible on its own and a change
  to the chain model does not change the tasks.

Each instance then passes a deterministic post-pass in
`generator/generate.py`
(`make_schedulable`):

1. **Positive slack.**  WCET and D/T were sampled independently and
   a deadline shorter than the WCET was clamped up to the WCET,
   giving tasks with `wcet = deadline`.  The deadline of every such
   task (about 3 % of tasks) is redrawn from its period's D/T histogram,
   restricted to deadlines above the WCET, with a separate random
   stream per task.  
2. **Admissible jitter.**  Jitter was sampled as a fraction of the
   period independently of the deadline, so some tasks had
   `max_jitter + wcet > deadline` and could not be guaranteed to meet their
   deadline.  Such a task's jitter is lowered to the largest
   fingerprint jitter ratio that still fits (or removed).
3. **WCET scaling.**  All execution times of an instance
   (`wcet`, `bcet`, `acet`, `wcet_min`) are scaled (and floored) by the largest α on the grid
   {1, 0.995, 0.990, …} for which the placement heuristic of step 4
   places every task schedulably.  α is recorded in
   `metadata.wcet_scaling_alpha` (median 1.000, minimum 0.770; 54 % of the instances need none), so the original
   WCETs can be recovered approximately as `wcet / α`.
4. **Reference allocation.**  The resulting placement
   (`cpu_id`, `core_id`) is shipped with every task.  The CPU
   assignment is part of it and generally differs from version 1.0,
   whose `cpu_id` came from a utilization-only pre-check.

The `WATERS-Bosch` fingerprint was rebuilt from the published tables
of Kramer et al. (WATERS 2015); the version 1.0 fingerprint's period
mix, execution times, angle-synchronous deadlines and chains did not
match them.  The generator gained the corresponding execution-time
model (factor variant of the aggregate form) and chain model
(`chains.model = "waters2015"`), see `fingerprints/SCHEMA.md`.  The
TrustMotion fingerprint and instances are not affected.

## License

The artifact (code, fingerprints, and synthetic data) is released
under the MIT License.  See `LICENSE` for the full text.
