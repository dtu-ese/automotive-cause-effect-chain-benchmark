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
@misc{craciunas_2026_20637979,
  author       = {Craciunas, Silviu and
                  Becker, Matthias and
                  Pop, Paul},
  title        = {Automotive Cause-Effect Chains in the Wild: A
                   Real-World Industrial Benchmark and Generator
                  },
  month        = jun,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v1},
  doi          = {10.5281/zenodo.20637979},
  url          = {https://doi.org/10.5281/zenodo.20637979},
  note         = {Presented at the 15th International Real-Time Systems Open Problems Seminar (RTSOPS 2026). GitHub repository: https://github.com/dtu-ese/automotive-cause-effect-chain-benchmark}
}
```

## Layout

```
artifact/
├── README.md                           — you are here
├── LICENSE                             — MIT
├── fingerprints/
│   ├── SCHEMA.md                       — fingerprint schema
│   ├── TrustMotion/profile.json        — TrustMotion ADAS controller fingerprint
│   └── WATERS-Bosch/profile.json       — WATERS-inspired Bosch engine-controller fingerprint
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
- **`WATERS-Bosch`** — a WATERS-inspired engine-controller
  fingerprint (curated from the WATERS-2015 Bosch task model and
  the WATERS-2017 Industrial Challenge chain corpus).  Included as
  a contrasting reference that exercises the schema's optional
  WATERS extensions (Weibull execution-time distributions,
  angle-synchronous activations, per-task BCET/ACET
  measurements).  The generator handles it identically to TrustMotion;
  no synthetic data is shipped — generate as many instances as
  you need via `./generate_all.sh -u WATERS-Bosch`.

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

- `platform`: CPUs and cores.  No scheduling policy is declared —
  the benchmark is policy-agnostic; analyzers and schedulers are
  free to apply table-driven, fixed-priority, EDF, or Logical
  Execution Time semantics as they see fit.
- `tasks`: array of tasks.  Each task carries `name` (e.g.
  `t_0042`), `period`, `deadline`, `wcet`, `bcet`,
  `earliest_activation`, `max_jitter`, a boolean `pinned` flag,
  and a `placement` object whose `cpu_id` is set (workload
  constraint) and whose `core_id` is `null`.  The execution-time
  fields follow the WATERS convention (Kramer et al. 2015): every
  task carries a per-instance worst-case `wcet` and a per-instance
  best-case `bcet`, with `bcet = 0` reserved as the sentinel for
  "the source did not measure it".  Four further optional fields
  may appear when the fingerprint populates them: `wcet_min` (lower
  bound of a per-slot WCET envelope), `acet` (per-instance
  average-case time), `exec_distribution` (per-instance
  execution-time distribution shape — Weibull α/β or empirical
  PMF), and `activation` (one of `"periodic"`, `"angle-sync"`,
  or `"sporadic"`; absent ⇔ `"periodic"`, the only class TrustMotion
  uses).  The released schema defers all core assignment to the
  evaluator — an analyzer or scheduler must run a best-fit
  allocation across the CPU's cores before evaluation; the
  generator guarantees that a feasible allocation always exists.
  The `pinned` flag preserves the source's workload property of
  whether the architect bound this task to a specific core
  (`pinned = true`) or left it free for the OS to migrate
  (`pinned = false`); it does not change the released-schema
  contract on `core_id`.  All time values are integers in
  microseconds.
- `chains`: array of cause-effect chains.  Each chain has a `name`
  (e.g. `Chain0`), a `semantic` (`reaction-delay`), an end-to-end
  latency `budget` (integer microseconds), an importance `priority`
  (float weight in `[0, 1]`), an optional ISO 26262 `asil` label
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
feasibility conditions (per-core $\Sigma u \le 1$ under a best-fit
allocation, total $\Sigma u \le$ total cores, chain task-reference
integrity, chain budget $\ge$ sum-of-WCETs lower bound).  Both
exit `0` on success and `1` on failure.

## License

The artifact (code, fingerprints, and synthetic data) is released
under the MIT License.  See `LICENSE` for the full text.
