#!/bin/bash
# publish.sh "message"  -- rebuild the gallery, commit ONLY map/gallery/code files by explicit list,
# push, wait for the GitHub Pages build, and check the live site.
set -uo pipefail
cd "$(dirname "$0")"
MSG=${1:-"Add transit maps"}
python3 build_gallery.py
# explicit list: what git sees, restricted to the shapes this repo publishes (never `git add -A`)
git status --porcelain -uall | sed -E 's/^.. //' | grep -E \
  '^(index\.html|view\.html|thumbs/[^/]+\.png|[a-z0-9-]+/[a-z0-9-]+-(network-map\.(svg|json)|atlas(-[0-9]{2}(-thumb\.png|\.svg|\.json)|-plan\.json|\.html))|[a-z0-9-]+-families\.yml|[a-z_]+\.(py|sh)|DESIGN\.md|README\.md|skip\.yml)$' \
  > /tmp/transit-publish-files.txt
echo "$(wc -l < /tmp/transit-publish-files.txt | tr -d ' ') files to commit"
[ -s /tmp/transit-publish-files.txt ] || { echo "nothing to publish"; exit 0; }
tr '\n' '\0' < /tmp/transit-publish-files.txt | xargs -0 git add --
git commit -q -m "$MSG

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git pull -q --rebase && git push -q || { echo "push failed"; exit 1; }
sha=$(git rev-parse HEAD)
for i in $(seq 1 30); do
  b=$(gh api repos/api-evangelist/api-transit-maps/pages/builds/latest --jq '.commit + " " + .status' 2>/dev/null)
  [ "${b%% *}" = "$sha" ] && [ "${b##* }" = "built" ] && break
  sleep 10
done
echo "pages build: $b"
echo "gallery: $(curl -s -o /dev/null -w '%{http_code}' https://transit.apievangelist.com/)"
for f in $(grep -E -- '-network-map\.svg$|-atlas\.html$' /tmp/transit-publish-files.txt | head -5); do
  echo "$f: $(curl -s -o /dev/null -w '%{http_code}' https://transit.apievangelist.com/$f)"
done
