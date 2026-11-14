# Plan: Spotter AI fuel-stop routing API

Take-home for a Backend Django Engineer role. No application code in this change. This document is the implementation plan for review.

Measured on 13 Nov 2026. Figures below are from the attached CSV and from single live calls to the public routing and geocoding servers.

## Decision

Build a small Django 6.1 + DRF app and run it on the Hostinger VPS already used for other services. One OSRM driving-route call returns the road geometry. Station coordinates are precomputed offline and committed, so a request never geocodes a truck stop. Start and finish resolve locally when they are coordinates or `City, ST`; a street address may add one Photon lookup per endpoint. Fuel stops are chosen on that single route to minimize dollars spent, with a 500-mile tank that starts full and 10 mpg.

## Data we actually have

Source file: `fuel-prices-for-be-assessment.csv` (will live at `data/fuel-prices.csv`).

| Fact | Value |
|---|---|
| Columns | OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price |
| Rows | 8,151. No blank cells. No latitude/longitude |
| Unique OPIS IDs | 6,738. 678 IDs repeat (max 6 rows). 597 of those have more than one price |
| Price | min $2.687, per-ID minimum median $3.399, max $6.399. Largest spread inside one ID is $0.90 |
| Geography | 6,626 US IDs (7,531 rows) and 112 Canadian IDs (620 rows). Canadian codes in the State column: AB, BC, MB, NB, NS, ON, QC, SK, YT |
| Addresses | Mostly highway exits (`I-44, EXIT 283 & US-69`), not street addresses. About 7,484 of 8,151 rows look like that |

California is thin in a way that affects the demo. All 16 California rows are in the Imperial Valley and Coachella Valley (El Centro, Calexico, Brawley, Westmorland, Thermal, Jacumba, Coachella). There is no Los Angeles, Central Valley, or Bay Area stop. Midwest and Texas coverage is dense (TX 790 rows, IL 774). New York to Los Angeles can still be fueled if the last stop is in Arizona or eastern California and the remaining distance is inside 500 miles. A route that enters California farther north, or a hop inside the state longer than the tank, will come back as infeasible. The API will say so, with the mile gap, rather than invent a stop.

## Tech stack

| Piece | Choice |
|---|---|
| Language | Python 3.12 (3.12.3 is on this machine; DRF supports 3.10–3.15) |
| Django | 6.1.3, latest stable patch as of 3 Nov 2026 |
| API | Django REST framework 3.18.3, which declares Django 6.1 support |
| Database | SQLite for Django's own tables. Stations stay a committed JSON file loaded into memory |
| Cache | Redis via django-redis. Spotter runs its own Redis. LocMem only when `REDIS_URL` is unset, which production refuses |
| HTTP | `requests` |
| Geo libraries | None. Haversine, point-to-segment, and a degree-cell grid are a few dozen lines |
| Tests | `python manage.py test` (Django `TestCase` + `unittest.mock`) |
| App server | Gunicorn, 2 `gthread` workers, 4 threads each, published only on `127.0.0.1:8001`. `runserver` is local development |
| Edge | The host's existing nginx + Certbot. A new site file for `spotter.kadamsahil.com`. No Caddy, Coolify, or Traefik |
| Process manager | Docker Compose project `spotter`, beside production project p1, never on p1's network |
| Static files | WhiteNoise |
| Host | Hostinger KVM 1 shared with p1. 1 vCPU, 3.9 GB RAM, no swap, ~2 GB available, ~35 GB disk free. Ubuntu 24.04 LTS. Public IPv4 `72.61.242.96` |

SQLite stays the Django database because the app has no relational workload. Route and geocode caching moved to Redis so two Gunicorn workers share one cache and one OSRM rate limit. PostGIS is still unnecessary: the station set is about 6,700 points.

Pinned in `requirements.txt`: `Django==6.1.3`, `djangorestframework==3.18.3`, `requests`, `gunicorn`, `whitenoise`, `django-redis`. Dev-only pins stay empty unless a test tool is actually needed.

Configuration is environment variables, with defaults that work with no key. `.env.example` lists them. `.env` is gitignored. Nothing secret is committed.

| Variable | Default |
|---|---|
| `OSRM_BASE_URL` | `https://router.project-osrm.org` |
| `PHOTON_BASE_URL` | `https://photon.komoot.io` |
| `ROUTING_USER_AGENT` | `SpotterFuelRoute/1.0 (assessment)` |
| `CORRIDOR_MILES` | `10` |
| `TANK_RANGE_MILES` | `500` |
| `MPG` | `10` |
| `DJANGO_DEBUG` | `1` locally, `0` on the VPS |
| `DJANGO_SECRET_KEY` | required when `DJANGO_DEBUG=0`. Generated on the server, never committed |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` locally. On the VPS: `spotter.kadamsahil.com,srv1208763.hstgr.cloud` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://spotter.kadamsahil.com` on the VPS |
| `SQLITE_PATH` | local file in dev. `/data/db.sqlite3` on the VPS. Django tables only |
| `REDIS_URL` | unset locally (LocMem). On the VPS: `redis://redis:6379/0` |
| `ORS_API_KEY` | unset until the OSRM fallback is enabled. Server env only |
| `WEB_CONCURRENCY` | `2` |
| `GUNICORN_THREADS` | `4` |
| `DOMAIN` | `spotter.kadamsahil.com` |

## Routing and geocoding

### Choice: public OSRM for the route, Photon only when a street address misses the local gazetteer

**Route.** `GET {OSRM_BASE_URL}/route/v1/driving/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=polyline&alternatives=false`

No API key. The demo server (`router.project-osrm.org`, also `routing.openstreetmap.de`) allows reasonable non-commercial use, asks for at most about one request per second, requires an identifying User-Agent, and requires ODbL / OSRM attribution. It has no uptime guarantee. That is acceptable for a take-home, and the cache below means a repeated demo does not depend on a second call.

Measured with an identifying User-Agent:

| Query | Time | Body | Geometry |
|---|---|---|---|
| Chicago → Houston, `overview=full`, GeoJSON | 0.79 s | 262 KB | 11,458 points, 1,086.4 mi |
| New York → Los Angeles, `overview=full`, GeoJSON | 0.96 s | 810 KB | 34,853 points, 2,794.2 mi |
| New York → Los Angeles, `overview=full`, polyline | 0.67 s | 122 KB | same route |

`overview=simplified` is the wrong geometry for fuel stops. Chicago → Houston came back as **18 points across 1,086 miles**. A 10-mile corridor drawn against those chords would pick stops that are not on the highway. Annotations on that response still described the full leg (11,457 segments) and do not line up with the simplified coordinates. The implementation will request the full overview, decode the polyline, and compute mile markers along those points.

The JSON we return to clients is a Douglas–Peucker simplification of that line (about a 500 m tolerance) so a cross-country response is tens of kilobytes. The optimizer uses the full line.

**Endpoint geocoding.** Two layers:

1. Coordinates (`{"lat","lng"}` or `"lat,lng"`) skip geocoding.
2. `City, ST` and `City, State Name` hit a committed US place gazetteer (Census populated-place centroids, public domain, compact JSON). The four sample cities resolve here.
3. Anything else (a street address) is one Photon search, cached in Redis.

Photon (`photon.komoot.io`) needs no key. It is a demo server: fair use, no availability promise, heavy traffic gets throttled. One or two cached lookups per odd request fits that.

**Station geocoding is not a request-time service.** See the next section. Photon is used once, offline.

### Alternatives considered

| Option | Why it is not the default |
|---|---|
| OpenRouteService (HeiGIT, `api.heigit.org`) | Free Standard key: 2,000 directions/day (40/min) and 3,000 geocodes/day (100/min). Same routing-client interface. Used only after OSRM returns a non-`Ok` code, 429, or a timeout, and only when `ORS_API_KEY` is set. One directions call, no ORS geocoding. The old host `api.openrouteservice.org` was shut down in early November 2026; the client uses `api.heigit.org`. |
| Nominatim public API | Hard 1 request/second, identifying User-Agent, no bulk, results must be cached. Policy asks LLM-generated clients to surface those rules. Two sample name searches (`WOODSHED OF BIG CABIN, Big Cabin, OK` and `TA SEYMOUR…, Seymour, IN`) returned no hit. Poor fit for exit-style truck-stop addresses and for a 6,700-row offline job. |
| US Census batch geocoder | Free, no key, 10,000 addresses per batch. It wants a street number and street name. This file is exit descriptions, so the match rate would be low. Rejected as the station geocoder. |
| Self-hosted OSRM or Photon | No quota, and the right production answer. A world OSRM needs hundreds of GB of RAM; a Photon planet dump is on the order of 95 GB disk and 64 GB RAM. Out of scope for the take-home. |
| Google or Mapbox | Paid keys. The brief asks for a free routing API. |

## External-call budget

Counted per user request, after the offline station file exists.

| Start and finish | Calls |
|---|---|
| Both lat/lng, or both `City, ST` in the gazetteer | **1** OSRM route |
| One side is a street address | **1** Photon + **1** OSRM = 2 |
| Both sides are street addresses | **2** Photon + **1** OSRM = 3 |
| Same pair again (rounded to 4 decimal degrees, ~11 m) | **0**. Route and geocode caches absorb it |

A map page does not add a routing call. The HTML view runs the same server-side plan and embeds the geometry. The browser only requests OSM tiles.

No per-station geocoding, no isochrones, no matrix, no alternative-route call. `alternatives=false` on purpose: a second geometry doubles memory and would change which stops are "on the route" without a product decision. OSRM can return an alternative in the same HTTP call; that is left out so the map shows one route.

User-Agent on every outbound call identifies the app. OSRM is not called faster than once a second from this app. The limiter is a Redis key with a 1-second TTL, shared by both Gunicorn workers. The public demo quota is still per source IP, and every container on `72.61.242.96` shares it, including p1. Photon is only contacted on gazetteer miss.

## Offline station geocoding

Management command: `python manage.py geocode_stations`.

1. Read the CSV. Collapse to one record per OPIS Truckstop ID.
   - Price = minimum Retail Price. There is no timestamp and no fuel-grade column, so the minimum is the cost-effective reading of a conflicting ID. It can mix grades; that limit is documented in the README.
   - Name = the most common name for that ID. Address, city, and state = the most common tuple.
2. Phase A — city centroids. Unique `(city, state)` through Photon structured search (`city`, `state`, `countrycode=us` or `ca`). About 3,900 pairs. Sample check: `Seymour, Indiana` returned the town at 38.959, -85.890.
3. Phase B — one biased Photon search per station: `q="{name}, {city}, {state}"`, `lat`/`lon` set to the city centroid, `limit=5`.
   - Accept a hit in the right state, within 25 miles of the city centroid, whose OSM tag is `amenity=fuel` or `highway=services`, or whose name shares a distinctive token with the truck stop (`Pilot`, `Love's`, `TA`, …).
   - Sample hits that matched this way: Pilot in Gila Bend, AZ (`amenity:fuel`, 32.930, -112.673) and Shelee's Travel Center in Coachella, CA (`amenity:fuel`).
   - Sample misses: `I-44 exit 283, Big Cabin, Oklahoma` was empty, and the name query for Big Cabin returned the town, not the stop. `Travel Center Seymour` with an `amenity:fuel` filter and no tight bias returned Seymour, Tennessee. Exit-line queries and unbounded fuel-tag queries are not used.
4. If phase B does not accept a hit and that city has a single OPIS ID, store the city centroid with `geocode_quality=city_centroid`. Small interstate towns are usually within a few miles of the exit.
5. Otherwise `geocode_quality=unresolved`. Those stations are stored for inspection and excluded from the optimizer. A large city must not collapse every stop onto one downtown point (Phoenix has 23 rows).
6. Write `data/stations.json` after every batch so the command can resume. Sleep to stay near one Photon request per second. Back off on 429/403. Expected one-time runtime is a few hours. The result is committed. Reviewers do not rerun the command.

Each station record: `opis_id`, `name`, `address`, `city`, `state`, `country`, `price_per_gallon`, `lat`, `lng`, `geocode_quality` (`poi` | `city_centroid` | `unresolved`), `geocode_label` (what Photon returned).

The request path loads this file once, builds a 1-degree grid, and never opens the CSV.

## Fuel-stop algorithm

Constants, overridable per request but defaulted to the brief: tank range **R = 500 miles**, **10 mpg**, tank **starts full**. The initial 50 gallons are prepaid and cost $0. "Money spent" is fuel purchased along the way. Total gallons burned is `distance_miles / 10`; gallons purchased is `max(0, distance_miles - 500) / 10`, spread across stops at whatever prices the plan buys. A trip of 500 miles or less has no stop and a fuel cost of $0.

Detour off the polyline is a filter, not a charge. A station is "on the route" when its cross-track distance to the full polyline is at most `CORRIDOR_MILES` (default 10). Its mile marker is the distance along the route to the closest point. Gallons use that mile marker only. A stop 2 miles off an exit does not add 4 miles of fuel. Corridor width is the knob that tolerates geocode error.

### Place stations on the route

1. Decode the full polyline. Cumulative distance along consecutive points is the mile axis. If that sum disagrees with OSRM's `distance` by more than 2%, scale the axis to OSRM's distance (road distance wins).
2. Bounding box of the line, expanded by the corridor. Look up only those grid cells.
3. For each station in the box, walk nearby segments (the grid limits which segments) and keep the minimum cross-track. Haversine point-to-segment is enough at 10-mile tolerance.
4. Keep stations with cross-track ≤ corridor, mile marker strictly between 0 and the destination, and `geocode_quality != unresolved`.
5. Sort by mile marker. Candidates on a cross-country trip should be a few hundred, not 6,700.

Downsample only inside a segment that is shorter than the precision we care about: the full Chicago line is 11k points and the full LA line is 35k. A station is tested against segments in its cell, not against every point. Target for this step plus the optimizer: under 30 ms.

### Choose stops (minimum dollars)

This is the linear "next cheaper station" greedy (the same rule as the classic full-tank refuel problem). The origin is a virtual point at mile 0 where we cannot buy, the tank is already full, and the fuel on board has price 0. Stations are processed only forward. Partial fills are allowed. Filling the tank is allowed only when every station still in range is at least as expensive as the current pump, because carrying expensive fuel past a cheaper pump wastes money.

`price_here` is 0 at the origin and the pump price once we have stopped at a station. `can_buy` is false at the origin and true at a real station. `fuel` is miles of range currently in the tank.

```
pos = 0, fuel = R, price_here = 0, can_buy = false, cost = 0, purchases = []

while pos < D:
    if fuel >= D - pos:
        return purchases, cost          # already able to finish

    reach = R if can_buy else fuel      # we may top up before leaving a pump
    window = stations with mile in (pos, pos + reach]
    dest_in = D <= pos + reach
    cheaper = nearest station in window whose price < price_here

    if cheaper exists and can_buy:
        buy just enough miles to arrive at cheaper with an empty spare
        drive to cheaper
    else if cheaper exists:
        drive to cheaper                   # still riding prepaid fuel
    else if dest_in and can_buy:
        buy just enough to arrive at D empty
        return purchases, cost
    else if dest_in:
        return purchases, cost
    else if window and can_buy:
        fill the tank                      # no cheaper pump in range
        drive to the cheapest station in window
    else if window:
        drive to the cheapest station in window
    else:
        infeasible; report the gap

buy(miles) at the current pump:
    gallons = miles / mpg
    cost += gallons * price_here
    fuel += miles
    record {station, gallons, cost}

drive to station s:
    fuel -= s.mile - pos
    pos = s.mile
    price_here = s.price
    can_buy = true
```

Worked checks the tests will lock in:

- Trip shorter than 500 miles → no purchases, cost $0.
- One station inside the first tank, destination beyond it → buy only the gallons required to finish, at that station's price. The prepaid miles are not billed.
- A cheap station inside the current tank and an expensive one beside it → drive to the cheap station and buy there.
- An expensive station that is the only way to reach a cheaper one → buy only enough expensive fuel to reach the cheap pump, then buy the rest there. Do not fill up at the expensive pump.
- A gap with no station inside the current range, even after a fill → `feasible: false` and the from/to mile. Do not return a partial stop list that strands the truck.

The plan does not optimize number of stops or travel time. It minimizes fuel dollars on the fastest OSRM route. A longer route with cheaper diesel is out of scope.

Canadian stations stay in the file. They are selected only when the corridor actually reaches them. Start and finish must be in the US.

### US check

- Text that ends in a state token must be one of the 50 states or DC. Canadian abbreviations are rejected.
- Coordinates must fall in a CONUS box (lat 24.5–49.5, lng -125–-66.5), Alaska (lat 51–72, lng -170–-129), or Hawaii (lat 18.5–22.5, lng -160.5–-154.5).
- Known limit: a point in Windsor, Ontario sits inside the CONUS box. Text endpoints are saved by the state token. Raw coordinates near the border can false-pass. Documented, not solved with another geocoding call.

## API

`POST /api/v1/route` and `GET /api/v1/route` (GET is what Postman and the map page use).

Request, JSON body or query string:

```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA",
  "range_miles": 500,
  "mpg": 10,
  "corridor_miles": 10
}
```

`start` and `finish` may instead be `{"lat": 40.7128, "lng": -74.006}` or `"40.7128,-74.006"`.

Response `200`:

```json
{
  "feasible": true,
  "start": {"label": "New York, NY", "lat": 40.7128, "lng": -74.006, "resolved_by": "gazetteer"},
  "finish": {"label": "Los Angeles, CA", "lat": 34.0522, "lng": -118.2437, "resolved_by": "gazetteer"},
  "route": {
    "distance_miles": 2794.2,
    "duration_seconds": 179300,
    "geometry": {"type": "LineString", "coordinates": [[-74.006, 40.713]]},
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
      "opis_id": "121",
      "name": "PETRO STOPPING CENTER #306",
      "address": "I-10, EXIT 200",
      "city": "Eloy",
      "state": "AZ",
      "price_per_gallon": 3.474,
      "location": {"lat": 32.75, "lng": -111.55},
      "mile_marker": 2410.4,
      "cross_track_miles": 0.4,
      "gallons": 28.5,
      "cost": 99.01,
      "geocode_quality": "poi"
    }
  ],
  "total_gallons": 229.4,
  "total_fuel_cost": 812.44,
  "gap": null,
  "map_path": "/api/v1/map?start=New%20York%2C%20NY&finish=Los%20Angeles%2C%20CA",
  "external_calls": {"geocode": 0, "route": 1},
  "cache": "miss",
  "elapsed_ms": 900
}
```

Coordinates in the sample above are illustrative. The implementation returns the simplified GeoJSON line (longitude, latitude order).

Infeasible fuel plan, still `200`, because the route itself succeeded and the map needs it:

```json
{
  "feasible": false,
  "gap": {"from_mile": 1200.0, "to_mile": 1750.0, "reason": "No station within 500 miles"},
  "fuel_stops": [],
  "total_gallons": 0,
  "total_fuel_cost": 0
}
```

Errors:

| Status | When |
|---|---|
| 400 | Missing start/finish, unparseable coordinates, place not in the gazetteer and Photon has no US hit, endpoint outside the US |
| 502 | OSRM failed, and `ORS_API_KEY` is unset or the OpenRouteService call also failed |

Money is rounded to cents in the JSON. Internal math uses decimals.

`GET /api/v1/map` returns `text/html`. Leaflet draws the simplified line and a marker per stop (name, price, gallons, cost in the popup). Tiles are `tile.openstreetmap.org` with the required OSM attribution plus a line crediting OSRM and Photon. The page is rendered from the same Python plan, so opening it does not call OSRM a second time when the route cache is warm, and it does not call OSRM from the browser at all.

## Project layout

```
manage.py
requirements.txt
.env.example
Dockerfile
docker-compose.yml        # project name spotter: web + private redis
deploy/nginx/spotter.kadamsahil.com
README.md                 # setup, samples, algorithm, timings, deploy runbook
PLAN.md
postman/spotter-fuel-route.postman_collection.json
data/fuel-prices.csv
data/stations.json        # committed geocode output, baked into the image
data/us_places.json       # Census place centroids, City|ST → [lat, lng]
config/                   # Django project (settings, urls, wsgi)
fuel/
  models.py               # Django tables only; route cache is Redis
  services/resolve.py     # gazetteer, Photon fallback, US check
  services/osrm.py
  services/corridor.py
  services/optimizer.py
  services/plan.py        # ties the above together
  views.py
  serializers.py
  templates/fuel/map.html
  management/commands/geocode_stations.py
  tests/test_optimizer.py
  tests/test_corridor.py
  tests/test_api.py
```

`fuel.services.plan` is the only object the views call. Tests cover the optimizer and the corridor without Django where they can, and the view with OSRM and Photon mocked.

## Tests

Optimizer, pure functions, no I/O:

- Under 500 miles: zero stops, $0.
- Start full is not billed: a 600-mile trip with one station at mile 400 and price $4 buys 100 miles of fuel (10 gallons), cost $40, not the whole trip.
- Prefer the cheaper station already inside the tank over a closer expensive one.
- Buy only enough at an expensive station to reach a cheaper one, then buy the rest there.
- Fill when the next pumps are all more expensive and the destination is beyond a full tank.
- Gap longer than the tank: infeasible, empty purchase list, gap miles set.
- Two rows, one OPIS ID: the loaded station carries the minimum price.

Corridor:

- A point on the line has cross-track near 0 and a sensible mile marker.
- A point 50 miles off is excluded at a 10-mile corridor.
- Stations marked `unresolved` are excluded.

API, `requests` mocked:

- `New York, NY` → `Los Angeles, CA` style fixture: gazetteer hit, exactly one OSRM call, stops and total cost present, geometry is a GeoJSON LineString.
- The second identical GET issues zero OSRM calls (`cache: hit`).
- Lat/lng input issues zero Photon calls.
- A street address issues one Photon call and one OSRM call.
- `Toronto, ON` and a coordinate in Europe return 400.
- OSRM `NoRoute` / timeout returns 502.
- Map GET returns HTML containing the stop name from the fixture.

The Postman collection has: New York, NY → Los Angeles, CA; Chicago, IL → Houston, TX; the same pair as lat/lng; a short hop under 500 miles (New York, NY → Philadelphia, PA) expecting no stops; a non-US rejection.

## Performance targets

From the OSRM measurements above, the network call dominates.

| Path | Target |
|---|---|
| Cache hit (Redis) | under 50 ms server time |
| Cache miss, cross-country | under 2.0 s, most of it OSRM (~0.7–1.0 s observed) |
| Corridor + optimizer | under 30 ms |

How:

- Station grid built once per process from `stations.json`. Two workers means two copies, both inside the 512 MB container cap.
- Route cache key: provider, start/finish rounded to 4 decimal degrees, range, mpg, corridor. Value: polyline, distance, duration, in Redis so both workers and a restart see it. No LocMem in production.
- Geocode cache keyed by the normalized query string.
- Client geometry simplified; full geometry kept for the mile axis and not stored twice in the response.
- One OSRM attempt, timeout 10 s. On a non-`Ok` code, 429, or timeout, one OpenRouteService directions call when `ORS_API_KEY` is set. The two providers are not both called for a success.

The README will include the measured `elapsed_ms` for the two sample routes, cold and warm, taken on the implementation run. The response field `elapsed_ms` is the server time excluding client transfer.

## Documentation and review artifacts

README (written with the code, not in this commit):

- What the service does, the stack pins, and the OSRM / Photon choice including the usage limits.
- Local setup: venv, `pip install -r requirements.txt`, `migrate`, `runserver`. No geocode step required.
- Deploy runbook for the Hostinger VPS (the short version of the Deployment section).
- Sample GET and the sample JSON, plus the two measured timings.
- The algorithm and the assumptions (full tank not billed, min price per OPIS ID, 10-mile corridor, detour not billed, single fastest route).
- California coverage limit.
- Attribution: OSM, OSRM, Photon.
- How to re-run `geocode_stations` if the CSV changes, and that the committed JSON is authoritative.

Postman collection committed. No Loom in the repo; the README will say the 5-minute recording is Postman against the two routes plus a short code walkthrough (resolve → one OSRM call → optimizer).

## Assumptions we will implement unless you change them

1. Tank starts full. The initial 500 miles are not charged. Purchases cover only miles beyond the fuel already on board, subject to where the pumps are.
2. Conflicting prices for one OPIS ID use the minimum.
3. Corridor default is 10 miles. Fuel for the drive off the highway and back is not added.
4. Objective is dollars on the fastest route, not fewest stops and not a cheaper alternate highway.
5. `feasible: false` is HTTP 200 with an empty stop list and a `gap` object. Bad input is 400. OSRM failure is 502.
6. Canadian stations remain available to the corridor. Endpoints must be in the US.
7. `city_centroid` geocodes are eligible. `unresolved` geocodes are not.
8. Range and mpg default to 500 and 10 and can be overridden per request so tests can use small numbers.

## Deployment

Target: `https://spotter.kadamsahil.com` on the Hostinger VPS that already runs production project p1. Spotter is a neighbor. It does not join p1's network, database, cache, or nginx site.

### What is on the box

Inspected state, not an assumption:

| | |
|---|---|
| Plan | Hostinger KVM 1 |
| CPU | 1 vCPU |
| RAM | 3.9 GB total, about 2 GB available, no swap |
| Disk | about 35 GB free |
| OS | Ubuntu 24.04 LTS |
| Public IPv4 | `72.61.242.96` |
| Hostname | `srv1208763.hstgr.cloud` |
| Firewall | no Hostinger firewall group. `ufw` is inactive |
| Edge | host nginx (systemd) and Certbot own ports 80 and 443. One site: `setup.p1.kadamsahil.com` proxies to `127.0.0.1:3000`. No Caddy, no Coolify, no Traefik |

Running containers, all on Docker network `p1_default`:

| Container | Role | Host port | Memory |
|---|---|---|---|
| `p1-p1-1` | p1 app | `127.0.0.1:3000` | about 1.1 GB |
| `p1-evolution-1` | p1 | `127.0.0.1:8080` | not measured |
| `p1-redis-1` | `redis:7` | none on the host | not measured |
| `p1-postgres-1` | `postgres:15` | none on the host | not measured |

A stopped `tech-news-assistant` stack is also present. Leave it stopped. Do not start it, remove it, or prune its volumes.

p1's ports are 3000 and 8080. Spotter uses `127.0.0.1:8001` and nothing else on the host. Spotter does not use p1's Redis or Postgres.

### nginx

Add a new file, `/etc/nginx/sites-available/spotter.kadamsahil.com`. The copy in the repo is `deploy/nginx/spotter.kadamsahil.com`. Do not edit the p1 site file, whatever its name is.

```
server {
    listen 80;
    server_name spotter.kadamsahil.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }
}
```

Enable it with a symlink in `sites-enabled`. Then:

```
nginx -t && systemctl reload nginx
```

`nginx -t` comes first every time. If the test fails, remove the new symlink, run `nginx -t` again to confirm the previous config is intact, and do not reload. The reload is `systemctl reload nginx`. A restart drops connections to `setup.p1.kadamsahil.com`. This runbook never restarts nginx.

After the name resolves and the HTTP site loads, issue the certificate:

```
certbot --nginx -d spotter.kadamsahil.com
nginx -t && systemctl reload nginx
```

`certbot --nginx` with that one `-d` rewrites the Spotter server block and leaves the p1 block alone. Certbot's own nginx hook is a reload. Django does not redirect HTTP to HTTPS. The health check talks to Gunicorn on plain HTTP at `127.0.0.1:8001`, and a Django redirect would make that check follow a scheme the loopback socket does not serve.

### Compose project `spotter`

`docker-compose.yml` sets `name: spotter`. The runbook always passes `-p spotter` as well, so a command typed in the wrong directory cannot attach to p1. Container names are `spotter-web` and `spotter-redis`. The network is `spotter_default`. Volumes are `spotter_sqlite` and `spotter_redis`. None of those names match `p1_`.

`spotter-web`:

- Image from the repo `Dockerfile`: `python:3.12-slim`, non-root, `collectstatic` at build, entrypoint runs `migrate` and then Gunicorn. A failed migration refuses to serve.
- `ports: ["127.0.0.1:8001:8001"]`. That is the only host port. 3000 and 8080 are untouched.
- Inside the container Gunicorn binds `0.0.0.0:8001`. Docker's port proxy connects to the container interface, so a bind of `127.0.0.1` inside the container would not accept the published port. From the host, and from nginx, the socket is `127.0.0.1:8001` and it is not reachable on the public IP.
- `mem_limit: 512m`, `cpus: 0.75`, `cpu_shares: 512`. p1's containers keep the default share of 1024, so when the core is busy p1 wins the weight. The 0.75 cap is a hard ceiling even when p1 is idle.
- Named volume `spotter_sqlite` mounted at `/data` for `/data/db.sqlite3`. SQLite holds Django's tables (migrations, and that is all). It does not hold the route cache.
- `stations.json` and `us_places.json` are baked into the image.
- `restart: unless-stopped`. Log driver `json-file` with `max-size: 10m` and `max-file: 3`, so a noisy app cannot fill the disk p1 is using.
- Depends on `spotter-redis` being healthy.

`spotter-redis`:

- Image `redis:7-alpine`. No `ports` key. It is reachable only as `redis:6379` on `spotter_default`.
- `mem_limit: 96m`.
- `cpu_shares: 256`.
- Command: `redis-server --maxmemory 64mb --maxmemory-policy allkeys-lru --appendonly yes --appendfsync everysec --save 60 1`.
- Named volume `spotter_redis` at `/data` for the AOF and the RDB snapshot.
- AOF rewrite forks. On a 64 MB dataset that spike has to fit in the 32 MB between `maxmemory` and the 96 MB cgroup. If the container is OOM-killed during a rewrite, drop `--appendonly` and keep `--save 60 1` rather than raising the 96 MB cap. The cap is what keeps Redis from growing into p1.

There is no Caddy service and no Compose profile that publishes 80 or 443.

Gunicorn:

```
gunicorn config.wsgi:application \
  --bind 0.0.0.0:8001 \
  --workers ${WEB_CONCURRENCY:-2} \
  --worker-class gthread \
  --threads ${GUNICORN_THREADS:-4} \
  --timeout 30 \
  --graceful-timeout 10 \
  --keep-alive 5 \
  --max-requests 500 \
  --max-requests-jitter 50 \
  --access-logfile - \
  --error-logfile -
```

Two workers each load a station grid. The grid is small next to p1's 1.1 GB, and both copies have to live inside 512 MB. Four threads per worker wait on OSRM without a third copy of the grid. `--preload` stays off. A worker recycle at 500 requests reloads one grid. The other worker keeps serving. The recycle drops nothing that lives in Redis.

SQLite stays on WAL with a 20 second `timeout` and `CONN_MAX_AGE=0`. Two processes writing migrations at startup is the entrypoint's job, once. Request traffic does not write the route cache to SQLite.

### Cache and the OSRM limiter

`django-redis` is the Django cache. `REDIS_URL=redis://redis:6379/0`. Key prefix `spotter`. Route values are the polyline plus distance and duration. Geocode values for street addresses live in the same Redis. `allkeys-lru` and a 64 MB ceiling evict cold routes before Redis grows. A cross-country polyline is on the order of 100 KB, so the cap holds hundreds of routes, which is more than this team will keep hot.

Production refuses to boot when `DJANGO_DEBUG=0` and `REDIS_URL` is empty. Tests mock the cache and the limiter. Local `runserver` may use LocMem only when `REDIS_URL` is unset.

The one-request-per-second limiter is `SET spotter:osrm:lease <token> NX PX 1000`. Both workers see the same key. A miss that loses the race waits out the remaining TTL instead of calling OSRM. A process-local lock would let the two workers call together and trip the public demo on the first burst.

That lock coordinates Spotter only. `router.project-osrm.org` meters the source IP `72.61.242.96`. p1's containers share the address. The route cache is what keeps the team off the limit: the second request for the same pair makes zero OSRM calls.

If OSRM returns a non-`Ok` code, HTTP 429, or times out, and `ORS_API_KEY` is set, the same request makes one OpenRouteService directions call to `api.heigit.org` and does not also retry OSRM. The free Standard key is 2,000 directions per day and 40 per minute. The key stays in the server environment. Geocoding does not move to ORS. With no key, a failed OSRM call is a 502 and cached routes still answer.

### Who it has to serve

About 50 to 100 employees, with 5 to 10 requests in flight at a peak. That is not 10 unique cross-country routes every second.

A cache hit is a Redis read and a JSON response. Target under 50 ms. A miss is the OSRM wait measured above, about 0.7 to 1.0 seconds, plus under 30 ms for the corridor and the optimizer. Eight threads can hold a handful of those waits. They cannot beat the public OSRM ceiling. Ten cold misses at once serialize on the Redis lease, so the last one starts about ten seconds later, and while the threads are parked a cache hit waits for a free thread. After the first person requests a lane, everyone else on that lane is a hit. The realistic peak is a few distinct lanes, not ten new ones.

The ceiling is the shared public IP, not Gunicorn and not the 512 MB cap. ORS is the escape hatch when that IP is throttled, inside the free daily quota. A self-hosted router would remove the ceiling and is out of scope on a 4 GB box that p1 already occupies.

### Django on this host

- `DJANGO_DEBUG=0`. The entrypoint refuses to boot when debug is on and `DOMAIN` is set.
- `DJANGO_ALLOWED_HOSTS=spotter.kadamsahil.com,srv1208763.hstgr.cloud`. The bare IPv4 is not a public API host.
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://spotter.kadamsahil.com`.
- `SECURE_PROXY_SSL_HEADER` for nginx's `X-Forwarded-Proto`. Secure session and CSRF cookies. No `SECURE_SSL_REDIRECT`.
- DRF auth is `AllowAny` without session auth, so Postman can POST without a CSRF cookie.
- `SECRET_KEY` comes from the environment. No fallback when debug is off.
- WhiteNoise serves `/static/` from the image. The map page is a Django template. Leaflet and OSM tiles are loaded by the browser. nginx does not get a second `location` for static files, which keeps the p1 config untouched.

### Health check

`GET /api/v1/health` does not call OSRM, ORS, or Photon.

- `200` `{"status":"ok","stations": N}` when the grid is loaded and Redis answers `PING`.
- `503` when the grid failed or Redis is down. Serving without Redis would drop the rate limit and stampede the shared IP.

The image `HEALTHCHECK` and the Compose healthcheck request `http://127.0.0.1:8001/api/v1/health` from inside `spotter-web` every 30 seconds.

### Swap, as a separate host change

The machine has no swap, and `p1-p1-1` is already about 1.1 GB of a 3.9 GB host. Spotter's caps add 512 MB plus 96 MB on top of the roughly 2 GB that is free today. Those caps stop Spotter from growing without bound. They do not stop the kernel from invoking the OOM killer if a spike (Certbot, an apt run, Redis fork, a Gunicorn reload) lands while p1 is at its high-water mark. With no swap, that killer can select `p1-p1-1`.

Recommend a 2 GB swapfile and `vm.swappiness=10`, before the first `docker compose -p spotter up`. Swappiness 10 keeps p1 in RAM during normal load and gives the kernel an overflow instead of an immediate kill. The file is about 2 GB of the 35 GB free. It is a host change: `/swapfile`, an `/etc/fstab` line, and a sysctl. It is not part of the Spotter compose project, and it waits for the owner's explicit yes. The app deploy does not create it on its own.

### DNS

One new A record in the Hostinger zone for `kadamsahil.com`. The existing `setup.p1` record stays as it is.

| Name | Type | Value |
|---|---|---|
| `spotter` | A | `72.61.242.96` |

No AAAA. This write is live DNS. At deploy time the current records are read first, the one new line is shown, and it waits for an explicit yes. If `spotter` already exists, it is updated in place.

### Deploy runbook

p1 must still be healthy after every step. Rollback does not mention p1's containers except to look at them.

Pre-flight, before anything is changed:

```
docker ps --filter name=p1- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -fsS -o /dev/null -w 'p1 %{http_code}\n' https://setup.p1.kadamsahil.com/health
ss -ltnp | awk 'NR==1 || /:80 |:443 |:3000|:8080|:8001/'
```

Expect four `p1-` containers Up, p1 HTTP 200, 8001 not listening, 3000 and 8080 still bound to loopback. If that is not true, stop.

Then:

1. Confirm the DNS A record and wait until `dig +short spotter.kadamsahil.com` is `72.61.242.96`.
2. Put the production env on the server only: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, allowed hosts, CSRF origin, `SQLITE_PATH=/data/db.sqlite3`, `REDIS_URL=redis://redis:6379/0`, `WEB_CONCURRENCY=2`, `GUNICORN_THREADS=4`, and `ORS_API_KEY` if the fallback is turned on. Do not commit that file.
3. From the repo directory: `docker compose -p spotter up -d --build`. This builds `spotter-web` and `spotter-redis`. It does not stop, start, or recreate any `p1-` container.
4. `curl -fsS http://127.0.0.1:8001/api/v1/health`.
5. Repeat the p1 pre-flight. Four `p1-` containers still Up, `https://setup.p1.kadamsahil.com/health` still 200. If either check fails, roll back before touching nginx.
6. Install the nginx site file, symlink it, `nginx -t && systemctl reload nginx`. On a failed test, remove the symlink and do not reload.
7. `certbot --nginx -d spotter.kadamsahil.com`, then `nginx -t && systemctl reload nginx`.
8. `curl -fsS https://spotter.kadamsahil.com/api/v1/health`, then the p1 check again.
9. One cold `GET /api/v1/route` for Chicago, IL → Houston, TX, then the same GET. The second body has `"cache": "hit"` and `"external_calls": {"geocode": 0, "route": 0}`. Then New York, NY → Los Angeles, CA, cold and warm.
10. Open `https://spotter.kadamsahil.com/api/v1/map?start=Chicago,%20IL&finish=Houston,%20TX`.

A later deploy is the same `docker compose -p spotter up -d --build`. Volumes are kept, so the route cache and the SQLite file survive. It does not re-geocode stations. `docker system prune` is not part of this runbook.

Rollback of the app:

```
docker compose -p spotter down
```

That stops `spotter-web` and `spotter-redis` and removes the `spotter_default` network. It does not take `-v`, so the volumes remain for a retry. It does not match `p1-` containers, `p1_default`, p1 volumes, or the stopped `tech-news-assistant` stack. If the nginx site itself has to come out, delete the Spotter symlink only, then `nginx -t && systemctl reload nginx`. Leave the p1 site file and the p1 certificate where they are.

### Trade-offs

- Two workers on one core duplicate the station grid and can occupy all eight threads during a cold burst. The 512 MB cap and the Redis lease are the bounds. A third worker would spend RAM p1 needs and would not raise the OSRM rate.
- `cpus: 0.75` leaves a quarter of the core even when Spotter is busy. Under contention `cpu_shares: 512` against p1's 1024 gives p1 the larger slice. The cost is a slower optimizer when both apps are hot. The optimizer is under 30 ms, so the wait that matters is still OSRM.
- Redis is a second container to operate, and it is the reason a hit is shared across workers and survives a recycle. p1's Redis would have been zero extra memory and a shared failure domain. That is the wrong trade on a production cache.
- AOF plus a 96 MB cap can OOM Redis during rewrite. RDB-only is the fallback, with a minute of cache loss, not a larger limit.
- The swapfile protects p1 from the OOM killer and is a host-wide change. It stays off until the owner approves it.
- ORS adds a secret and a second provider whose free tier is 2,000 directions a day. It is used only when the shared IP is already blocked at OSRM. Warm routes never call either provider.
## Out of scope

- Accounts and API keys for our own API.
- A second VPS, a self-hosted OSRM, or using p1's Redis or Postgres.
- Docker is in scope. Kubernetes is not.
- Diesel vs gasoline split (the CSV does not say).
- Turn-by-turn instructions, traffic, or elevation.
- Re-routing through a cheaper region (`alternatives` stays off).
- Charging the driver for the fuel already in the tank at mile 0.

## Implementation order, after approval

1. Django project, settings, health check, models, gazetteer loader.
2. Optimizer and corridor tests, written against the cases above, then the implementation.
3. OSRM client, plan service, API, map page, route cache.
4. Offline geocode command, run it, commit `data/stations.json` and `data/us_places.json`.
5. API tests with mocked HTTP. Postman collection. README with real sample responses and timings from New York → Los Angeles and Chicago → Houston.
6. Dockerfile, Compose project `spotter` (web + Redis), nginx site file, WhiteNoise, health check, production settings.
7. Open the PR to `main` with those samples, timings, and a map screenshot.
8. Deploy to the VPS only after that, and only once the p1 pre-flight and the DNS change have an explicit yes. The A record for `spotter.kadamsahil.com` is a live DNS write and is confirmed on its own. The 2 GB swapfile is a separate host change and a separate yes.
