#!/bin/bash
# run_batch.sh <tag> <slug>...   build each provider's network map (auto families unless <slug>-families.yml exists)
tag=$1; shift
for p in "$@"; do
  fam=""; [ -f "$p-families.yml" ] && fam="$p-families.yml"
  ( .venv/bin/python build_network_map.py work/$p $p ${fam:-/dev/null} --tag=-$tag > logs/$p-$tag.log 2>&1 \
    && ./render.sh $p/$p-network-map-$tag.svg previews/$p-$tag.png 1800 ) &
  while [ $(jobs -r | wc -l) -ge 3 ]; do sleep 2; done
done
wait
for p in "$@"; do echo "$p: $(tail -1 logs/$p-$tag.log)"; done
