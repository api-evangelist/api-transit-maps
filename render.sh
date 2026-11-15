#!/bin/bash
# render.sh <svg> <out.png> [maxdim] -- headless Chrome screenshot, then downscale.
# Chrome drops the bottom of very large screenshots, so sheets over 9000px are captured at a
# device scale factor that keeps the capture under 9000px.
svg=$1; png=$2; max=${3:-1800}
wh=$(python3 -I -c "import re;s=open('$svg').read(500);m=re.search(r'width=\"(\d+)\" height=\"(\d+)\"',s);print(m.group(1),m.group(2))")
set -- $wh
sf=$(python3 -I -c "print(min(1.0, 9000/max($1,$2)))")
perl -e 'alarm 180; exec @ARGV' "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=$sf --screenshot="$png.full.png" --window-size=$1,$2 "file://$PWD/$svg" 2>/dev/null
[ -f "$png.full.png" ] && sips -Z $max "$png.full.png" --out "$png" >/dev/null
