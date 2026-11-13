#!/usr/bin/env bash
# Run this on the VPS, from anywhere. It never stops or recreates p1 containers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Create .env from .env.example first. DJANGO_SECRET_KEY and DJANGO_DEBUG=0 are required." >&2
  exit 1
fi

echo "Pre-flight p1"
docker ps --filter name=p1- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
p1_count="$(docker ps --filter name=p1- --filter status=running -q | wc -l | tr -d ' ')"
if [[ "$p1_count" -lt 4 ]]; then
  echo "Expected 4 running p1 containers, found $p1_count. Stop." >&2
  exit 1
fi

if curl -fsS -o /dev/null -w '%{http_code}' https://setup.p1.kadamsahil.com/ | grep -qx 200; then
  echo "p1 HTTP 200"
else
  echo "p1 did not return HTTP 200. Stop." >&2
  exit 1
fi

if ss -ltn | awk '{print $4}' | grep -q ':8001$'; then
  if ! docker ps --format '{{.Names}}' | grep -qx 'spotter-web'; then
    echo "Port 8001 is already in use by something other than spotter-web. Stop." >&2
    ss -ltnp | awk 'NR==1 || /:8001/' || true
    exit 1
  fi
fi

docker compose -p spotter up -d --build

echo "Waiting for Spotter health"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8001/api/v1/health; then
    echo
    break
  fi
  sleep 3
done
curl -fsS http://127.0.0.1:8001/api/v1/health >/dev/null

echo "Post-flight p1"
docker ps --filter name=p1- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -fsS -o /dev/null -w 'p1 %{http_code}\n' https://setup.p1.kadamsahil.com/

echo
echo "App is on 127.0.0.1:8001 only. Nginx is unchanged."
echo "Next, if the vhost is not installed yet: sudo bash deploy/install-nginx.sh"
