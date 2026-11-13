# Spotter fuel-route API

Django API that plans a US driving route, chooses truck stops that minimize fuel spend for a 500-mile tank, and returns the route, the stops, the bill, and a map.

## Quickstart

Python 3.12 or newer. No Redis, no API key, and no geocoding step. `data/stations.json` is already in the repo. If `REDIS_URL` is unset, the cache and the one-request-per-second OSRM limiter stay in this process.

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python manage.py migrate && python manage.py runserver
```

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/route?start=Chicago,%20IL&finish=Houston,%20TX'
```

Map: http://127.0.0.1:8000/api/v1/map?start=Chicago,%20IL&finish=Houston,%20TX

```bash
python manage.py test fuel
```

Every direct and transitive dependency is pinned in `requirements.txt`. They are pure-Python wheels (no GDAL or other system libraries). Defaults live in `config/settings.py`. `.env.example` lists the same variables; copying it is optional for local work. `ORS_API_KEY` is blank unless you want a fallback when the public OSRM demo fails.

## API reference

`GET` or `POST /api/v1/route`

| Field | Default | Meaning |
|---|---|---|
| `start`, `finish` | required | `City, ST`, `City, State`, `"lat,lng"`, or `{"lat", "lng"}` |
| `range_miles` | 500 | Tank range |
| `mpg` | 10 | Miles per gallon |
| `corridor_miles` | 10 | How far off the route a stop may sit |

`GET /api/v1/health` returns `200` with the number of loaded stations. `GET /api/v1/map` is the same plan as HTML with a Leaflet map. `GET /` lists the paths.

Outside the US is HTTP 400. OSRM down, with no `ORS_API_KEY`, is HTTP 502. A route that cannot be fueled is still HTTP 200, with `"feasible": false` and a `gap`.

Request:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/route?start=Chicago,%20IL&finish=Houston,%20TX'
```

Response from that call (geometry shortened; the live body has 209 points, and the file is `docs/samples/chi-hou-cold.json`):

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
  "assumptions": {
    "tank_range_miles": 500,
    "mpg": 10,
    "start_tank": "full",
    "initial_fuel_charged_usd": 0,
    "corridor_miles": 10,
    "detour_fuel": "not charged"
  },
  "fuel_stops": [
    {
      "opis_id": "69861",
      "name": "HUCKS FOOD & FUEL #379",
      "address": "I-57, EXIT 53",
      "city": "Marion",
      "state": "IL",
      "price_per_gallon": 2.929,
      "location": {"lat": 37.74033, "lng": -88.918029},
      "mile_marker": 312.1,
      "cross_track_miles": 2.12,
      "gallons": 28.881,
      "cost": 84.59,
      "geocode_quality": "poi"
    }
  ],
  "total_gallons": 58.39,
  "total_fuel_cost": 168.06,
  "gap": null,
  "map_path": "/api/v1/map?start=Chicago%2C+IL&finish=Houston%2C+TX&range_miles=500.0&mpg=10.0&corridor_miles=10.0",
  "external_calls": {"geocode": 0, "route": 1},
  "cache": "miss",
  "elapsed_ms": 1006.9
}
```

The other four stops on this plan are Texarkana, Marshall, Carthage, and Lufkin, all in Texas. New York, NY → Los Angeles, CA is 2,810.4 miles, 17 stops, **$697.28** (`docs/samples/ny-la-cold.json`). The last stop is Maverik in North Las Vegas at mile 2,517; the tank covers the rest of the way into Los Angeles.

| Route | Cold `elapsed_ms` | Cold wall | Warm `elapsed_ms` | Warm wall |
|---|---|---|---|---|
| Chicago → Houston | 1006.9 | 1.01 s | 0.2 | 0.002 s |
| New York → Los Angeles | 1860.8 | 1.86 s | 0.3 | 0.003 s |

A warm response has `"cache": "hit"` and `"external_calls": {"geocode": 0, "route": 0}`.

Coordinates or `City, ST` cost one OSRM call. One street address adds one Photon call. Two street addresses add two. The same pair again costs nothing.

## How the optimizer works

1. Resolve the endpoints. Coordinates skip the network. `City, ST` hits `data/us_places.json` (US Census place centroids). Anything else is one Photon search, cached.
2. Take one OSRM driving route with `overview=full` so the line is dense enough for a 10-mile corridor. Before the call, wait for a one-per-second lease. With `REDIS_URL` set, that lease is a Redis `SET NX PX 1000` shared by every Gunicorn worker. With `REDIS_URL` unset, the lease is in this process. If OSRM fails and `ORS_API_KEY` is set, one OpenRouteService directions call replaces it.
3. Keep truck stops from `data/stations.json` whose cross-track distance to that line is at most `corridor_miles`. Unresolved stops are dropped.
4. Plan purchases along the mile markers.

The tank starts full. That fuel has price 0 and is not billed. Work in miles; gallons are miles divided by `mpg`.

- A trip of `range_miles` or less has no stop and costs $0.
- From the current point, if a pump you can reach is cheaper than the fuel you would buy here, buy only enough to reach it (or drive there on fuel you already have).
- If the destination is inside a full tank and nothing ahead is cheaper, buy only enough to arrive empty.
- If every pump still in range costs at least as much as the current pump, fill the tank and drive to the cheapest of them.
- If nothing is in range even with a full tank, the plan is infeasible. The route is still returned, `fuel_stops` is empty, and `gap` says where the truck would run dry.

Stops are the minimum dollar cost on this one route. The planner does not search for a cheaper highway, and it does not minimize the number of stops. Detour off the line is not added to the gallons. The corridor is only the test for "along the route".

Duplicate rows for one OPIS Truckstop ID use the minimum retail price. There is no timestamp and no fuel-grade column.

## Assumptions and limits

- Both endpoints must be in the US. A trailing Canadian province is rejected. Coordinates must fall in the contiguous box, Alaska, or Hawaii. A point in Windsor, Ontario can still pass the box.
- The CSV is truck-stop prices. California in the file is the Imperial Valley and Coachella Valley only. New York → Los Angeles still plans because the last Nevada stop leaves less than 500 miles. A route up the California coast can come back `feasible: false`.
- Of 6,738 stops, 1,034 are a Photon fuel POI, 5,649 are a city centroid, and 55 have no coordinate and are excluded. A big-city centroid more than 10 miles from the interstate is not selected. `python3 scripts/geocode_stations.py --poi-only` resumes the POI pass.
- The public OSRM demo allows about one request per second from a source IP and can refuse the address. On the VPS that budget is shared with everything else on `72.61.242.96`, including p1. Cached routes keep working.
- Local `runserver` is one process, so the in-process limiter is enough. Production is two Gunicorn workers and requires `REDIS_URL`.

## Docker deploy

This is for the Hostinger VPS that already runs p1. Do not join p1's network, do not use p1's Redis or Postgres, and do not edit the p1 nginx site. The app listens on `127.0.0.1:8001` only. Host nginx and Certbot already own ports 80 and 443.

Optional, and only with your yes, before the first start: the box has no swap and p1's app is about 1.1 GB of 3.9 GB.

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf && sudo sysctl -p /etc/sysctl.d/99-swappiness.conf
```

```bash
cp .env.example .env
# Set DJANGO_DEBUG=0 and a real DJANGO_SECRET_KEY. Compose sets REDIS_URL itself.
bash deploy/deploy.sh
```

`deploy/deploy.sh` checks that four `p1-` containers are running and that `https://setup.p1.kadamsahil.com/` returns 200, then runs `docker compose -p spotter up -d --build`. Caps are 512 MB and 0.75 CPU for `spotter-web`, 96 MB for `spotter-redis` (`redis:7-alpine`, not published, 64 MB `allkeys-lru`, AOF).

DNS, in the Hostinger zone, one new record. Leave `setup.p1` alone. No AAAA.

| Name | Type | Value |
|---|---|---|
| `spotter` | A | `72.61.242.96` |

```bash
sudo bash deploy/install-nginx.sh
sudo ISSUE_CERT=1 bash deploy/install-nginx.sh
```

That installs `deploy/nginx/spotter.kadamsahil.com.conf`, runs `nginx -t`, and reloads nginx. It never restarts nginx and never edits the p1 site. The second command runs `certbot --nginx -d spotter.kadamsahil.com`. A later run will not overwrite a file that already has a certificate unless `FORCE=1`.

```bash
curl -fsS http://127.0.0.1:8001/api/v1/health
curl -fsS https://spotter.kadamsahil.com/api/v1/health
curl -fsS -o /dev/null -w 'p1 %{http_code}\n' https://setup.p1.kadamsahil.com/
```

Rollback, which does not stop p1:

```bash
docker compose -p spotter down
```

## Postman

Import `postman/spotter-fuel-route.postman_collection.json`. The collection variable `baseUrl` is `http://127.0.0.1:8000`. On the VPS set it to `https://spotter.kadamsahil.com`.

The requests are health, Chicago → Houston, New York → Los Angeles, the same Chicago pair as lat/lng, New York → Philadelphia (under 500 miles, no stops), a Toronto rejection, and the Chicago map.

Map tiles are © OpenStreetMap contributors. Routes are from the OSRM demo or OpenRouteService. Places are from Photon and the US Census Bureau. Prices are the assessment CSV.
