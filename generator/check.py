#!/usr/bin/env python3
r"""Sanity-check generated instances for trivial infeasibility.

Four checks are necessary conditions for *any* scheduling policy.
Failing any of them means the instance is unusable for schedulability
or end-to-end latency research:

  1. Per-core utilisation upper bound -- every core's
     sum_{t pinned to it} wcet/period must be <= 1.  Tasks with
     cpu_id=null or core_id=null are placed in a "flexible" pool and
     reported separately.

  2. Total-capacity upper bound -- sum_t wcet/period across the whole
     instance (pinned + flexible) must be <= the number of cores in
     the platform.  Failing this means no allocation of the flex
     pool can avoid overload.

  3. Chain reference integrity -- every task name in each chain's
     ordered list must resolve to a task in the instance's task array.

  4. Chain budget feasibility -- if the chain declares a positive
     end-to-end budget, the sum of WCETs along the chain (a strict
     lower bound on E2E latency) must not exceed it.

Passing all four does not prove feasibility, but it does eliminate
the trivially broken cases.  A real schedulability check (per-CPU FP
response-time, EDF tightness) or an end-to-end latency analyser
(e.g.\ TU-Dortmund's E2EEvaluation) is the next step.

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


def check_instance(d: dict) -> tuple[list[str], dict]:
    """Return (issues, stats). issues is empty iff the instance passes
    all three checks; stats reports per-check summary metrics."""
    issues: list[str] = []
    stats: dict = {
        'tasks':           len(d.get('tasks', [])),
        'chains':          len(d.get('chains', [])),
        'cores':           sum(len(c['cores']) for c in d['platform']['cpus']),
        'max_core_util':   0.0,
        'flex_util':       0.0,
        'chains_infeasible_by_wcet_sum': 0,
    }

    # ------------------------------------------------------------------
    # Check 1: per-core utilisation
    #
    # Tasks ship with core_id either populated (an explicit pinning the
    # instance commits to) or null (the released-schema default, the
    # evaluator allocates at evaluation time).  We validate explicit
    # pinnings against their stated core, and we run BFD only for the
    # null-core tasks within their stated CPU.
    # ------------------------------------------------------------------
    cores_per_cpu: dict[int, list[int]] = {
        cpu['id']: [c['id'] for c in cpu['cores']]
        for cpu in d['platform']['cpus']
    }
    util_by_core: dict[tuple[int, int], float] = defaultdict(float)
    flex_util = 0.0     # tasks whose cpu_id is null (boundary markers)
    explicit = []       # (name, cpu_id, core_id, u)
    needs_bfd = []      # (name, cpu_id, u)
    for t in d.get('tasks', []):
        p = t.get('placement', {})
        period = t.get('period')
        wcet   = t.get('wcet')
        if not period or not wcet:
            continue
        u = wcet / period
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
    # 1b. BFD-allocate the cpu-pinned, core-flex tasks, respecting the
    # u already accumulated by the explicit pinnings (heaviest-first,
    # ties by name).
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
                f"u={u:.3f}; least-loaded ({chosen}) is at "
                f"{util_by_core[(cpu_id, chosen)]:.3f}"
            )
        util_by_core[(cpu_id, chosen)] += u
    stats['flex_util'] = round(flex_util, 4)
    if util_by_core:
        stats['max_core_util'] = round(max(util_by_core.values()), 4)
    for (cpu, core), u in sorted(util_by_core.items()):
        if u > 1.0:
            issues.append(
                f"core ({cpu}, {core}) overloaded: "
                f"sum u = {u:.3f} > 1.0"
            )

    # ------------------------------------------------------------------
    # Check 2: total-capacity upper bound
    # ------------------------------------------------------------------
    total_u = sum(util_by_core.values()) + flex_util
    total_cores = stats['cores']
    stats['total_util'] = round(total_u, 4)
    if total_u > total_cores:
        issues.append(
            f"total Σu = {total_u:.3f} exceeds platform capacity "
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

    for path in files:
        with open(path) as f:
            try:
                d = json.load(f)
            except json.JSONDecodeError as e:
                print(f"FAIL  {path}: invalid JSON ({e})")
                n_fail += 1
                continue
        issues, stats = check_instance(d)
        n_total += 1
        bucket = os.path.basename(os.path.dirname(path))
        bs = bucket_stats[bucket]
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
