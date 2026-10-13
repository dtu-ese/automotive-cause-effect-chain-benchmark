#!/usr/bin/env bash
# Regenerate the full Cause-Effect Chain Benchmark dataset from the
# released fingerprint.  Five scale buckets (100..500pct), 100
# instances per bucket.  Output goes to ../data/<USECASE>/<bucket>/
# by default, overwriting whatever is already there.
#
# Usage:
#   ./generate_all.sh                            # default: TrustMotion, 100 per bucket
#   ./generate_all.sh -u <USECASE>               # different use-case (must have a fingerprint)
#   ./generate_all.sh -n <COUNT>                 # instances per bucket
#   ./generate_all.sh -o <OUT_DIR>               # output root (per-bucket subdirs created inside)
#   ./generate_all.sh -s <SEED_BASE>             # seed base; bucket k uses SEED_BASE + k (default 42)
set -euo pipefail
INVOKE_DIR="$(pwd)"
cd "$(dirname "$0")"

USECASE="TrustMotion"
COUNT=100
SEED_BASE=42
ROOT="$(cd .. && pwd)"
OUT_ROOT=""

while getopts "u:n:o:s:h" opt; do
  case $opt in
    u) USECASE="$OPTARG" ;;
    n) COUNT="$OPTARG" ;;
    o) case "$OPTARG" in /*) OUT_ROOT="$OPTARG" ;; *) OUT_ROOT="${INVOKE_DIR}/$OPTARG" ;; esac ;;
    s) SEED_BASE="$OPTARG" ;;
    h) sed -n '2,12p' "$0"; exit 0 ;;
    *) sed -n '2,12p' "$0"; exit 1 ;;
  esac
done

PROFILE="${ROOT}/fingerprints/${USECASE}/profile.json"
OUT_ROOT="${OUT_ROOT:-${ROOT}/data/${USECASE}}"

if [[ ! -f "$PROFILE" ]]; then
    echo "# profile not found: $PROFILE" >&2
    echo "# expected layout:  <root>/fingerprints/<USECASE>/profile.json" >&2
    exit 1
fi

echo "# usecase  : $USECASE"
echo "# profile  : $PROFILE"
echo "# out root : $OUT_ROOT"
echo "# count    : $COUNT instances per bucket"
echo

mkdir -p "$OUT_ROOT"

for k in 1 2 3 4 5; do
    bucket="$(printf '%dpct' $((k*100)))"
    out="${OUT_ROOT}/${bucket}"
    echo "# generating ${bucket} -> ${out}"
    rm -rf "$out"
    python3 generate.py \
        --profile "$PROFILE" \
        --scale "$k" \
        --count "$COUNT" \
        --seed  "$((SEED_BASE + k))" \
        --out   "$out"
done

echo
echo "# done"
echo "# instances per bucket:"
for d in "$OUT_ROOT"/*pct; do
    echo "  ${d}: $(ls "$d"/*.json 2>/dev/null | wc -l | tr -d ' ')"
done
