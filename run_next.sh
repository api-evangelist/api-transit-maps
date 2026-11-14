#!/bin/bash
# run_next.sh N [slug...]  -- map the next N unmapped exemplar providers (or the named slugs):
# pull from S3, build (atlas above ATLAS_OPS operations), make canonical, verify, print review table.
set -uo pipefail
cd "$(dirname "$0")"
N=${1:-50}; shift || true
ATLAS_OPS=${ATLAS_OPS:-2500}
TAG=${TAG:-final}
SLUGS="$*"
[ -z "$SLUGS" ] && SLUGS=$(.venv/bin/python next_batch.py "$N")
[ -z "$SLUGS" ] && { echo "nothing left to map"; exit 0; }
mkdir -p logs previews work
echo "batch: $SLUGS"

# 1. pull profiles into work/ (never into all/, which other tools treat as the catalog cache)
ALL="$PWD/work" bash ~/Github/engine/profiles-sync.sh down $SLUGS >/dev/null 2>&1

# 2. sort into single sheets and atlases by operation count (after version/duplicate collapse)
SINGLE=""; ATLAS=""
for s in $SLUGS; do
  n=$(.venv/bin/python -c "import transit_model as T; print(len(T.load('work/$s', None).ops))" 2>logs/$s-load.log || echo ERR)
  if [ "$n" = "ERR" ]; then echo "LOAD FAILED $s (logs/$s-load.log)"; continue; fi
  if [ "$n" -gt "$ATLAS_OPS" ]; then
    ATLAS="$ATLAS $s"
    [ -f "$s-families.yml" ] || printf '# %s is published as an atlas.\nmax_families: 60\natlas:\n  sheet_max_ops: 450\n' "$s" > "$s-families.yml"
  elif [ "$n" -gt 1 ]; then SINGLE="$SINGLE $s"
  else echo "NO OPERATIONS $s"; fi
done

# 3. build
[ -n "$SINGLE" ] && ./run_batch.sh "$TAG" $SINGLE > logs/batch-$TAG.log 2>&1
for s in $ATLAS; do rm -f $s/$s-atlas-*; .venv/bin/python atlas.py work/$s $s $s-families.yml --jobs 3 > logs/$s-atlas.log 2>&1; done

# 4. canonical files, verification, review table
.venv/bin/python finalize.py "$TAG" $SINGLE $ATLAS
.venv/bin/python next_batch.py --status
