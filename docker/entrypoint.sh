#!/bin/sh
set -eu

debug="${DJANGO_DEBUG:-0}"

if [ "$debug" = "0" ] && [ -z "${REDIS_URL:-}" ]; then
  echo "REDIS_URL is required when DJANGO_DEBUG=0" >&2
  exit 1
fi

if [ "$debug" = "0" ]; then
  secret=$(printf '%s' "${DJANGO_SECRET_KEY:-}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  case "$secret" in
    ""|change-me|dev-only-not-for-production|build-only-not-used-at-runtime)
      echo "DJANGO_SECRET_KEY must be a unique value when DJANGO_DEBUG is off" >&2
      exit 1
      ;;
  esac
fi

if [ -n "${DOMAIN:-}" ] && [ "$debug" != "0" ]; then
  echo "Refusing to boot with DEBUG on while DOMAIN is set" >&2
  exit 1
fi

# One-off admin: docker compose run --rm web python manage.py <cmd>
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8001 \
  --workers "${WEB_CONCURRENCY:-2}" \
  --worker-class gthread \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout 30 \
  --graceful-timeout 10 \
  --keep-alive 5 \
  --max-requests 500 \
  --max-requests-jitter 50 \
  --access-logfile - \
  --error-logfile -
