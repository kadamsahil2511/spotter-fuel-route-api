#!/usr/bin/env bash
# Install the Spotter vhost and reload nginx. Does not edit the p1 site file.
# Reloads nginx. Never restarts it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AVAILABLE="/etc/nginx/sites-available/spotter.kadamsahil.com"
ENABLED="/etc/nginx/sites-enabled/spotter.kadamsahil.com"
SOURCE="$ROOT/deploy/nginx/spotter.kadamsahil.com.conf"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo." >&2
  exit 1
fi

if [[ -f "$AVAILABLE" ]] && grep -q 'ssl_certificate' "$AVAILABLE" && [[ "${FORCE:-}" != "1" ]]; then
  echo "Spotter vhost already has a certificate. Not overwriting it. FORCE=1 to replace the file." >&2
  exit 0
fi

install -m 644 "$SOURCE" "$AVAILABLE"
ln -sfn "$AVAILABLE" "$ENABLED"

if ! nginx -t; then
  echo "nginx -t failed. Removing the new symlink and leaving nginx running as it was." >&2
  rm -f "$ENABLED"
  nginx -t
  exit 1
fi

systemctl reload nginx
echo "Reloaded nginx. p1 site files were not modified."

if [[ "${ISSUE_CERT:-}" == "1" ]]; then
  certbot --nginx -d spotter.kadamsahil.com
  nginx -t
  systemctl reload nginx
  echo "Certificate installed."
else
  echo "HTTP vhost is in place. After DNS resolves, run: sudo ISSUE_CERT=1 bash deploy/install-nginx.sh"
fi
