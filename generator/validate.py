#!/usr/bin/env python3
"""Run the SCHEMA.md invariants against one or more fingerprints.

Exits 0 if every fingerprint passes, 1 otherwise.

Run::

    python3 validate.py PROFILE1.json [PROFILE2.json ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TOL = 1.5  # ± tolerance for share-sum invariants (percent points)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL


def _sorted(seq):
    return list(seq) == sorted(seq)


def validate(profile: dict, label: str = '<profile>') -> list[str]:
    """Return a list of failure messages.  Empty list = all invariants pass."""
    fails: list[str] = []
    push = fails.append

    # ---- Metadata / shape ----
    if profile.get('format_version') != '1.0':
        push(f'format_version = {profile.get("format_version")!r}, expected "1.0"')
    md = profile.get('metadata', {}) or {}
    for k in ('name', 'description', 'source_type'):
        if not md.get(k):
            push(f'metadata.{k} missing or empty')

    plat = profile.get('platform')
    if not isinstance(plat, list) or len(plat) == 0:
        push('platform must be a non-empty list')
    else:
        for i, cpu in enumerate(plat):
            if not (isinstance(cpu, dict) and isinstance(cpu.get('cores'), int)
                    and cpu['cores'] > 0):
                push(f'platform[{i}] must be {{cores: positive int}}')

    pus = profile.get('periods_us')
    if not isinstance(pus, list) or len(pus) == 0:
        push('periods_us must be a non-empty list')
    else:
        if not _sorted(pus):
            push('periods_us is not sorted')
        if not all(isinstance(x, int) and x > 0 for x in pus):
            push('periods_us must contain only positive ints')

    # ---- chains.global ----
    # When the source has no chain corpus (chain_count_per_100_tasks == 0),
    # the chain section is optional and may carry empty placeholders.
    has_chains = profile.get('chain_count_per_100_tasks', 0) > 0
    cg = profile.get('chains', {}).get('global', {}) or {}
    big = cg.get('bigram')
    if has_chains and (not isinstance(big, dict) or len(big) == 0):
        push('chains.global.bigram missing or empty')
    rde = cg.get('rho_decile_edges')
    if has_chains:
        if not isinstance(rde, list) or len(rde) != 11:
            push(f'chains.global.rho_decile_edges must have 11 floats '
                 f'(got {type(rde).__name__} of length '
                 f'{len(rde) if isinstance(rde, list) else "?"})')
        elif not _sorted(rde):
            push('chains.global.rho_decile_edges not sorted')

    # ---- Task-share sum (per_period + variable_period == 100) ----
    pp = profile.get('per_period', {}) or {}
    ts_per = sum(per.get('task_share', 0.0) for per in pp.values())
    ts_var = sum(v.get('task_share', 0.0) for v in profile.get('variable_period', []) or [])
    if not _close(ts_per + ts_var, 100.0):
        push(f'task_share sum (per_period + variable_period) = {ts_per+ts_var:.2f}, expected ≈ 100')

    # ---- Per-period invariants ----
    pus_set = set(str(p) for p in (pus or []))
    for k, per in pp.items():
        if k not in pus_set:
            push(f'per_period key {k!r} not in periods_us')
        # D/T histogram sums to ~100
        dt = per.get('deadline_ratio_hist', [])
        sdt = sum(b.get('count', 0) for b in dt)
        if dt and not _close(sdt, 100):
            push(f'per_period[{k}].deadline_ratio_hist sum = {sdt:.2f}')
        # task_visit_weights sums to ~100
        tvw = per.get('task_visit_weights', [])
        stvw = sum(v.get('share', 0) for v in tvw)
        if tvw and not _close(stvw, 100):
            push(f'per_period[{k}].task_visit_weights sum = {stvw:.2f}')
        # activation sums to ~100 if present
        act = per.get('activation')
        if act:
            sact = sum(v for v in act.values() if isinstance(v, (int, float)))
            if not _close(sact, 100):
                push(f'per_period[{k}].activation sum = {sact:.2f}')
        # envelope is two ints lo ≤ hi
        env = per.get('wcet_envelope')
        if not (isinstance(env, list) and len(env) == 2
                and env[0] <= env[1]):
            push(f'per_period[{k}].wcet_envelope malformed: {env!r}')
        # wcet has exactly one of histogram / aggregate
        wc = per.get('wcet', {})
        keys = set(wc.keys()) if isinstance(wc, dict) else set()
        if keys not in ({'histogram'}, {'aggregate'}):
            push(f'per_period[{k}].wcet must have exactly one of '
                 f'{{histogram, aggregate}}; got {keys}')

    # ---- Length-bucket invariants ----
    lbs = profile.get('chains', {}).get('length_buckets', {}) or {}
    if has_chains and not lbs:
        push('chain_count_per_100_tasks > 0 but length_buckets is empty')
    if lbs:
        ss = sum(b.get('share', 0) for b in lbs.values())
        if not _close(ss, 100):
            push(f'length_buckets shares sum = {ss:.2f}, expected ≈ 100')
    bucket_bounds = {'very_short': (1, 3), 'short': (4, 6),
                     'medium': (7, 12), 'long': (13, 9999)}
    for bname, b in lbs.items():
        if bname not in bucket_bounds:
            push(f'unknown bucket {bname!r}'); continue
        lo, hi = bucket_bounds[bname]
        bands = b.get('length_quartile_bands') or []
        if not (isinstance(bands, list) and len(bands) == 5):
            push(f'length_buckets[{bname}].length_quartile_bands '
                 f'must have 5 numbers (got {bands!r})')
        else:
            if not _sorted(bands):
                push(f'length_buckets[{bname}].length_quartile_bands not sorted')
            if not (lo <= bands[0] and bands[-1] <= hi):
                push(f'length_buckets[{bname}].length_quartile_bands '
                     f'{bands} out of bucket range [{lo}, {hi}]')
        # Per-bucket ρ deciles when present.
        rde_b = b.get('rho_decile_edges')
        if rde_b is not None:
            if not (isinstance(rde_b, list) and len(rde_b) == 11):
                push(f'length_buckets[{bname}].rho_decile_edges '
                     f'must have 11 floats')
            elif not _sorted(rde_b):
                push(f'length_buckets[{bname}].rho_decile_edges not sorted')
        # Per-bucket bigram keys must be valid periods (or Start/End).
        bgram = b.get('bigram')
        if bgram is not None:
            bkeys = set(bgram.keys()) | {k for row in bgram.values() for k in row.keys()}
            for tok in bkeys:
                if tok in ('Start', 'End'):
                    continue
                if tok not in pus_set:
                    push(f'length_buckets[{bname}].bigram key {tok!r} '
                         f'not in periods_us')
                    break

    # Global bigram keys
    if isinstance(big, dict):
        gkeys = set(big.keys()) | {k for row in big.values() for k in row.keys()}
        for tok in gkeys:
            if tok in ('Start', 'End'):
                continue
            if tok not in pus_set:
                push(f'chains.global.bigram key {tok!r} not in periods_us')
                break

    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('profile', nargs='+', help='One or more profile JSON paths.')
    args = ap.parse_args()
    all_ok = True
    for path_str in args.profile:
        path = Path(path_str)
        if not path.exists():
            print(f'  {path}: MISSING'); all_ok = False; continue
        try:
            profile = json.loads(path.read_text())
        except Exception as exc:
            print(f'  {path}: PARSE ERROR — {exc}'); all_ok = False; continue
        fails = validate(profile, str(path))
        if fails:
            all_ok = False
            print(f'  {path}: FAIL ({len(fails)})')
            for f in fails:
                print(f'    - {f}')
        else:
            print(f'  {path}: OK')
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
