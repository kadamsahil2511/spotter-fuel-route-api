#!/usr/bin/env bash
# Run this on the VPS, from anywhere. It never stops or recreates p1 containers.
# Deploys origin/main. The image is tagged with the git short SHA. latest is an alias.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "This directory is not a git checkout. Clone the repo and run deploy/deploy.sh from it." >&2
  exit 1
fi
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "Git remote origin is not configured." >&2
  exit 1
fi

echo "Updating checkout to origin/main"
GIT_TERMINAL_PROMPT=0 git fetch origin main
git checkout -f -B main origin/main

# The checkout may have replaced this file. Continue with the script from origin/main.
if [[ "${DEPLOY_REEXEC:-}" != "1" ]]; then
  export DEPLOY_REEXEC=1
  exec bash "$ROOT/deploy/deploy.sh"
fi

if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "Checkout is not origin/main." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Create .env from .env.example first. A unique DJANGO_SECRET_KEY is required. DJANGO_DEBUG stays 0." >&2
  exit 1
fi

if [[ -f docker-compose.override.yml ]]; then
  echo "Refusing to deploy: docker-compose.override.yml turns debug on." >&2
  echo "Remove it on the VPS. Production debug stays off in docker-compose.yml." >&2
  exit 1
fi

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

env_value() {
  local key="$1"
  local line value first last
  line="$(grep -E "^[[:space:]]*${key}=" .env | tail -n 1 || true)"
  value="$(trim "${line#*=}")"
  if [[ ${#value} -ge 2 ]]; then
    first="${value:0:1}"
    last="${value: -1}"
    if [[ "$first" == "$last" && ( "$first" == '"' || "$first" == "'" ) ]]; then
      value="$(trim "${value:1:${#value}-2}")"
    fi
  fi
  printf '%s' "$value"
}

secret="$(env_value DJANGO_SECRET_KEY)"
case "$secret" in
  ""|change-me|dev-only-not-for-production|build-only-not-used-at-runtime)
    echo "DJANGO_SECRET_KEY in .env is missing or still a placeholder." >&2
    echo "Set a unique value and run deploy.sh again. The running container was not replaced." >&2
    exit 1
    ;;
esac

prod_hosts="spotter.kadamsahil.com,srv1208763.hstgr.cloud,127.0.0.1,localhost"
allowed_hosts="$(env_value DJANGO_ALLOWED_HOSTS)"
allowed_hosts="${allowed_hosts//[[:space:]]/}"
if [[ -z "$allowed_hosts" || "$allowed_hosts" == "localhost,127.0.0.1" ]]; then
  if [[ "$allowed_hosts" == "localhost,127.0.0.1" ]]; then
    echo "DJANGO_ALLOWED_HOSTS in .env is the dev list. Keeping the production host list."
  fi
  export DJANGO_ALLOWED_HOSTS="$prod_hosts"
elif [[ "$allowed_hosts" != *127.0.0.1* ]]; then
  echo "DJANGO_ALLOWED_HOSTS in .env does not include 127.0.0.1. The container healthcheck would fail." >&2
  exit 1
else
  export DJANGO_ALLOWED_HOSTS="$allowed_hosts"
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

pin_running_release() {
  local ref
  for ref in "spotter-web:latest" "spotter_web:latest"; do
    if docker image inspect "$ref" >/dev/null 2>&1; then
      docker tag "$ref" "spotter-web:rollback"
      return 0
    fi
  done
  if docker container inspect spotter-web >/dev/null 2>&1; then
    ref="$(docker container inspect --format '{{.Image}}' spotter-web)"
    if docker image inspect "$ref" >/dev/null 2>&1; then
      docker tag "$ref" "spotter-web:rollback"
      return 0
    fi
  fi
  return 1
}

NEW_TAG="$(git rev-parse --short HEAD)"
PREVIOUS_TAG=""
if [[ -f .deploy-tag ]]; then
  PREVIOUS_TAG="$(tr -d '[:space:]' < .deploy-tag)"
fi
if [[ -z "$PREVIOUS_TAG" || "$PREVIOUS_TAG" == "latest" ]] || ! docker image inspect "spotter-web:${PREVIOUS_TAG}" >/dev/null 2>&1; then
  if [[ -n "$PREVIOUS_TAG" && "$PREVIOUS_TAG" != "latest" ]]; then
    echo "Recorded image spotter-web:${PREVIOUS_TAG} is not on disk. Pinning the running image instead." >&2
  fi
  if pin_running_release; then
    PREVIOUS_TAG="rollback"
  else
    PREVIOUS_TAG=""
  fi
fi

if [[ -n "$PREVIOUS_TAG" && "$PREVIOUS_TAG" != "$NEW_TAG" ]]; then
  printf '%s\n' "$PREVIOUS_TAG" > .deploy-previous-tag
  echo "Previous release kept as spotter-web:${PREVIOUS_TAG}"
  echo "Rollback: IMAGE_TAG=${PREVIOUS_TAG} docker compose -p spotter up -d --no-build"
fi

export IMAGE_TAG="$NEW_TAG"
echo "Releasing spotter-web:${IMAGE_TAG}"
docker compose -p spotter up -d --build

echo "Waiting for Spotter health"
healthy=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8001/api/v1/health; then
    echo
    healthy=1
    break
  fi
  sleep 3
done
if [[ "$healthy" -ne 1 ]]; then
  echo "Spotter health check failed." >&2
  if [[ -n "$PREVIOUS_TAG" && "$PREVIOUS_TAG" != "$NEW_TAG" ]]; then
    echo "Restoring spotter-web:${PREVIOUS_TAG}" >&2
    IMAGE_TAG="$PREVIOUS_TAG" docker compose -p spotter up -d --no-build
  fi
  exit 1
fi

docker tag "spotter-web:${IMAGE_TAG}" spotter-web:latest
printf '%s\n' "$IMAGE_TAG" > .deploy-tag

echo "Post-flight p1"
docker ps --filter name=p1- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -fsS -o /dev/null -w 'p1 %{http_code}\n' https://setup.p1.kadamsahil.com/

echo
echo "spotter-web:${IMAGE_TAG} is running and tagged latest."
echo "App is on 127.0.0.1:8001 only. Nginx is unchanged."
echo "Next, if the vhost is not installed yet: sudo bash deploy/install-nginx.sh"
