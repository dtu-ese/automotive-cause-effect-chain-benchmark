#!/usr/bin/env python3
"""generator: consume a fingerprint (per
``data/fingerprints/SCHEMA.md``) and emit synthetic task-set JSON
instances.  Output schema is the released instance format so
``check.py`` and any downstream analyzer consumes the output
unchanged.

Run::

    python3 generate.py --profile PROFILE.json --scale K \\
                        --count N --seed S --out DIR
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Sampling primitives
# ---------------------------------------------------------------------------

def weighted_choice(rng: random.Random, items: list, weights: list):
    """Pick one of `items` weighted by `weights` (need not sum to 1)."""
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.random() * total
    cum = 0.0
    for x, w in zip(items, weights):
        cum += w
        if r < cum:
            return x
    return items[-1]


def sample_histogram_bin(rng: random.Random, bins: list,
                         value_key: str = 'wcet_bin',
                         count_key: str = 'count') -> float:
    """Pick a bin weighted by count, then sample uniformly within it."""
    weights = [b[count_key] for b in bins]
    bin = weighted_choice(rng, bins, weights)
    lo, hi = bin[value_key]
    return rng.uniform(lo, hi)


def sample_dt_ratio(rng: random.Random, hist: list) -> float:
    weights = [b['count'] for b in hist]
    if sum(weights) <= 0:
        return 1.0
    bin = weighted_choice(rng, hist, weights)
    lo, hi = bin['bin']
    # Top bin [1.0, 1.0001] is the implicit-deadline sentinel and
    # always resolves to 1.0; every other bin (incl. [0.9, 1.0]) is
    # sampled uniformly inside its range.  Using `>` (not `>=`) here
    # keeps the bin just below 1.0 from collapsing to exactly 1.0.
    if hi > 1.0:
        return 1.0
    return rng.uniform(lo, hi)


def sample_quartile_bands(rng: random.Random, bands: list) -> float:
    """`bands` = [p0, p25, p50, p75, p100].  Pick a quartile uniformly,
    then a value uniformly within it."""
    q = rng.randrange(4)
    return rng.uniform(bands[q], bands[q + 1])


def sample_decile_edges(rng: random.Random, edges: list) -> float:
    """`edges` = 11 percentile edges [p0, p10, ..., p100]."""
    d = rng.randrange(10)
    return rng.uniform(edges[d], edges[d + 1])


# ---------------------------------------------------------------------------
# Per-instance assembly
# ---------------------------------------------------------------------------

def build_platform(profile: dict, scale: int) -> list[dict]:
    """Replicate the CPU list k times and assign global CPU ids + core ids."""
    cpus = []
    cpu_id = 0
    for _ in range(scale):
        for spec in profile['platform']:
            cores = [{'id': i} for i in range(spec['cores'])]
            cpus.append({'id': cpu_id, 'cores': cores})
            cpu_id += 1
    return cpus


def _sample_exec(rng: random.Random, entry: dict, eps: float) -> dict:
    """Sample WCET (plus optional BCET / ACET / wcet_min / exec_distribution)
    from a per-period or variable_period entry.

    Two forms of ``entry['wcet']`` are recognised:

    - **histogram** — the fitter binned the source pool's per-task
      WCETs.  Returns ``{wcet, bcet=0}``; histogram-form fingerprints
      do not carry per-task BCET/ACET landmarks.
    - **aggregate** — Kramer-2015 / WATERS-style aggregate with a
      ``wcet_range`` envelope and optionally ``bcet_range``,
      ``acet_range``, ``wcet_min_range``, and an ``exec_distribution``
      shape (Weibull α/β or empirical PMF).  Sampled fields propagate
      through to the released task so analyzers that consume them
      (Bi 22, Becker 17, …) see the WATERS execution-time semantics.

    The WCET is finally clamped to ``entry['wcet_envelope']`` (the
    strict per-period [min, max] of the source pool) and a ±ε
    multiplicative jitter is applied; BCET/ACET are clamped to
    preserve the BCET ≤ ACET ≤ WCET invariant.
    """
    wcet_def = entry['wcet']
    env_lo, env_hi = entry['wcet_envelope']
    hist = wcet_def.get('histogram')

    if hist is not None:
        wcet_raw = sample_histogram_bin(rng, hist)
        wcet = int(round(max(1, wcet_raw * (1 + rng.uniform(-eps, +eps)))))
        wcet = max(env_lo, min(env_hi, wcet))
        return {'wcet': wcet, 'bcet': 0}

    agg = wcet_def['aggregate']
    wmin, wmax = agg['wcet_range']
    dist = agg.get('distribution')

    # Sample raw WCET from the declared distribution shape, falling back
    # to a uniform draw on the wcet_range when no shape is given.
    if dist and dist.get('kind') == 'weibull':
        # Schema convention: alpha = shape, beta = scale.  Python's
        # random.weibullvariate(alpha, beta) takes (scale, shape), so
        # we pass them in swapped order.
        shape = float(dist['alpha'])
        scale = float(dist['beta'])
        wcet_raw = rng.weibullvariate(scale, shape)
    elif dist and dist.get('kind') == 'empirical':
        pmf = dist.get('values', [])
        if pmf:
            wcet_raw = weighted_choice(rng,
                                       [v['value'] for v in pmf],
                                       [v['share'] for v in pmf])
        else:
            wcet_raw = rng.uniform(wmin, wmax)
    else:
        wcet_raw = rng.uniform(wmin, wmax)

    wcet = int(round(max(1, wcet_raw * (1 + rng.uniform(-eps, +eps)))))
    wcet = max(env_lo, min(env_hi, wcet))

    out: dict = {'wcet': wcet}

    # BCET: per-task lower bound on execution time.  When the source
    # records a bcet_range we sample uniformly from it; otherwise the
    # released sentinel `bcet = 0` ("source did not measure") applies.
    if 'bcet_range' in agg:
        bmin, bmax = agg['bcet_range']
        bcet = max(0, int(round(rng.uniform(bmin, bmax))))
        out['bcet'] = min(bcet, wcet)
    else:
        out['bcet'] = 0

    # ACET: per-task average-case execution time (optional WATERS extension).
    if 'acet_range' in agg:
        amin, amax = agg['acet_range']
        acet = int(round(rng.uniform(amin, amax)))
        out['acet'] = max(out['bcet'], min(wcet, acet))

    # WCET_min: lower bound of a per-slot WCET envelope (optional).
    if 'wcet_min_range' in agg:
        wmlo, wmhi = agg['wcet_min_range']
        wcet_min = max(0, int(round(rng.uniform(wmlo, wmhi))))
        out['wcet_min'] = min(wcet_min, wcet)

    # exec_distribution: per-task copy of the shape declaration.
    if dist:
        out['exec_distribution'] = dict(dist)

    return out


def _build_task_from_entry(rng: random.Random, entry: dict, T: int,
                           idx: int, eps: float,
                           activation: str | None = None) -> dict:
    """Common per-task assembly used by both the static-period sampler
    and the variable-period sampler."""
    exec_fields = _sample_exec(rng, entry, eps)
    wcet = exec_fields['wcet']

    # D/T -> D
    dt_hist = entry.get('deadline_ratio_hist', [{'bin': [1.0, 1.0001], 'count': 100}])
    dt = sample_dt_ratio(rng, dt_hist)
    D = min(T, max(wcet, int(round(dt * T))))

    # Per-entry EA (optional).
    EA = 0
    ea_dist = entry.get('ea')
    if ea_dist and ea_dist.get('active_bins'):
        if rng.uniform(0, 100) >= ea_dist.get('none', 0):
            bins = ea_dist['active_bins']
            bin = weighted_choice(rng, bins, [b['count'] for b in bins])
            lo, hi = bin['bin']
            EA = int(round(rng.uniform(lo, hi) * T))

    pinned = rng.random() < entry.get('pinning_probability', 0.0)

    vw_dist = entry.get('task_visit_weights',
                        [{'weight': 0, 'share': 100}])
    visit_weight = weighted_choice(rng, [v['weight'] for v in vw_dist],
                                   [v['share'] for v in vw_dist])

    task = {
        'name':                 f't_{idx:04d}',
        'period':               T,
        'deadline':             D,
        'wcet':                 wcet,
        'bcet':                 exec_fields['bcet'],
        'earliest_activation':  EA,
        'max_jitter':           None,    # filled in by apply_global_jitter
        'pinned':               pinned,
        'placement':            {'cpu_id': None, 'core_id': None},
        '_visit_weight':        float(visit_weight),
    }
    # Optional WATERS-style execution-time extensions (only when the
    # fingerprint provides them).
    if 'wcet_min' in exec_fields:
        task['wcet_min'] = exec_fields['wcet_min']
    if 'acet' in exec_fields:
        task['acet'] = exec_fields['acet']
    if 'exec_distribution' in exec_fields:
        task['exec_distribution'] = exec_fields['exec_distribution']
    if activation:
        task['activation'] = activation
    return task


def sample_one_task(rng: random.Random, profile: dict, idx: int,
                    n_cpus: int, eps: float) -> dict:
    """Draw one static-period task from the per_period table."""
    periods = profile['periods_us']
    period_weights = [profile['per_period'][str(p)]['task_share']
                      for p in periods]
    T = weighted_choice(rng, periods, period_weights)
    per = profile['per_period'][str(T)]
    return _build_task_from_entry(rng, per, T, idx, eps, activation=None)


def sample_one_variable_task(rng: random.Random, vp: dict, idx: int,
                             eps: float) -> dict:
    """Draw one variable-period task (angle-sync, sporadic, ...) from a
    single ``variable_period`` entry.  The period is drawn uniformly from
    the entry's ``period_range_us``."""
    lo_T, hi_T = vp['period_range_us']
    T = rng.randint(lo_T, hi_T)
    return _build_task_from_entry(rng, vp, T, idx, eps,
                                  activation=vp.get('activation'))


def apply_global_jitter(rng: random.Random, profile: dict,
                        tasks: list[dict]) -> None:
    jd = profile.get('jitter')
    if not jd or not jd.get('values'):
        return
    none_share = jd.get('none', 100.0)
    val_pairs = jd['values']
    for t in tasks:
        if rng.uniform(0, 100) >= none_share:
            ratio = weighted_choice(rng,
                                    [v[0] for v in val_pairs],
                                    [v[1] for v in val_pairs])
            t['max_jitter'] = int(round(ratio * t['period']))


def assign_cpus(rng: random.Random, tasks: list[dict], cpus: list[dict]) -> bool:
    """Best-fit assign each task to a CPU and a core under u <= 1.
    Returns False if any task fails to fit."""
    # Per-CPU per-core utilisation tracker.
    core_u: dict[tuple[int, int], float] = {}
    for c in cpus:
        for co in c['cores']:
            core_u[(c['id'], co['id'])] = 0.0
    cpu_u: dict[int, float] = {c['id']: 0.0 for c in cpus}
    # CPU load tracker (sum over cores) for "least-loaded CPU" choice.
    cpu_cores = {c['id']: [co['id'] for co in c['cores']] for c in cpus}

    tasks_sorted = sorted(tasks,
                          key=lambda t: (-t['wcet'] / t['period'], t['name']))
    for t in tasks_sorted:
        u = t['wcet'] / t['period']
        # Pick least-loaded CPU first.
        candidates = sorted(cpu_u.keys(), key=lambda cid: cpu_u[cid])
        placed = False
        for cid in candidates:
            # Within the CPU, pick least-loaded core that fits.
            cores = sorted(cpu_cores[cid], key=lambda coid: core_u[(cid, coid)])
            for coid in cores:
                if core_u[(cid, coid)] + u <= 1.0:
                    core_u[(cid, coid)] += u
                    cpu_u[cid] += u
                    t['placement']['cpu_id'] = cid
                    # Released JSON ships core_id = null — keep it that
                    # way; the evaluator re-runs the allocation.
                    t['placement']['core_id'] = None
                    placed = True
                    break
            if placed:
                break
        if not placed:
            return False
    return True


def sample_one_chain(rng: random.Random, profile: dict, tasks_by_period: dict,
                     name_to_task: dict, chain_idx: int, eps: float,
                     length_multiplier: int = 1) -> dict | None:
    chain_cfg = profile['chains']
    buckets = chain_cfg['length_buckets']
    bucket_names = list(buckets.keys())
    bucket_weights = [buckets[b]['share'] for b in bucket_names]

    # Pick bucket, then length.  Under length-scaling mode the
    # source-distributed L is multiplied by the platform-scale factor:
    # at scale S_k a chain that would draw L=10 from the source
    # quartile bands instead produces a length-(k·10) chain, modelling
    # the case where a k× larger platform integrates k× more end-to-end
    # functionality per chain rather than just k× more independent chains.
    bname = weighted_choice(rng, bucket_names, bucket_weights)
    bucket = buckets[bname]
    L_source = max(2, int(round(
        sample_quartile_bands(rng, bucket['length_quartile_bands']))))
    L = L_source * max(1, length_multiplier)

    # Walk bigram for up to L positions.  Per-bucket bigram when
    # present; otherwise inherit the corpus-wide global bigram.
    # The walk excludes End-only continuations from the weighted draw
    # so the chain reaches the requested length whenever the bigram has
    # any non-terminal continuation; if the row offers only End (true
    # natural termination in the source), we accept the shorter chain
    # rather than dropping it — that preserves the source's structural
    # termination semantics.
    bigram = (bucket.get('bigram')
              or profile['chains']['global']['bigram'])
    periods: list[int] = []
    cur = 'Start'
    while len(periods) < L:
        row = bigram.get(cur, {})
        if not row:
            break
        keys = [k for k in row.keys() if k != 'End']
        weights = [row[k] for k in keys]
        if not keys or sum(weights) == 0:
            break  # natural termination: only End is reachable
        nxt = weighted_choice(rng, keys, weights)
        periods.append(int(nxt))
        cur = nxt
    if len(periods) < 2:
        return None

    # Resolve each period to a task.  During the natural walk every
    # task identity is unique within the chain (max-multiplicity 1):
    # this keeps the bigram-driven period sequence and the visit-weight
    # bias intact but suppresses accidental task-identity collisions
    # that would otherwise produce repeats at arbitrary positions.
    # All same-task reuse is introduced *after* the walk, in
    # `_calibrate_repeats`, with the fingerprint marginal as the per-chain
    # Bernoulli probability and uniformly random positions for the pair.
    chain_task_names: list[str] = []
    use_count: dict[str, int] = {}
    max_mult = 1

    def _scaled_weight(visit_weight: float, used: int) -> float:
        if used >= max_mult:
            return 0.0
        # Sqrt-dampened base; floor 0.5 so zero-visit tasks still draw.
        return visit_weight ** 0.5 + 0.5

    for T in periods:
        pool = tasks_by_period.get(T)
        if not pool:
            continue
        weights = [_scaled_weight(t['_visit_weight'],
                                  use_count.get(t['name'], 0))
                   for t in pool]
        if sum(weights) <= 0:
            # Every candidate has hit the multiplicity cap.  Re-filter
            # to candidates still below the cap; if none remain, skip
            # the position rather than admit an over-cap pick.  This
            # is a *hard* multiplicity invariant — the source data
            # never has a task at >2 occurrences in one chain.
            below_cap = [t for t in pool
                         if use_count.get(t['name'], 0) < max_mult]
            if not below_cap:
                continue
            pool = below_cap
            weights = [1.0] * len(below_cap)
        picked = weighted_choice(rng, pool, weights)
        chain_task_names.append(picked['name'])
        use_count[picked['name']] = use_count.get(picked['name'], 0) + 1
    if len(chain_task_names) < 2:
        return None

    # Compute T_th and sum-WCET for the budget step.
    # T_th = Σ_i T_τ_i over all tasks in the chain — the period-only
    # component of Davare's reaction-delay bound Lat ≤ Σ_i (T_i + R_i).
    actual_periods = [name_to_task[n]['period'] for n in chain_task_names]
    T_th = sum(actual_periods)
    sum_wcet = sum(name_to_task[n]['wcet'] for n in chain_task_names)

    rho_grid = profile['chains']['global'].get('rho_grid_us', 5000)
    # Per-bucket ρ deciles when present; otherwise inherit global.
    rho_edges = (bucket.get('rho_decile_edges')
                 or profile['chains']['global']['rho_decile_edges'])
    b = None
    for _ in range(8):
        rho = sample_decile_edges(rng, rho_edges)
        rho_eff = max(0.05, rho * (1.0 + rng.uniform(-eps, +eps)))
        candidate = int(round(rho_eff * T_th / rho_grid)) * rho_grid
        candidate = max(rho_grid, candidate)
        if candidate >= sum_wcet:
            b = candidate
            break
    if b is None:
        b = max(rho_grid, ((sum_wcet + rho_grid - 1) // rho_grid) * rho_grid)

    # ASIL & priority.
    asil_dist = profile['chains']['global'].get('asil', {'none': 100.0})
    asil_keys, asil_w = list(asil_dist.keys()), list(asil_dist.values())
    asil = weighted_choice(rng, asil_keys, asil_w)
    if asil == 'none':
        asil = None

    prio_dist = profile['chains']['global'].get('priority', [])
    if prio_dist:
        priority = weighted_choice(rng,
                                   [p['value'] for p in prio_dist],
                                   [p['share'] for p in prio_dist])
    else:
        priority = 0

    return {
        'name':     f'Chain{chain_idx}',
        'semantic': 'reaction-delay',
        'budget':   b,
        'priority': priority,
        'asil':     asil,
        'tasks':    chain_task_names,
        '_bucket':  bname,    # consumed by _calibrate_repeats; stripped before output
    }


def _calibrate_repeats(rng: random.Random, chains: list[dict],
                       profile: dict) -> None:
    """Same-task reuse: Bernoulli per chain with uniformly-random
    positions.

    For each chain, draw ``Bernoulli(p = same_task_repeat_share)``;
    on success, pick two distinct positions uniformly at random in
    ``[0, L-1]`` and copy the task at the first position into the
    second.  Multiplicity stays at 2 by construction (the natural walk
    in :func:`sample_one_chain` produces all-distinct identities).

    Budget re-check: a forced repeat grows ``Σ_i C_i`` along the chain,
    so the post-edit budget is bumped up to the next ρ-grid step above
    the new sum-WCET whenever the original budget would underflow.
    """
    if not chains:
        return
    target = profile.get('same_task_repeat_share', 0.0)
    if target <= 0:
        return
    rho_grid = profile.get('chains', {}).get('global', {}).get('rho_grid_us', 5000)

    for c in chains:
        if rng.random() >= target:
            continue
        L = len(c['tasks'])
        if L < 2:
            continue
        i, j = sorted(rng.sample(range(L), 2))
        c['tasks'][j] = c['tasks'][i]
        n2t = c.get('_name_to_task')
        if n2t is not None:
            new_sum_wcet = sum(n2t[n]['wcet'] for n in c['tasks'])
            if c['budget'] < new_sum_wcet:
                c['budget'] = ((new_sum_wcet + rho_grid - 1) // rho_grid) * rho_grid


def generate_one(rng: random.Random, profile: dict, scale: int,
                 eps: float, idx: int,
                 max_retries: int = 50,
                 utilization_cap: float = 0.95,
                 length_scaling: str = 'scale') -> dict | None:
    """Generate one feasible synthetic instance."""
    for attempt in range(max_retries):
        cpus = build_platform(profile, scale)
        n_cpus = len(cpus)
        n_tasks = 100 * scale

        # Activation-class-first sampling: every task is drawn from a
        # single unified 100·k-task budget.  Per draw, decide whether
        # the task is static-period (sampled from per_period[T]) or
        # variable-period (sampled from one of the variable_period
        # entries), weighted by per-entry task_share.  This keeps the
        # released instance size exactly `n_tasks` regardless of the
        # variable-period share.
        var_entries = profile.get('variable_period', []) or []
        var_shares  = [vp.get('task_share', 0.0) for vp in var_entries]
        var_total   = sum(var_shares)

        tasks: list[dict] = []
        for i in range(n_tasks):
            if var_entries and rng.uniform(0, 100) < var_total:
                vp = weighted_choice(rng, var_entries, var_shares)
                tasks.append(sample_one_variable_task(rng, vp, i, eps))
            else:
                tasks.append(sample_one_task(rng, profile, i, n_cpus, eps))

        # Global jitter pass.
        apply_global_jitter(rng, profile, tasks)

        # Best-fit allocate and verify total-cap safety margin.
        if not assign_cpus(rng, tasks, cpus):
            continue
        total_u = sum(t['wcet'] / t['period'] for t in tasks)
        total_cores = sum(len(c['cores']) for c in cpus)
        if total_u > utilization_cap * total_cores:
            continue

        # Reset core_id (released schema requirement).
        for t in tasks:
            t['placement']['core_id'] = None

        # Sample chains.
        tasks_by_period: dict[int, list] = defaultdict(list)
        for t in tasks:
            tasks_by_period[t['period']].append(t)
        name_to_task = {t['name']: t for t in tasks}

        # When the source had no chains (chain_count_per_100_tasks == 0)
        # the released instance carries an empty chain list.
        n_chains = profile.get('chain_count_per_100_tasks', 0) * scale
        # Chain-length scaling: default is to multiply the per-bucket L
        # draws by `scale` so a k× larger platform integrates k× more
        # end-to-end functionality per chain.  `length_scaling='source'`
        # opts out and keeps L identically distributed to the source.
        length_mult = scale if length_scaling == 'scale' else 1
        chains: list[dict] = []
        for ci in range(n_chains):
            c = sample_one_chain(rng, profile, tasks_by_period,
                                 name_to_task, ci, eps,
                                 length_multiplier=length_mult)
            if c is not None:
                # Stamp the name-to-task map so _calibrate_repeats can
                # re-check budget feasibility after editing the chain.
                c['_name_to_task'] = name_to_task
                chains.append(c)

        # Same-task-reuse: Bernoulli marginal + uniform-random positions.
        _calibrate_repeats(rng, chains, profile)

        # Strip internal sampling fields before serialising.
        for c in chains:
            c.pop('_bucket', None)
            c.pop('_name_to_task', None)
        for t in tasks:
            t.pop('_visit_weight', None)

        return {
            'metadata': {
                'time_unit': 'us',
                'description': (
                    f'Synthetic cause-effect task-set instance #{idx} '
                    f'at scale {scale} (pure-sampling, '
                    f'fingerprint-noise={eps}).'
                ),
            },
            'platform': {'cpus': cpus},
            'tasks': tasks,
            'chains': chains,
        }
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', required=True)
    ap.add_argument('--scale', type=int, default=1,
                    help='Bucket scale (instance has 100*scale tasks).')
    ap.add_argument('--count', type=int, default=1)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--out', required=True)
    ap.add_argument('--fingerprint-noise', type=float, default=0.10,
                    help='ε: ±ε multiplicative jitter on WCET and ρ.')
    ap.add_argument('--utilization-cap', type=float, default=0.95,
                    help='Per-instance total-utilization cap as a '
                         'fraction of total_cores (default 0.95, i.e. '
                         'a 5%% safety margin).  An instance with '
                         'Σu > cap·n_cores is retried.')
    ap.add_argument('--length-scaling', choices=['scale', 'source'],
                    default='scale',
                    help='How chain length L scales with --scale.  '
                         '"scale" (default) multiplies the per-bucket '
                         'L draws by --scale, so a k× larger platform '
                         'integrates k× more end-to-end functionality '
                         'per chain.  "source" keeps L identically '
                         'distributed to the source use-case '
                         'regardless of --scale.')
    args = ap.parse_args()

    profile_path = Path(args.profile)
    if not profile_path.is_file():
        print(f'# profile not found: {args.profile}', file=sys.stderr)
        sys.exit(1)
    with profile_path.open() as f:
        profile = json.load(f)
    if profile.get('format_version') != '1.0':
        print(f'# WARNING: profile format_version is '
              f'{profile.get("format_version")!r}, expected "1.0".',
              file=sys.stderr)
    # Sanity: chains.global must carry the fallback bigram + ρ deciles
    # so sparse buckets can inherit them.
    cg = profile.get('chains', {}).get('global', {})
    if 'bigram' not in cg or 'rho_decile_edges' not in cg:
        print('# WARNING: chains.global missing bigram or rho_decile_edges; '
              'sparse buckets will fail to sample.', file=sys.stderr)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    written = 0
    for i in range(args.count):
        inst = generate_one(rng, profile, args.scale,
                            args.fingerprint_noise, i,
                            utilization_cap=args.utilization_cap,
                            length_scaling=args.length_scaling)
        if inst is None:
            print(f'# instance #{i}: gave up after retries (infeasible)',
                  file=sys.stderr)
            continue
        (out / f'instance-{args.scale}x-{i:04d}.json').write_text(
            json.dumps(inst, indent=2))
        written += 1
    print(f'# wrote {written}/{args.count} instances to {out}')


if __name__ == '__main__':
    main()
