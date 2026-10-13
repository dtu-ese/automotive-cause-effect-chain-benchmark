#!/usr/bin/env python3
r"""Sanity-check generated instances for trivial infeasibility.

Checks 1-4 are necessary conditions for *any* scheduling policy.
Failing any of them means the instance is unusable for schedulability
or end-to-end latency research.  Check 5 requires constrained
deadlines with positive slack, and check 6 verifies the reference
schedule shipped with every released instance (benchmark v2.0):

  1. Per-core utilisation upper bound -- every core's
     sum_{t on it} wcet/period must be <= 1.  Tasks with an explicit
     core_id are counted on that core; tasks with core_id=null (v1
     instances) are first allocated within their CPU, heaviest first,
     each to the least-loaded core that fits; tasks with cpu_id=null form a "flexible" pool that is
     reported separately.

  2. Total-capacity upper bound -- sum_t wcet/period across the whole
     instance (pinned + flexible) must be <= the number of cores in
     the platform.  Failing this means no allocation of the flex
     pool can avoid overload.

  3. Chain reference integrity -- every task name in each chain's
     ordered list must resolve to a task in the instance's task array.

  4. Chain budget feasibility -- if the chain declares a positive
     end-to-end budget, the sum of WCETs along the chain (a lower
     bound on the worst-case latency measured from the start of the
     first task) must not exceed it.  Measured from the external
     event (maximum reaction time), T_1 + sum of WCETs is a lower
     bound, which this check does not test.

  5. Constrained deadline with positive slack -- every task must have
     0 < wcet < deadline <= period.  Task names must be unique (chains and
     check 6 refer to tasks by name).

  6. Reference-schedule feasibility -- when tasks carry a `core_id`
     (all of them must; required for benchmark v2.0), each core is scheduled partitioned preemptive
     fixed-priority with deadline-monotonic priorities (ties broken
     by period, then task name) and every task must satisfy R <= D,
     where R is the jitter-aware worst-case response time
     R_i = J_i + w_i,  w_i = C_i + sum_{j in hp(i)} ceil((w_i + J_j)/T_j) C_j.
     `earliest_activation` is the release offset and `max_jitter` the
     release jitter; deadlines are relative to the nominal release.
     Offsets are ignored, which is safe: the analysis considers the
     critical instant (all higher-priority tasks released together
     with their maximum jitter), which bounds any offset pattern.
     Valid for D <= T, which check 5 ensures.

Passing checks 1-5 does not prove feasibility; passing check 6 proves
the instance is schedulable under the reference allocation and
priority assignment.

Usage:
    python3 check.py instance.json
    python3 check.py /path/to/folder/        # recursive over *.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from fractions import Fraction


def dm_key(t: dict) -> tuple:
    """Deadline-monotonic priority order (smaller = higher priority)."""
    return (t['deadline'], t['period'], t['name'])


def response_times(core_tasks: list[dict]) -> dict[str, int | None]:
    """Jitter-aware response-time analysis for one core under DM
    priorities.  Returns name -> R (None when R exceeds the deadline;
    the iteration stops there because the task is unschedulable)."""
    ordered = sorted(core_tasks, key=dm_key)
    out: dict[str, int | None] = {}
    for i, t in enumerate(ordered):
        C, D, J = t['wcet'], t['deadline'], t.get('max_jitter') or 0
        hp = ordered[:i]
        w = C
        while True:
            nxt = C + sum(-(-(w + (h.get('max_jitter') or 0)) // h['period'])
                          * h['wcet'] for h in hp)
            if nxt + J > D:
                w = None
                break
            if nxt == w:
                break
            w = nxt
        out[t['name']] = None if w is None else w + J
    return out


def core_schedulable(core_tasks: list[dict]) -> bool:
    return all(r is not None for r in response_times(core_tasks).values())


def check_instance(d: dict) -> tuple[list[str], dict]:
    """Return (issues, stats). issues is empty iff the instance passes
    all checks; stats reports per-check summary metrics."""
    issues: list[str] = []
    stats: dict = {
        'tasks':           len(d.get('tasks', [])),
        'chains':          len(d.get('chains', [])),
        'cores':           sum(len(c['cores']) for c in d['platform']['cpus']),
        'max_core_util':   0.0,
        'flex_util':       0.0,
        'chains_infeasible_by_wcet_sum': 0,
        'deadline_misses': None,
        'rta_skipped':     False,
    }

    # ------------------------------------------------------------------
    # Check 1: per-core utilisation
    #
    # Tasks ship with core_id either populated (the v2.0 reference
    # allocation) or null (v1.0 instances).  We validate explicit
    # placements against their stated core, and we allocate only the
    # null-core tasks within their stated CPU.
    # ------------------------------------------------------------------
    cores_per_cpu: dict[int, list[int]] = {
        cpu['id']: [c['id'] for c in cpu['cores']]
        for cpu in d['platform']['cpus']
    }
    # Exact rational utilizations: float summation can report a core at
    # exactly u = 1 as 1.0000000000000002.
    util_by_core: dict[tuple[int, int], Fraction] = defaultdict(Fraction)
    flex_util = Fraction(0)    # tasks whose cpu_id is null
    explicit = []       # (name, cpu_id, core_id, u)
    needs_bfd = []      # (name, cpu_id, u)
    for t in d.get('tasks', []):
        p = t.get('placement', {})
        period = t.get('period')
        wcet   = t.get('wcet')
        if not period or not wcet:
            continue
        u = Fraction(wcet, period)
        cpu_id  = p.get('cpu_id')
        core_id = p.get('core_id')
        if cpu_id is None:
            flex_util += u
        elif core_id is not None:
            explicit.append((t['name'], cpu_id, core_id, u))
        else:
            needs_bfd.append((t['name'], cpu_id, u))
    # 1a. Validate explicit pinnings against the named cores.
    for name, cpu_id, core_id, u in explicit:
        cands = cores_per_cpu.get(cpu_id, [])
        if not cands:
            issues.append(f"task {name!r}: cpu_id={cpu_id} has no cores")
            continue
        if core_id not in cands:
            issues.append(
                f"task {name!r}: core_id={core_id} not in cpu_id={cpu_id}'s "
                f"core list {cands}"
            )
            continue
        util_by_core[(cpu_id, core_id)] += u
    # 1b. Allocate the cpu-pinned, core-flex tasks to the least-loaded
    # core that fits, respecting the u already accumulated by the
    # explicit pinnings (heaviest-first, ties by name).
    needs_bfd.sort(key=lambda x: (-x[2], x[0]))
    for name, cpu_id, u in needs_bfd:
        cands = cores_per_cpu.get(cpu_id, [])
        if not cands:
            issues.append(f"task {name!r}: cpu_id={cpu_id} has no cores")
            continue
        ranked = sorted(cands, key=lambda cid: util_by_core[(cpu_id, cid)])
        chosen = ranked[0]
        fit_found = False
        for cid in ranked:
            if util_by_core[(cpu_id, cid)] + u <= 1.0:
                chosen = cid
                fit_found = True
                break
        if not fit_found:
            issues.append(
                f"task {name!r}: no core in cpu_id={cpu_id} has headroom for "
                f"u={float(u):.3f}; least-loaded ({chosen}) is at "
                f"{float(util_by_core[(cpu_id, chosen)]):.3f}"
            )
        util_by_core[(cpu_id, chosen)] += u
    stats['flex_util'] = round(float(flex_util), 4)
    if util_by_core:
        stats['max_core_util'] = round(float(max(util_by_core.values())), 4)
    for (cpu, core), u in sorted(util_by_core.items()):
        if u > 1.0:
            issues.append(
                f"core ({cpu}, {core}) overloaded: "
                f"sum u = {float(u):.3f} > 1.0"
            )

    # ------------------------------------------------------------------
    # Check 2: total-capacity upper bound
    # ------------------------------------------------------------------
    total_u = sum(util_by_core.values()) + flex_util
    total_cores = stats['cores']
    stats['total_util'] = round(float(total_u), 4)
    if total_u > total_cores:
        issues.append(
            f"total Σu = {float(total_u):.3f} exceeds platform capacity "
            f"= {total_cores} cores"
        )

    # ------------------------------------------------------------------
    # Check 3: chain reference integrity
    # ------------------------------------------------------------------
    name_to_wcet: dict[str, int] = {}
    for t in d.get('tasks', []):
        name_to_wcet[t['name']] = t.get('wcet', 0)
    for ch in d.get('chains', []):
        missing = [n for n in ch.get('tasks', []) if n not in name_to_wcet]
        if missing:
            issues.append(
                f"chain {ch['name']!r}: {len(missing)} unresolved task ref(s) "
                f"(first: {missing[0]!r})"
            )

    # ------------------------------------------------------------------
    # Check 4: chain budget vs sum-of-WCETs lower bound
    # ------------------------------------------------------------------
    for ch in d.get('chains', []):
        budget = ch.get('budget', 0)
        if not budget or budget <= 0:
            continue  # unconstrained chain (source had budget=0 for some)
        sum_wcet = sum(name_to_wcet.get(n, 0) for n in ch.get('tasks', []))
        if sum_wcet > budget:
            stats['chains_infeasible_by_wcet_sum'] += 1
            issues.append(
                f"chain {ch['name']!r}: sum WCET = {sum_wcet} us "
                f"> budget = {budget} us  (length {len(ch['tasks'])})"
            )

    # ------------------------------------------------------------------
    # Check 5: constrained deadline with positive slack
    # ------------------------------------------------------------------
    constrained = True      # RTA below is only valid for D <= T
    for t in d.get('tasks', []):
        C, D, T = t.get('wcet'), t.get('deadline'), t.get('period')
        if C is None or D is None or T is None:
            constrained = False
            issues.append(f"task {t['name']!r}: missing wcet, deadline or period")
        elif C <= 0:
            issues.append(f"task {t['name']!r}: wcet = {C} is not positive")
        elif C >= D:
            issues.append(
                f"task {t['name']!r}: wcet = {C} >= deadline = {D} (no slack)")
        elif D > T:
            constrained = False
            issues.append(
                f"task {t['name']!r}: deadline = {D} > period = {T}")
    names = [t.get('name') for t in d.get('tasks', [])]
    dup = sorted({n for n in names if names.count(n) > 1}, key=str)
    if dup:
        constrained = False     # check 6 keys results by name
        issues.append(f"duplicate task names: {dup}")

    # ------------------------------------------------------------------
    # Check 6: reference-schedule feasibility (DM-FP, jitter-aware RTA)
    # ------------------------------------------------------------------
    placed = [t for t in d.get('tasks', [])
              if t.get('placement', {}).get('core_id') is not None]
    n_tasks = len(d.get('tasks', []))
    try:
        major = int(str((d.get('metadata') or {})
                        .get('benchmark_version', '0')).split('.')[0])
    except ValueError:
        major = 0
    if not placed:
        stats['rta_skipped'] = True
    if major >= 2 and not placed:
        issues.append("check 6 not run: benchmark v2 instance without "
                      "reference placement (core_id)")
    elif placed and len(placed) < n_tasks:
        issues.append(
            f"check 6 not run: {n_tasks - len(placed)} of {n_tasks} tasks "
            f"have no core_id")
    elif any(t['placement'].get('cpu_id') is None for t in placed):
        issues.append("check 6 not run: a task has a core_id but no cpu_id")
    elif placed and not constrained:
        issues.append("check 6 not run: deadlines must satisfy D <= T "
                      "and task names must be unique")
    elif placed:
        by_core: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for t in placed:
            p = t['placement']
            by_core[(p['cpu_id'], p['core_id'])].append(t)
        misses = 0
        for (cpu, core), ts in sorted(by_core.items()):
            for name, r in response_times(ts).items():
                if r is None:
                    misses += 1
                    issues.append(
                        f"task {name!r} on core ({cpu}, {core}) misses its "
                        f"deadline under DM-FP")
        stats['deadline_misses'] = misses

    return issues, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+',
        help='instance JSON path(s) or a directory containing them.')
    ap.add_argument('--verbose', action='store_true',
        help='Print every issue (default: per-file summary only).')
    ap.add_argument('--show-passes', action='store_true',
        help='Also print rows for instances that pass.')
    args = ap.parse_args()

    files: list[str] = []
    for p in args.paths:
        if not os.path.exists(p):
            print(f"# path does not exist: {p}", file=sys.stderr)
            return 1
        if os.path.isdir(p):
            # Restrict to instance files: skip anything named like a profile.
            for f in sorted(glob.glob(os.path.join(p, '**/*.json'),
                                       recursive=True)):
                if os.path.basename(f).startswith('profile'):
                    continue
                files.append(f)
        else:
            files.append(p)

    if not files:
        print("# no instance JSONs found", file=sys.stderr)
        return 1

    n_total = 0
    n_pass = 0
    n_fail = 0
    bucket_stats: dict[str, dict] = defaultdict(
        lambda: {'pass': 0, 'fail': 0, 'max_util': 0.0,
                 'infeasible_chains': 0, 'total_chains': 0})

    n_rta_skipped = 0
    for path in files:
        n_total += 1
        bucket = os.path.basename(os.path.dirname(path))
        bs = bucket_stats[bucket]
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
        except (ValueError, UnicodeDecodeError) as e:
            print(f"FAIL  {path}: invalid JSON ({e})")
            n_fail += 1
            bs['fail'] += 1
            continue
        try:
            if not isinstance(d, dict):
                raise TypeError('top level is not a JSON object')
            issues, stats = check_instance(d)
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            print(f"FAIL  {path}: malformed instance "
                  f"({type(e).__name__}: {e})")
            n_fail += 1
            bs['fail'] += 1
            continue
        n_rta_skipped += stats['rta_skipped']
        bs['total_chains'] += stats['chains']
        bs['infeasible_chains'] += stats['chains_infeasible_by_wcet_sum']
        bs['max_util'] = max(bs['max_util'], stats['max_core_util'])
        if issues:
            n_fail += 1
            bs['fail'] += 1
            if args.verbose:
                print(f"FAIL  {path}")
                for i in issues:
                    print(f"    {i}")
            else:
                first = issues[0]
                if len(issues) > 1:
                    first += f"  (+{len(issues)-1} more)"
                print(f"FAIL  {path}  max_core_util={stats['max_core_util']}  {first}")
        else:
            n_pass += 1
            bs['pass'] += 1
            if args.show_passes:
                print(f"pass  {path}  max_core_util={stats['max_core_util']}")

    # --------------------------------------------------------------
    # Summary
    # --------------------------------------------------------------
    print()
    print(f"# summary")
    print(f"#   total instances: {n_total}")
    print(f"#   pass:            {n_pass}")
    print(f"#   fail:            {n_fail}")
    if n_rta_skipped:
        print(f"#   check 6 not run (no reference placement, pre-v2.0): "
              f"{n_rta_skipped}")
    if len(bucket_stats) > 1:
        print(f"#   per-bucket breakdown:")
        for bucket in sorted(bucket_stats.keys()):
            bs = bucket_stats[bucket]
            print(f"#     {bucket:<10}  "
                  f"pass={bs['pass']:<3} fail={bs['fail']:<3}  "
                  f"max_core_util={bs['max_util']:.3f}  "
                  f"infeasible_chains={bs['infeasible_chains']}/{bs['total_chains']}")

    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
