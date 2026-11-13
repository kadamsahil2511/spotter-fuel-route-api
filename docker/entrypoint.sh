#!/bin/sh
set -eu

if [ "${DJANGO_DEBUG:-1}" = "0" ] && [ -z "${REDIS_URL:-}" ]; then
  echo "REDIS_URL is required when DJANGO_DEBUG=0" >&2
  exit 1
fi

if [ -n "${DOMAIN:-}" ] && [ "${DJANGO_DEBUG:-1}" != "0" ]; then
  echo "Refusing to boot with DEBUG on while DOMAIN is set" >&2
  exit 1
fi

python manage.py migrate --noinput
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
