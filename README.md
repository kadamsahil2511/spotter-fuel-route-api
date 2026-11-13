# Spotter fuel-route API

Backend Django REST API. It plans a US drive and the truck stops that minimize fuel spend for a 500-mile tank.

## Contents

- [Live demo](#live-demo)
- [What this is](#what-this-is)
- [Quickstart](#quickstart)
- [Project structure](#project-structure)
- [Architecture](#architecture)
- [Environment](#environment)
- [API](#api)
- [Performance](#performance)
- [Data](#data)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Docker deploy](#docker-deploy)
- [Twelve-factor](#twelve-factor)
- [Postman](#postman)
- [Decisions](#decisions)

## Live demo

- Health: https://spotter.kadamsahil.com/api/v1/health
- Chicago, IL → Houston, TX: https://spotter.kadamsahil.com/api/v1/route?start=Chicago,%20IL&finish=Houston,%20TX
- Map: https://spotter.kadamsahil.com/api/v1/map?start=Chicago,%20IL&finish=Houston,%20TX

The same paths on a laptop use `http://127.0.0.1:8000`.

## What this is

This is a backend-only Django REST API. There is no separate frontend. `/api/v1/map` is a small server-rendered Leaflet page so a reviewer can see the line and the stops. The browser loads tiles. It does not call the router.

## Quickstart

Python 3.12 or newer. No Redis, no API key, and no geocoding step. `data/stations.json` is already in the repo. If `REDIS_URL` is unset, the cache and the one-request-per-second OSRM limiter stay in this process.

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && export DJANGO_DEBUG=1 && python manage.py migrate && python manage.py runserver
```

`DJANGO_DEBUG` defaults to off. Local `runserver` sets it to `1`. With debug on and no `DJANGO_SECRET_KEY`, settings use a dev-only key.

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/route?start=Chicago,%20IL&finish=Houston,%20TX'
DJANGO_DEBUG=1 python manage.py test fuel
```

Dependencies in `requirements.txt` are pinned, including transitive packages. They are pure-Python wheels. Nothing needs GDAL or another system library.

## Project structure

| Path | Purpose |
|---|---|
| `manage.py` | Django entry point |
| `config/settings.py` | Settings. LocMem when `REDIS_URL` is empty. Redis when it is set |
| `config/urls.py` | Mounts `fuel.urls` |
| `fuel/views.py` | `/`, `/api/v1/health`, `/route`, `/map` |
| `fuel/services/plan.py` | Ties resolve, route, corridor, and optimizer into the JSON body |
| `fuel/services/resolve.py` | Coordinates, Census gazetteer, or one Photon lookup. US check |
| `fuel/services/routing.py` | One OSRM call. OpenRouteService only if that call fails and a key is set |
| `fuel/services/limiter.py` | One OSRM start per second. Redis in production, in-process otherwise |
| `fuel/services/corridor.py` | Stops within `corridor_miles` of the full route line |
| `fuel/services/optimizer.py` | Minimum dollars on that line. Starting tank is not billed |
| `fuel/services/geo.py` | Haversine, polyline, mile markers, Douglas–Peucker |
| `fuel/services/stations.py` | Loads `data/stations.json` once per process |
| `fuel/services/poi_match.py` | Brand-word overlap required before a Photon hit is a specific stop |
| `fuel/services/regions.py` | US and Canadian state names. Point-in-US boxes |
| `fuel/services/catalog.py` | CSV collapse: one row per OPIS ID, minimum price |
| `fuel/templates/fuel/map.html` | Leaflet page. Popup text is escaped |
| `fuel/tests/` | 25 tests. No network |
| `data/stations.json` | Precomputed stops. Required at runtime |
| `data/us_places.json` | Census place centroids for `City, ST` |
| `data/fuel-prices.csv` | Source prices. Not read on a request |
| `scripts/geocode_stations.py` | Resumable offline geocoder |
| `scripts/build_places.py` | Rebuilds the Census gazetteer JSON |
| `Dockerfile`, `docker-compose.yml` | Gunicorn on `127.0.0.1:8001` plus a private Redis. Image tag is `IMAGE_TAG` (default `latest`) |
| `docker-compose.override.example.yml` | Local debug on. Copy to `docker-compose.override.yml` |
| `docker/entrypoint.sh` | Gunicorn, or the command you pass. Refuses debug when `DOMAIN` is set |
| `deploy/deploy.sh` | VPS release from `origin/main`. Checks p1 first. Tags the image with the git SHA |
| `deploy/install-nginx.sh` | New nginx site, `nginx -t`, reload, optional Certbot |
| `deploy/nginx/spotter.kadamsahil.com.conf` | Proxy to `127.0.0.1:8001` |
| `postman/spotter-fuel-route.postman_collection.json` | Collection. `baseUrl` defaults to localhost:8000 |
| `docs/` | Longer reference: API fields, data, tests, deploy, decisions |

## Architecture

```mermaid
flowchart LR
  client[Client] --> api[Django route view]
  api --> geo{Place}
  geo -->|City ST or coordinates| local[Census gazetteer]
  geo -->|street address| photon[Photon once]
  api --> cache{Plan cache}
  cache -->|hit| json[JSON response]
  cache -->|miss| lease[OSRM lease 1 per second]
  lease --> osrm[OSRM full geometry]
  osrm -->|failure and ORS key| ors[OpenRouteService]
  osrm --> corridor[Corridor match]
  ors --> corridor
  corridor --> opt[Optimizer]
  opt --> json
  json --> map[Leaflet map page]
```

Detail is in [docs/decisions.md](docs/decisions.md).

## Environment

Copy `.env.example` only if you want to override a default. Local `runserver` needs `DJANGO_DEBUG=1`. The other variables can stay unset. `.env.example` itself has debug off and `DJANGO_SECRET_KEY=change-me`, so sourcing it will refuse to boot until you set `DJANGO_DEBUG=1` or replace the key.

| Variable | Default | Used for |
|---|---|---|
| `DJANGO_DEBUG` | `0` | Set `1` for local `runserver` and the Compose override. Compose production forces `0`. Debug on plus `DOMAIN` set refuses to boot in Docker |
| `DJANGO_SECRET_KEY` | dev key when debug is on | Required when debug is off. Empty, `change-me`, and `dev-only-not-for-production` refuse to boot |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` in settings | Compose default is `spotter.kadamsahil.com`, `srv1208763.hstgr.cloud`, `127.0.0.1`, `localhost` when this is unset or empty. A non-empty `.env` value replaces that list. Keep the loopback names for the healthcheck |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty in settings | Compose default `https://spotter.kadamsahil.com` when this is unset or empty. The API does not use session auth |
| `LOG_LEVEL` | `INFO` | Django log level on stdout. `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `SQLITE_PATH` | `db.sqlite3` | Django tables only. `/data/db.sqlite3` in Docker |
| `REDIS_URL` | empty | Empty: LocMem and an in-process limiter. Compose sets `redis://redis:6379/0` |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | Driving route. No key |
| `PHOTON_BASE_URL` | `https://photon.komoot.io` | Street addresses and towns missing from the gazetteer. No key |
| `ORS_BASE_URL` | `https://api.heigit.org` | Used only after OSRM fails |
| `ORS_API_KEY` | empty | Leave empty. One directions call when OSRM fails and this is set |
| `ROUTING_USER_AGENT` | `SpotterFuelRoute/1.0 (assessment)` | Sent on every outbound call |
| `STATIONS_PATH` | `data/stations.json` | Stop file |
| `PLACES_PATH` | `data/us_places.json` | Census places |
| `DOMAIN` | empty in settings | Compose default `spotter.kadamsahil.com` when this is unset or empty. Debug on with `DOMAIN` set refuses to boot |
| `WEB_CONCURRENCY` | `2` in the image | Gunicorn processes. Local `runserver` ignores it |
| `GUNICORN_THREADS` | `4` in the image | Threads per worker |
| `CORRIDOR_MILES`, `TANK_RANGE_MILES`, `MPG` | `10`, `500`, `10` | Listed in `.env.example`. The request overrides them. They are not read from the environment by the view |

## API

`GET` or `POST /api/v1/route`. Places are `City, ST`, `lat,lng`, or `{"lat","lng"}`. Defaults: 500 mile tank, 10 mpg, 10 mile corridor.

Chicago → Houston, measured. Geometry shortened. 209 points in the live body (`docs/samples/chi-hou-cold.json`):

```json
{
  "feasible": true,
  "start": {"label": "Chicago, IL", "lat": 41.837045, "lng": -87.684939, "resolved_by": "gazetteer"},
  "finish": {"label": "Houston, TX", "lat": 29.785743, "lng": -95.388806, "resolved_by": "gazetteer"},
  "route": {
    "distance_miles": 1083.1,
    "duration_seconds": 71592,
    "geometry": {"type": "LineString", "coordinates": [[-87.68489, 41.83705], [-95.38881, 29.78576]]},
    "provider": "osrm"
  },
  "total_gallons": 58.39,
  "total_fuel_cost": 168.06,
  "fuel_stops": [
    {
      "opis_id": "69861",
      "name": "HUCKS FOOD & FUEL #379",
      "city": "Marion",
      "state": "IL",
      "price_per_gallon": 2.929,
      "mile_marker": 312.1,
      "gallons": 28.881,
      "cost": 84.59,
      "geocode_quality": "poi"
    }
  ],
  "gap": null,
  "external_calls": {"geocode": 0, "route": 1},
  "cache": "miss",
  "elapsed_ms": 1006.9
}
```

The other stops are Texarkana, Marshall, Carthage, and Lufkin. New York → Los Angeles is 2,810.4 miles, 17 stops, $697.28 (`docs/samples/ny-la-cold.json`).

Every field, with units, and the error bodies (missing input, unknown city, Canada, infeasible `gap`) are in [docs/api.md](docs/api.md).

## Performance

Measured with `runserver`, no Redis, against the public OSRM demo. `elapsed_ms` is server time.

| Route | Cold `elapsed_ms` | Cold wall | Calls | Warm `elapsed_ms` | Warm wall | Calls |
|---|---|---|---|---|---|---|
| Chicago → Houston | 1006.9 | 1.01 s | 1 route, 0 geocode | 0.2 | 0.002 s | 0 |
| New York → Los Angeles | 1860.8 | 1.86 s | 1 route, 0 geocode | 0.3 | 0.003 s | 0 |

A cold miss stores the finished plan. The key is the rounded coordinates, tank, mpg, corridor, and the station-file timestamp. A hit does not call OSRM or Photon. The road geometry is cached separately, so a station-file change does not repeat the OSRM call. Before a miss, the process takes a one-second lease so two workers cannot both call OSRM. The public demo's limit is per source IP, so other containers on the VPS share it.

## Data

`data/stations.json` was built offline. 6,738 stops: **860** Photon POIs whose name matches the stop, **5,823** city centroids (Census, or a fallback when the POI was not specific), **55** unresolved and excluded. How to regenerate: [docs/data.md](docs/data.md).

## Testing

```bash
DJANGO_DEBUG=1 python manage.py test fuel
```

25 tests, no network. What each file covers: [docs/testing.md](docs/testing.md).

## Troubleshooting

macOS, Apple Silicon or Intel. The pins are pure Python and do not need Homebrew libraries.

- `python3 --version` should be 3.12 or newer. `python3 -m venv .venv` uses that interpreter. If `venv` is missing, install Python from python.org. The project does not need `python3-venv` from Homebrew.
- Activate with `source .venv/bin/activate`. `source .venv/bin/activate.fish` on fish. The prompt should show `(.venv)` before `pip` and `python`.
- `Address already in use` means something is on port 8000. `lsof -nP -iTCP:8000 -sTCP:LISTEN`, stop it, or `python manage.py runserver 127.0.0.1:8001` and curl that port.
- A first Chicago → Houston call takes about a second because OSRM does. A hang past about 15 seconds, or HTTP 502 `Routing failed`, is the public demo timing out or refusing. Wait and repeat the same URL. The second call should be a cache hit. Do not loop the endpoint. The limiter allows about one new route per second.
- `Invalid HTTP_HOST header` on the laptop means `DJANGO_ALLOWED_HOSTS` was overridden and dropped `localhost`. Unset it, or set `localhost,127.0.0.1`.
- `DJANGO_SECRET_KEY must be a unique value` means debug is off and the key is missing or still `change-me`. For local `runserver` and tests, `export DJANGO_DEBUG=1`.

## Docker deploy

For the VPS that already runs p1. Not required to try the API.

`DJANGO_ALLOWED_HOSTS` defaults in Compose to `spotter.kadamsahil.com`, `srv1208763.hstgr.cloud`, `127.0.0.1`, and `localhost` when the variable is unset or empty. A non-empty value in `.env` replaces that list. `127.0.0.1` and `localhost` stay in the default because the container healthcheck calls `http://127.0.0.1:8001/api/v1/health`. Without them Django returns 400 and Docker marks `spotter-web` unhealthy. Public requests still use the domain, through nginx.

```bash
cp .env.example .env
# Set a unique DJANGO_SECRET_KEY. Leave DJANGO_DEBUG=0. Compose sets REDIS_URL.
bash deploy/deploy.sh
sudo bash deploy/install-nginx.sh
sudo ISSUE_CERT=1 bash deploy/install-nginx.sh
```

`deploy/deploy.sh` checks out `origin/main`, builds `spotter-web:<git short sha>`, and tags `latest` as an alias. `IMAGE_TAG` in Compose defaults to `latest`, so an `.env` without that variable still starts. The previous tag is kept in `.deploy-previous-tag`.

DNS is an A record `spotter` → `72.61.242.96`. No AAAA. Previous image, p1 stays up: `IMAGE_TAG="$(cat .deploy-previous-tag)" docker compose -p spotter up -d --no-build`. Stop Spotter and keep its volumes: `docker compose -p spotter down`.

One-off admin command, on the VPS with the project name:

```bash
docker compose -p spotter run --rm web python manage.py <cmd>
```

Locally the same form is `docker compose run --rm web python manage.py <cmd>`.

### Local Docker

Same Compose file as the VPS. The override turns debug on and clears `DOMAIN` so the entrypoint will boot:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose up --build
```

Web and Redis start. The app is at `http://127.0.0.1:8001`. Leave `docker-compose.override.yml` off the VPS. `deploy/deploy.sh` exits if that file is present.

Steps, the p1 checks, and the swap note: [docs/deploy.md](docs/deploy.md).

## Twelve-factor

| Factor | Where it lives |
|---|---|
| Codebase | This git repo. `deploy/deploy.sh` fetches and checks out `origin/main` |
| Dependencies | Pinned `requirements.txt`. Image `python:3.12.16-slim` |
| Config | Environment variables. Debug defaults off in settings and the image. Debug off refuses a missing or dev `DJANGO_SECRET_KEY`. Compose hostnames default to the current production names when unset |
| Backing services | `REDIS_URL`, `SQLITE_PATH`, `OSRM_BASE_URL`, `PHOTON_BASE_URL`, `ORS_BASE_URL` |
| Build, release, run | `deploy/deploy.sh` builds `spotter-web:<short sha>`, tags `latest`, and records the previous tag. Compose `IMAGE_TAG` defaults to `latest` |
| Processes | Gunicorn workers stay stateless. Finished plans are stored in Redis or LocMem |
| Port binding | Gunicorn binds `0.0.0.0:8001`. Compose publishes `127.0.0.1:8001` |
| Concurrency | `WEB_CONCURRENCY` and `GUNICORN_THREADS` |
| Disposability | Compose `stop_grace_period: 15s`. Gunicorn `--graceful-timeout 10` |
| Dev/prod parity | One image and `docker-compose.yml`. `docker-compose.override.example.yml` runs web and Redis locally with debug on |
| Logs | Django `LOGGING` writes stdout. `LOG_LEVEL` defaults to `INFO`. Gunicorn access and error logs go to stdout and stderr |
| Admin processes | `docker compose run --rm web python manage.py <cmd>`. The entrypoint starts Gunicorn when no command is passed |

## Postman

Import `postman/spotter-fuel-route.postman_collection.json`. `baseUrl` is `http://127.0.0.1:8000`. On the VPS set it to `https://spotter.kadamsahil.com`.

Requests: health, Chicago → Houston, New York → Los Angeles, Chicago as lat/lng, New York → Philadelphia (under 500 miles), Toronto rejected, Chicago map.

## Decisions

One OSRM route, then the cheapest pumps on that line. Full tank at the start is not billed. Redis only when more than one process must share the cache. The longer write-up and the list of later work: [docs/decisions.md](docs/decisions.md).

Map tiles © OpenStreetMap contributors. Routes from the OSRM demo or OpenRouteService. Places from Photon and the US Census Bureau. Prices from the assessment CSV.
