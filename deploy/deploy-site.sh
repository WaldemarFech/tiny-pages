#!/bin/bash
# Deploy the static site (router dashboard + /preis-uhr/) into CT <ct-id> from your checkout, over the VPS host (pct exec).
# No staging dir on the VPS: tar is piped through ssh into the container. Needs the VPS key.
#   bash deploy/deploy-site.sh            # deploy + smoke test
# Bump the ?v= token in index.html / router.js / explain.js before deploying changed JS/CSS (Cloudflare caches them up to 4 h).
# router.json and models_trending.json are NOT deployed here: the router publishes them itself (scripts/models/or_router_export.py).
# preis-uhr/holidays.js is NOT deployed: it is regenerated monthly inside the CT (tiny-holidays.timer); an existing root-level copy is moved once.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
KEY=${SSH_KEY:-$HOME/.ssh/pve_netcup}
HOST=${VPS_HOST:-root@your-vps.example}
PORT=${VPS_PORT:-1001}
CT=${CT:-100}
DEST=/srv/pages/deepseek-offpeak
SSH=(ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=10 "$HOST")

cd "$HERE/deepseek-offpeak"
"${SSH[@]}" "pct exec $CT -- sh -c 'mkdir -p $DEST/preis-uhr; [ -f $DEST/holidays.js ] && [ ! -f $DEST/preis-uhr/holidays.js ] && mv $DEST/holidays.js $DEST/preis-uhr/holidays.js; true'"
tar --owner=0 --group=0 --mode='u=rwX,go=rX' --exclude=preis-uhr/holidays.js -cf - index.html router.css router.js model.js charts.js explain.js trending.js watch.js modelle preis-uhr \
  | "${SSH[@]}" "pct exec $CT -- tar -xf - -C $DEST"
"${SSH[@]}" "pct exec $CT -- rm -f $DEST/app.js $DEST/peak.js $DEST/holidays.js"
# the holiday refresher writes to the new path now
"${SSH[@]}" "pct exec $CT -- sh -c 'cat > /opt/tiny-pages/update_holidays.py'" < scripts/update_holidays.py

for path in / /modelle/ /preis-uhr/ /router.js /watch.js /modelle/modelle.js /preis-uhr/holidays.js; do
  printf '%s -> ' "$path"; curl -fsS -o /dev/null -w '%{http_code}\n' "https://wannkannichballern.wfech.de$path?smoke=$(date +%s)"
done
# every module the page imports must exist on the server: a forgotten file in the tar list broke the whole page once (watch.js, 2026-11-02)
for f in *.js *.css modelle/*.js; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "https://wannkannichballern.wfech.de/$f?smoke=$(date +%s)")
  [ "$code" = 200 ] || { printf '%s -> %s MISSING\n' "$f" "$code"; exit 1; }
done
printf 'all deployed assets -> 200\n'
printf 'POST / -> '; curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://wannkannichballern.wfech.de/
printf '/.env -> '; curl -sS -o /dev/null -w '%{http_code}\n' https://wannkannichballern.wfech.de/.env
