#!/bin/bash
set -euo pipefail

# One-time/infra provisioning only (nginx, holiday timer, root index). The site files are deployed by deploy-site.sh.
CT=100
STAGE=/tmp/tiny-pages-stage

pct exec "$CT" -- mkdir -p /srv/pages/deepseek-offpeak

pct push "$CT" "$STAGE/root-index.html" /srv/pages/index.html

pct exec "$CT" -- mkdir -p /opt/tiny-pages
pct push "$CT" "$STAGE/update_holidays.py" /opt/tiny-pages/update_holidays.py
pct push "$CT" "$STAGE/tiny-holidays.service" /etc/systemd/system/tiny-holidays.service
pct push "$CT" "$STAGE/tiny-holidays.timer" /etc/systemd/system/tiny-holidays.timer
pct exec "$CT" -- systemctl daemon-reload
pct exec "$CT" -- systemctl enable --now tiny-holidays.timer

pct push "$CT" "$STAGE/nginx-tiny-pages.conf" /etc/nginx/sites-available/tiny-pages.conf
pct exec "$CT" -- sh -c 'rm -f /etc/nginx/sites-enabled/default; ln -sf /etc/nginx/sites-available/tiny-pages.conf /etc/nginx/sites-enabled/tiny-pages.conf; nginx -t'
pct exec "$CT" -- systemctl enable nginx
pct exec "$CT" -- systemctl restart nginx
sleep 2

pct exec "$CT" -- sh -c 'systemctl is-active nginx; ls -l /srv/pages /srv/pages/deepseek-offpeak /srv/pages/deepseek-offpeak/preis-uhr'
echo -n "backend root: "; curl -fsS -o /dev/null -w '%{http_code}\n' http://192.0.2.32/
echo -n "backend page: "; curl -fsS -o /dev/null -w '%{http_code}\n' http://192.0.2.32/deepseek-offpeak/
echo -n "backend POST: "; curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://192.0.2.32/
echo -n "backend dotfile: "; curl -sS -o /dev/null -w '%{http_code}\n' http://192.0.2.32/.hidden
