# Spotter fuel-route API

Django API that plans a US driving route, picks cost-effective truck-stop fuel stops for a 500-mile tank, and returns the route geometry, the stops, the fuel bill, and a map.

The vehicle starts with a full tank. Those first 500 miles are not billed. Fuel bought along the way is priced from the attached OPIS truck-stop file at 10 miles per gallon. Stops are chosen to minimize dollars on the fastest road route, not to minimize the number of stops.

## Stack

| Piece | Version / choice |
|---|---|
| Python | 3.12 |
| Django | 6.1.3 |
| Django REST framework | 3.18.3 |
| App server | Gunicorn, 2 `gthread` workers, 4 threads |
| Cache and OSRM limiter | Redis 7 (`django-redis`), Spotter's own container |
| Database | SQLite for Django's tables only |
| Stations | `data/stations.json`, loaded in memory |
| Endpoint places | `data/us_places.json` (US Census places) |
| Routing | [OSRM demo](https://router.project-osrm.org) (`overview=full`, polyline). [OpenRouteService](https://api.heigit.org) only if OSRM fails and `ORS_API_KEY` is set |
| Geocoding | Census gazetteer, then [Photon](https://photon.komoot.io) for a street address or a town the gazetteer does not have |
| Edge on the VPS | The host nginx + Certbot already in front of project p1. This app does not bind 80/443 |

OSRM's public demo allows about one request per second per source IP, with an identifying User-Agent and ODbL attribution. Photon is a demo server with no availability promise. Both are called with `SpotterFuelRoute/1.0 (assessment)`.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

No geocoding step. `data/stations.json` and `data/us_places.json` are already in the repo. `REDIS_URL` unset uses an in-process cache, which is enough for one `runserver` process. Production refuses to boot without Redis.

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/route?start=Chicago,%20IL&finish=Houston,%20TX'
```

Open the map:

```text
http://127.0.0.1:8000/api/v1/map?start=Chicago,%20IL&finish=Houston,%20TX
```

`start` and `finish` accept `City, ST`, `City, State`, `lat,lng`, or `{"lat", "lng"}`. Optional `range_miles` (500), `mpg` (10), and `corridor_miles` (10).

Tests:

```bash
.venv/bin/python manage.py test fuel
```

Postman: import `postman/spotter-fuel-route.postman_collection.json`. The `baseUrl` variable defaults to `http://127.0.0.1:8000`. On the VPS set it to `https://spotter.kadamsahil.com`.

## Sample runs

Measured on the dev server against the public OSRM demo, with geocoding calls at 0 because both cities are in the Census gazetteer. `elapsed_ms` is server time. Wall time is curl's total.

Chicago, IL → Houston, TX. 1,083.1 miles, feasible, 5 stops, **$168.06**.

| | HTTP | server `elapsed_ms` | curl wall | cache | OSRM calls |
|---|---|---|---|---|---|
| Cold | 200 | 1006.9 | 1.01 s | miss | 1 |
| Warm | 200 | 0.2 | 0.002 s | hit | 0 |

New York, NY → Los Angeles, CA. 2,810.4 miles, feasible, 17 stops, **$697.28**.

| | HTTP | server `elapsed_ms` | curl wall | cache | OSRM calls |
|---|---|---|---|---|---|
| Cold | 200 | 1860.8 | 1.86 s | miss | 1 |
| Warm | 200 | 0.3 | 0.003 s | hit | 0 |

A warm hit is a Redis (or local memory) read of the finished plan. The public OSRM call is the cold-path cost. Full response bodies, with geometry shortened in the README only, are in `docs/samples/`.

Chicago cold excerpt:

```json
{
  "feasible": true,
  "start": {"label": "Chicago, IL", "lat": 41.837045, "lng": -87.684939, "resolved_by": "gazetteer"},
  "finish": {"label": "Houston, TX", "lat": 29.785743, "lng": -95.388806, "resolved_by": "gazetteer"},
  "route": {"distance_miles": 1083.1, "provider": "osrm"},
  "fuel_stops": [
    {"name": "HUCKS FOOD & FUEL #379", "city": "Marion", "state": "IL", "mile_marker": 312.1, "price_per_gallon": 2.929, "gallons": 28.881, "cost": 84.59, "geocode_quality": "poi"}
  ],
  "total_gallons": 58.39,
  "total_fuel_cost": 168.06,
  "external_calls": {"geocode": 0, "route": 1},
  "cache": "miss"
}
```

New York cold excerpt:

```json
{
  "feasible": true,
  "route": {"distance_miles": 2810.4, "provider": "osrm"},
  "fuel_stops_count": 17,
  "first_stop": {"name": "SHEETZ #639", "city": "Youngstown", "state": "OH", "mile_marker": 395.4},
  "last_stop": {"name": "Maverik #674", "city": "North Las Vegas", "state": "NV", "mile_marker": 2517.4},
  "total_fuel_cost": 697.28,
  "external_calls": {"geocode": 0, "route": 1},
  "cache": "miss"
}
```

The tank from North Las Vegas covers the rest of the way into Los Angeles. The CSV has no Los Angeles truck stop (see coverage below). The map for Chicago → Houston is `docs/samples/chicago-houston-map.png`.

A repeat of either URL returns `"cache": "hit"` and `"external_calls": {"geocode": 0, "route": 0}`.

## How a request is answered

1. Resolve start and finish. Coordinates skip the network. `City, ST` hits `data/us_places.json`. Anything else is one Photon search, cached. Canadian state codes and coordinates outside the US boxes are HTTP 400.
2. Reuse a cached plan when the rounded coordinates, tank, mpg, corridor, and station-file timestamp match.
3. Otherwise one OSRM driving route (`overview=full`, so the line is dense enough for a 10-mile corridor). A Redis key `spotter:osrm:lease` with a 1 second TTL is taken first, so both Gunicorn workers share one call per second. The limit is per public IP: everything else on `72.61.242.96`, including p1, draws from the same OSRM budget.
4. If OSRM returns a non-`Ok` code, HTTP 429, or times out, and `ORS_API_KEY` is set, one OpenRouteService directions call replaces it. Geocoding does not move to ORS. With no key the failure is HTTP 502. Cached routes still answer.
5. Project stations onto that line. Keep those within `corridor_miles` (default 10). Unresolved stations are ignored.
6. Run the fuel planner. Simplify the line (~500 m) for the JSON and the map.

External calls per request, after stations are precomputed:

| Input | Calls |
|---|---|
| Coordinates or `City, ST` in the gazetteer | 1 OSRM |
| One street address | 1 Photon + 1 OSRM |
| Two street addresses | 2 Photon + 1 OSRM |
| Same pair again | 0 |

The map page is rendered on the server from that same plan. The browser loads OSM tiles only. It does not call OSRM.

## Fuel planner

Work in miles. The tank holds `range_miles` (500). Price is dollars per gallon. Gallons are miles divided by `mpg` (10).

The origin is not a pump. The tank is already full and the fuel in it has price 0, so it is never billed. A trip of 500 miles or less has no stop and a fuel cost of $0. Total gallons purchased on a longer trip is whatever the stops actually sell, which equals `(distance - 500) / mpg` only when the planner can buy exactly the miles it burns. Because you can only buy at a pump you have reached, the gallons match that figure when the plan is feasible and you arrive empty. You do not buy fuel past the destination.

Stations are sorted by mile marker. From the current point:

- If the tank already reaches the destination, stop.
- If a pump inside reach is cheaper than the fuel you would buy here, buy only enough to reach that pump (or drive there on fuel you already have).
- If the destination is inside a full tank and no cheaper pump is, buy only enough to arrive empty.
- If every pump still in range is at least as expensive, fill the tank and drive to the cheapest one.
- If nothing is in range, the plan is infeasible. The response is still HTTP 200, with the route, an empty stop list, and `gap.from_mile` / `gap.to_mile`. The truck is not given a stop list that strands it.

Detour off the highway is not added to the gallons. The 10-mile corridor is only the test for "along the route". Cross-track distance is reported on each stop.

Conflicting rows for one OPIS Truckstop ID use the minimum retail price. There is no timestamp and no fuel-grade column, so that minimum can mix grades. The name and address kept are the most common ones for that ID.

The planner does not look at a second highway. `alternatives=false` on the OSRM call, so the map shows one route.

## Station coordinates

`scripts/geocode_stations.py` (also `manage.py geocode_stations`) is resumable.

1. Collapse the CSV to one row per OPIS ID (minimum price).
2. City coordinate: US Census populated-place interior point when `City, ST` matches `data/us_places.json`. Otherwise Photon, and only a hit whose name and state match.
3. One Photon search per stop, biased to that city, accepting a fuel or highway-services POI, or a name-token match, within 25 miles of the city.
4. If no POI was accepted, the stop keeps the city coordinate with `geocode_quality=city_centroid`. Stops with no coordinate are `unresolved` and are not used.

Progress is `data/geocode/cities.json` and `data/geocode/pois.json` (gitignored). `data/stations.json` is rewritten as the run goes and is the file the app loads. Reviewers do not need to rerun it.

Coverage for the committed file is printed at the bottom of this README under **Geocoding coverage** and is whatever the run had finished when the file was committed. City centroids are eligible. A large city's interior point often sits more than 10 miles off the interstate, so those stops simply do not enter the corridor. Small towns on the highway are the ones the planner uses.

California in this CSV is 16 rows, all in the Imperial Valley and Coachella Valley (El Centro, Calexico, Brawley, Westmorland, Thermal, Jacumba, Coachella). There is no Los Angeles, Central Valley, or Bay Area stop. New York → Los Angeles still plans because the last Nevada or eastern-California stop leaves less than 500 miles. A route that enters California farther north can come back `feasible: false` with a mile gap.

## Deploy

Do this on the VPS (`72.61.242.96`). Do not point this compose project at p1. p1 is nginx → `127.0.0.1:3000`, plus `p1-evolution-1` on `127.0.0.1:8080`, `p1-redis-1`, and `p1-postgres-1`, on network `p1_default`. Spotter does not join that network, does not use that Redis or Postgres, and does not edit the p1 site file.

The box has 1 vCPU, 3.9 GB RAM, about 2 GB free, no swap, and p1's app container is about 1.1 GB. Spotter's compose caps are `spotter-web` 512 MB / 0.75 CPU and `spotter-redis` 96 MB. CPU shares are lower than p1's default so p1 wins when the core is busy.

A 2 GB swapfile with `vm.swappiness=10` is recommended before the first start, because a spike with no swap can make the OOM killer pick p1. That is a host change and needs your yes. The app deploy does not create it.

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf && sudo sysctl -p /etc/sysctl.d/99-swappiness.conf
```

Then, on the VPS, in a clone of this repo:

```bash
cp .env.example .env
# Set DJANGO_DEBUG=0, a real DJANGO_SECRET_KEY, and optionally ORS_API_KEY.
# REDIS_URL is overridden by compose to redis://redis:6379/0.
bash deploy/deploy.sh
```

`deploy/deploy.sh` checks that four `p1-` containers are running and that `https://setup.p1.kadamsahil.com/` returns 200, then runs:

```bash
docker compose -p spotter up -d --build
```

That publishes only `127.0.0.1:8001`. It does not reload nginx.

DNS, in the Hostinger zone for `kadamsahil.com`, one new record. Leave `setup.p1` alone.

| Name | Type | Value |
|---|---|---|
| `spotter` | A | `72.61.242.96` |

No AAAA. Confirm the existing records before writing this one.

After `dig +short spotter.kadamsahil.com` is `72.61.242.96`:

```bash
sudo bash deploy/install-nginx.sh
sudo ISSUE_CERT=1 bash deploy/install-nginx.sh
```

The script installs `deploy/nginx/spotter.kadamsahil.com.conf` as `/etc/nginx/sites-available/spotter.kadamsahil.com`, symlinks `sites-enabled`, runs `nginx -t`, and only then `systemctl reload nginx`. It never restarts nginx and never edits the p1 site. `certbot --nginx -d spotter.kadamsahil.com` adds the certificate. A later run will not overwrite a file that already has `ssl_certificate` unless `FORCE=1`.

Check:

```bash
curl -fsS http://127.0.0.1:8001/api/v1/health
curl -fsS https://spotter.kadamsahil.com/api/v1/health
curl -fsS -o /dev/null -w 'p1 %{http_code}\n' https://setup.p1.kadamsahil.com/
docker ps --filter name=p1- --format 'table {{.Names}}\t{{.Status}}'
```

Rollback of the app, which does not stop p1:

```bash
docker compose -p spotter down
```

Volumes `spotter_sqlite` and `spotter_redis` are kept. To remove the vhost, delete the Spotter symlink only, then `nginx -t && systemctl reload nginx`.

`spotter-redis` is `redis:7-alpine`, not published on the host, `--maxmemory 64mb --maxmemory-policy allkeys-lru`, AOF every second plus an RDB snapshot, on volume `spotter_redis`. If the 96 MB cap kills it during an AOF rewrite, drop `--appendonly` and keep `--save 60 1` rather than raising the cap. SQLite at `/data/db.sqlite3` on volume `spotter_sqlite` holds Django's tables only.

Gunicorn inside the container binds `0.0.0.0:8001` so Docker can proxy it. The host socket is `127.0.0.1:8001` only.

Environment variables are listed in `.env.example`. Do not commit `.env`.

## Attribution

Map tiles © OpenStreetMap contributors. Routes from OSRM (FOSSGIS demo) or OpenRouteService (HeiGIT). Place search from Photon (komoot) and the US Census Bureau gazetteer. Truck-stop prices are the assessment CSV.

## Geocoding coverage

Committed `data/stations.json` has 6,738 stops (one per OPIS id):

| Quality | Stops |
|---|---|
| `poi` (Photon fuel / name match) | 1,034 |
| `city_centroid` (Census place, or Photon city when the POI pass had not accepted a hit) | 5,649 |
| `unresolved` (no coordinate; excluded from planning) | 55 |

6,683 stops have coordinates. The POI pass was stopped so this could be submitted; it is resumable with `python3 scripts/geocode_stations.py --poi-only`. City centroids are what the Chicago and New York sample plans used for most stops. A large-city interior point farther than 10 miles from the highway is simply not selected.
