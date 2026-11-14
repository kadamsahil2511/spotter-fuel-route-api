# Deploy

The VPS is Hostinger KVM 1 (`72.61.242.96`, `srv1208763.hstgr.cloud`): 1 vCPU, 3.9 GB RAM, no swap, about 2 GB free, about 35 GB disk free, Ubuntu 24.04. Host nginx and Certbot own ports 80 and 443. `ufw` is inactive. There is no Caddy, Coolify, or Traefik.

p1 is already there and must keep running:

| Container | Host port |
|---|---|
| `p1-p1-1` | `127.0.0.1:3000` |
| `p1-evolution-1` | `127.0.0.1:8080` |
| `p1-redis-1` | none |
| `p1-postgres-1` | none |

Network `p1_default`. A stopped `tech-news-assistant` stack is left stopped.

Spotter is Compose project `spotter`, containers `spotter-web` and `spotter-redis`, network `spotter_default`, volumes `spotter_sqlite` and `spotter_redis`. It does not use p1's Redis or Postgres.

## Healthcheck and allowed hosts

The image healthcheck and the Compose healthcheck request `http://127.0.0.1:8001/api/v1/health` from inside the container. Django sees `Host: 127.0.0.1`. The Compose default for `DJANGO_ALLOWED_HOSTS` includes `127.0.0.1` and `localhost` along with `spotter.kadamsahil.com` and `srv1208763.hstgr.cloud` when the variable is unset or empty. A non-empty `.env` value replaces that list. Public traffic still arrives as `spotter.kadamsahil.com` through nginx. Keep the loopback names or the container stays `unhealthy` (HTTP 400).

## Commands

On the VPS, in a clone of this repo:

```bash
cp .env.example .env
# A unique DJANGO_SECRET_KEY. Leave DJANGO_DEBUG=0. Compose sets REDIS_URL.
bash deploy/deploy.sh
```

`deploy/deploy.sh` refuses to start unless four `p1-` containers are running and `https://setup.p1.kadamsahil.com/health` returns 200. It fetches `origin/main`, checks out that commit, and runs:

```bash
IMAGE_TAG=<git short sha> docker compose -p spotter up -d --build
```

The image is `spotter-web:<sha>`. After the health check passes, that image is also tagged `latest`. The previous tag is written to `.deploy-previous-tag` and left on disk. If Compose fails while replacing the container, or the health check fails, and a previous tag exists, the script starts that tag again. `DJANGO_ALLOWED_HOSTS` must list `127.0.0.1` as its own entry. A value that only contains those characters inside another host, such as `127.0.0.10`, is refused. `IMAGE_TAG` defaults to `latest` when Compose is started without the script, so an `.env` that never mentions `IMAGE_TAG` still resolves.

The first run of this script on an existing clone has to update the checkout before the new script is what executes:

```bash
git fetch origin main && git checkout -f -B main origin/main && bash deploy/deploy.sh
```

Later deploys are `bash deploy/deploy.sh`. That checkout discards tracked local edits. `.env` is untracked and stays.

No new variable is required. Optional: `LOG_LEVEL` (default `INFO`). `DJANGO_SECRET_KEY` has to be unique. The app refuses to boot when debug is off and the key is empty, `change-me`, or `dev-only-not-for-production`. The script checks that before it replaces the container. If `.env` still has `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1` from the old example, the script keeps the production host list for that deploy. Remove that line, or set the public names plus `127.0.0.1,localhost`, before a manual `docker compose up`. Empty `DOMAIN` and `DJANGO_CSRF_TRUSTED_ORIGINS` keep the Compose defaults (`spotter.kadamsahil.com` and `https://spotter.kadamsahil.com`). Do not copy `docker-compose.override.example.yml` to `docker-compose.override.yml` on the server. The script exits when that override is present.

Admin commands are one-off processes. The entrypoint starts Gunicorn when it is given no command:

```bash
docker compose -p spotter run --rm web python manage.py <cmd>
```

DNS, one new record in the Hostinger zone. Do not change `setup.p1`. No AAAA.

| Name | Type | Value |
|---|---|---|
| `spotter` | A | `72.61.242.96` |

```bash
sudo bash deploy/install-nginx.sh
sudo ISSUE_CERT=1 bash deploy/install-nginx.sh
```

`install-nginx.sh` copies `deploy/nginx/spotter.kadamsahil.com.conf` to `/etc/nginx/sites-available/spotter.kadamsahil.com`, symlinks `sites-enabled`, runs `nginx -t`, and only then `systemctl reload nginx`. It never restarts nginx and never edits the p1 site. `ISSUE_CERT=1` runs `certbot --nginx -d spotter.kadamsahil.com` and reloads again. A file that already contains `ssl_certificate` is left alone unless `FORCE=1`.

```bash
curl -fsS http://127.0.0.1:8001/api/v1/health
curl -fsS https://spotter.kadamsahil.com/api/v1/health
curl -fsS -o /dev/null -w 'p1 %{http_code}\n' https://setup.p1.kadamsahil.com/
```

Previous image. Volumes stay. p1 stays up. `.deploy-previous-tag` is the release that was running before the last successful deploy.

```bash
IMAGE_TAG="$(cat .deploy-previous-tag)" docker compose -p spotter up -d --no-build
```

Stop Spotter and keep volumes:

```bash
docker compose -p spotter down
```

## Swap

The box has no swap. p1's app container is about 1.1 GB. Spotter's caps add 512 MB and 96 MB. A spike can make the OOM killer pick p1. A 2 GB swapfile with `vm.swappiness=10` is recommended and is not part of `deploy.sh`. Create it only if you mean to change the host:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf && sudo sysctl -p /etc/sysctl.d/99-swappiness.conf
```

## Process

Gunicorn inside the container binds `0.0.0.0:8001` so Docker can proxy it. The only host socket is `127.0.0.1:8001`. Two `gthread` workers, four threads each. `spotter-redis` is `redis:7-alpine`, not published, `--maxmemory 64mb --maxmemory-policy allkeys-lru`, AOF every second and an RDB save, memory limit 96 MB.
