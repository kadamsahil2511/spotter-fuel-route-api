# Plan: Spotter AI fuel-stop routing API

Take-home for a Backend Django Engineer role. No application code in this change. This document is the implementation plan for review.

Measured on 13 Nov 2026. Figures below are from the attached CSV and from single live calls to the public routing and geocoding servers.

## Decision

Build a small Django 6.1 + DRF app. One OSRM driving-route call returns the road geometry. Station coordinates are precomputed offline and committed, so a request never geocodes a truck stop. Start and finish resolve locally when they are coordinates or `City, ST`; a street address may add one Photon lookup per endpoint. Fuel stops are chosen on that single route to minimize dollars spent, with a 500-mile tank that starts full and 10 mpg.

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
| Database | SQLite. Stations themselves are a committed JSON file loaded into memory |
| Cache | SQLite tables `RouteCache` and `GeocodeCache`, plus a per-process LocMem layer in front of the route cache |
| HTTP | `requests` |
| Geo libraries | None. Haversine, point-to-segment, and a degree-cell grid are a few dozen lines |
| Tests | `python manage.py test` (Django `TestCase` + `unittest.mock`) |
| Server | `manage.py runserver` for the review and the Loom. Gunicorn is unnecessary for the demo |

SQLite keeps setup to `pip install` and `migrate`. PostGIS and Redis would add install steps and would not make the hot path faster: the station set is about 6,700 points, and the route cache values are a few hundred kilobytes.

Pinned in `requirements.txt`: `Django==6.1.3`, `djangorestframework==3.18.3`, `requests`. Dev-only pins stay empty unless a test tool is actually needed.

Configuration is environment variables, with defaults that work with no key. `.env.example` lists them. `.env` is gitignored. Nothing secret is committed.

| Variable | Default |
|---|---|
| `OSRM_BASE_URL` | `https://router.project-osrm.org` |
| `PHOTON_BASE_URL` | `https://photon.komoot.io` |
| `ROUTING_USER_AGENT` | `SpotterFuelRoute/1.0 (assessment)` |
| `CORRIDOR_MILES` | `10` |
| `TANK_RANGE_MILES` | `500` |
| `MPG` | `10` |

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
3. Anything else (a street address) is one Photon search, cached forever in `GeocodeCache`.

Photon (`photon.komoot.io`) needs no key. It is a demo server: fair use, no availability promise, heavy traffic gets throttled. One or two cached lookups per odd request fits that.

**Station geocoding is not a request-time service.** See the next section. Photon is used once, offline.

### Alternatives considered

| Option | Why it is not the default |
|---|---|
| OpenRouteService (HeiGIT, `api.heigit.org`) | Free Standard key: 2,000 directions/day (40/min) and 3,000 geocodes/day (100/min). Real quota, and commercial use of the Standard plan has been described as allowed. Costs a signup and a secret. The old host `api.openrouteservice.org` was shut down in early November 2026. Kept as the documented fallback if the OSRM demo is blocking us during implementation: same client interface, `ORS_API_KEY` in the environment. Not built unless OSRM fails. |
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

User-Agent on every outbound call identifies the app. OSRM is not called faster than once a second even on a cold cache miss storm (a process-wide lock / minimum interval). Photon is only contacted on gazetteer miss.

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
| 502 | OSRM timeout or non-`Ok` code, after one retry |

Money is rounded to cents in the JSON. Internal math uses decimals.

`GET /api/v1/map` returns `text/html`. Leaflet draws the simplified line and a marker per stop (name, price, gallons, cost in the popup). Tiles are `tile.openstreetmap.org` with the required OSM attribution plus a line crediting OSRM and Photon. The page is rendered from the same Python plan, so opening it does not call OSRM a second time when the route cache is warm, and it does not call OSRM from the browser at all.

## Project layout

```
manage.py
requirements.txt
.env.example
README.md                 # setup, samples, algorithm, timings (written with the implementation)
PLAN.md
postman/spotter-fuel-route.postman_collection.json
data/fuel-prices.csv
data/stations.json        # committed geocode output
data/us_places.json       # Census place centroids, City|ST → [lat, lng]
config/                   # Django project (settings, urls, wsgi)
fuel/
  models.py               # GeocodeCache, RouteCache
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
| Cache hit (LocMem or SQLite route cache) | under 100 ms server time |
| Cache miss, cross-country | under 2.0 s, most of it OSRM (~0.7–1.0 s observed) |
| Corridor + optimizer | under 30 ms |

How:

- Station grid built once per process from `stations.json`.
- Route cache key: provider, start/finish rounded to 4 decimal degrees, range, mpg, corridor. Value: polyline, distance, duration. SQLite so a restart (and the Loom's second take) stays warm. LocMem in front so a hit does not parse SQLite on every poll.
- Geocode cache keyed by the normalized query string.
- Client geometry simplified; full geometry kept for the mile axis and not stored twice in the response.
- One OSRM attempt plus a single retry on connection reset. Timeout 10 s.

The README will include the measured `elapsed_ms` for the two sample routes, cold and warm, taken on the implementation run. The response field `elapsed_ms` is the server time excluding client transfer.

## Documentation and review artifacts

README (written with the code, not in this commit):

- What the service does, the stack pins, and the OSRM / Photon choice including the usage limits.
- Setup: venv, `pip install -r requirements.txt`, `migrate`, `runserver`. No geocode step required.
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

## Out of scope

- Accounts, API keys for our own API, deployment, Docker.
- Diesel vs gasoline split (the CSV does not say).
- Turn-by-turn instructions, traffic, or elevation.
- Re-routing through a cheaper region (`alternatives` stays off).
- A self-hosted routing engine.
- Charging the driver for the fuel already in the tank at mile 0.

## Implementation order, after approval

1. Django project, settings, health check, models, gazetteer loader.
2. Optimizer and corridor tests, written against the cases above, then the implementation.
3. OSRM client, plan service, API, map page, route cache.
4. Offline geocode command, run it, commit `data/stations.json` and `data/us_places.json`.
5. API tests with mocked HTTP. Postman collection. README with real sample responses and timings from New York → Los Angeles and Chicago → Houston.
6. Open the PR to `main` with those samples, timings, and a map screenshot.
