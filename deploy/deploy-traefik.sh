#!/bin/bash
set -euo pipefail

STAGE=/tmp/tiny-pages-stage
TS=$(date -u +%Y%m%dT%H%M%SZ)

pct exec 1003 -- mkdir -p /root/conf.d-backups
pct exec 1003 -- tar -czf "/root/conf.d-backups/conf.d-$TS.tar.gz" -C /root conf.d
pct push 1003 "$STAGE/wannkannichballern.yaml" /root/conf.d/wannkannichballern.yaml

pct exec 1003 -- systemctl restart traefik
sleep 5
echo -n "traefik: "; pct exec 1003 -- systemctl is-active traefik

echo "--- origin checks via Traefik (SNI) ---"
for host in wannkannichballern.wfech.de; do
  echo -n "$host: "
  curl -sk -o /dev/null -w '%{http_code}\n' --resolve "$host:443:192.0.2.3" "https://$host/" || true
done

echo "--- new page body check ---"
curl -sk --resolve "wannkannichballern.wfech.de:443:192.0.2.3" https://wannkannichballern.wfech.de/ | grep -o 'Preis-Uhr' | head -1
echo -n "POST: "
curl -sk -o /dev/null -w '%{http_code}\n' -X POST --resolve "wannkannichballern.wfech.de:443:192.0.2.3" https://wannkannichballern.wfech.de/
echo "--- headers ---"
curl -skI --resolve "wannkannichballern.wfech.de:443:192.0.2.3" https://wannkannichballern.wfech.de/ | sed -n '1,15p'
