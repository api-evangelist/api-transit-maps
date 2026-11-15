#!/bin/bash
# run strong batches back to back: build, skip no-op providers, publish
B=${1:-5}; END=${2:-30}
while [ $B -le $END ]; do
  L=logs/strong$B-run.log
  BAND=strong ./run_next.sh 50 > $L 2>&1
  grep -q "nothing left to map" $L && break
  grep -o "^NO OPERATIONS .*" $L | awk '{print "  "$3": profile has no operations"}' >> skip.yml
  python3 -c "import yaml;yaml.safe_load(open('skip.yml'))" || { echo "skip.yml broken at batch $B"; exit 1; }
  ./publish.sh "Add transit maps: strong band batch $B" > logs/strong$B-publish.log 2>&1
  echo "batch $B: $(grep strong: $L | tail -1)"
  grep -v "^batch:" $L | grep -E "FAIL|MISSING|INVALID|huge|Error" | sed "s/^/  /"
  B=$((B+1))
done
