# API reference

Base path: `/api/v1`. No authentication. `GET` and `POST` on `/route` accept the same fields. Query string for GET, JSON body for POST.

## Endpoints

| Method | Path | Success |
|---|---|---|
| GET | `/` | `200` JSON list of paths |
| GET | `/api/v1/health` | `200` if stations loaded (and Redis answers, when `REDIS_URL` is set). `503` otherwise |
| GET, POST | `/api/v1/route` | `200` plan, including an infeasible fuel plan. `400` bad input. `502` routing provider failed |
| GET | `/api/v1/map` | `200` HTML. Same errors as `/route` |

Health body:

```json
{"status": "ok", "stations": 6683}
```

With Redis configured, the body also has `"redis": "ok"` or `"redis": "down"`.

## Request fields

| Field | Required | Default | Unit / form |
|---|---|---|---|
| `start` | yes | | Place. See below |
| `finish` | yes | | Place |
| `range_miles` | no | 500 | Miles of range on a full tank. Must be a positive number |
| `mpg` | no | 10 | Miles per gallon. Must be a positive number |
| `corridor_miles` | no | 10 | Miles. A stop farther than this from the route line is ignored |

A place is one of:

- `"Chicago, IL"` or `"Chicago, Illinois"` (Census gazetteer)
- `"41.8781,-87.6298"`
- `{"lat": 41.8781, "lng": -87.6298}` (`lon` is accepted in place of `lng`)
- Any other string, sent to Photon once and cached

## Response fields

Money is US dollars. Coordinates in `location` and `start`/`finish` are decimal degrees, latitude then longitude as numbers. GeoJSON positions are `[longitude, latitude]`.

| Field | Unit | Meaning |
|---|---|---|
| `feasible` | boolean | A fuel plan exists that never runs the tank dry |
| `start.label` | text | The input label, or `lat,lng` when the input was coordinates |
| `start.lat`, `start.lng` | degrees | Resolved point |
| `start.resolved_by` | `coordinates`, `gazetteer`, or `photon` | How the point was found |
| `finish.*` | | Same as `start` |
| `route.distance_miles` | miles | OSRM road distance, one decimal |
| `route.duration_seconds` | seconds | OSRM drive time, rounded to an integer. No traffic |
| `route.geometry` | GeoJSON `LineString` | Simplified route (~500 m tolerance). Full geometry is used only to place stops |
| `route.provider` | `osrm` or `ors` | Who computed the road |
| `assumptions.tank_range_miles` | miles | The `range_miles` used |
| `assumptions.mpg` | miles/gallon | The `mpg` used |
| `assumptions.start_tank` | `full` | The tank starts full |
| `assumptions.initial_fuel_charged_usd` | dollars | Always `0`. Fuel already in the tank is not billed |
| `assumptions.corridor_miles` | miles | The filter width |
| `assumptions.detour_fuel` | text | `not charged`. Leaving the highway to the pump is not added to gallons |
| `fuel_stops[]` | list | Purchases in mile order. Empty when `feasible` is false, or when the trip fits in the starting tank |
| `fuel_stops[].opis_id` | text | OPIS Truckstop ID from the CSV |
| `fuel_stops[].name` | text | Most common name for that ID |
| `fuel_stops[].address`, `city`, `state` | text | Stop address |
| `fuel_stops[].price_per_gallon` | dollars / gallon | Minimum retail price for that ID. Up to 6 decimal places |
| `fuel_stops[].location.lat`, `.lng` | degrees | Stop coordinate |
| `fuel_stops[].mile_marker` | miles | Distance along the route from the start, one decimal |
| `fuel_stops[].cross_track_miles` | miles | Perpendicular distance from the route line, two decimals |
| `fuel_stops[].gallons` | gallons | Fuel bought at this stop, three decimals |
| `fuel_stops[].cost` | dollars | `gallons * price_per_gallon`, two decimals |
| `fuel_stops[].geocode_quality` | `poi` or `city_centroid` | How the stop was located. `unresolved` stops are never returned |
| `total_gallons` | gallons | Sum of purchases. Not the fuel already in the tank |
| `total_fuel_cost` | dollars | Sum of `cost` |
| `gap` | object or `null` | Set only when `feasible` is false |
| `gap.from_mile` | miles | Where the tank runs out |
| `gap.to_mile` | miles | Next station, or the destination if none |
| `gap.reason` | text | `No station within 500 miles`, or the requested range |
| `map_path` | path | Same plan as HTML |
| `external_calls.geocode` | count | Photon calls made for this request |
| `external_calls.route` | count | Routing calls. `1` for OSRM. `2` when OSRM failed and OpenRouteService was used. `0` on a cache hit |
| `cache` | `miss` or `hit` | Whether this plan was already stored |
| `elapsed_ms` | milliseconds | Server time for this request, one decimal. Not included in the cached object, so a hit reports only the cache read |

A real Chicago → Houston body, with the line shortened, is in the README. The full bodies are `docs/samples/chi-hou-cold.json` and `docs/samples/ny-la-cold.json`.

## Errors

Missing endpoint, HTTP 400:

```json
{"detail": "start and finish are required"}
```

Not a number, HTTP 400:

```json
{"detail": "range_miles, mpg, and corridor_miles must be numbers"}
```

Zero or negative, HTTP 400:

```json
{"detail": "range_miles, mpg, and corridor_miles must be positive"}
```

Canadian endpoint, HTTP 400:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/route?start=Toronto,%20ON&finish=Chicago,%20IL'
```

```json
{"detail": "start and finish must be in the United States"}
```

Coordinates outside the US boxes, HTTP 400:

```json
{"detail": "start and finish must be in the United States"}
```

City not in the gazetteer and not found by Photon, HTTP 400:

```json
{"detail": "Could not resolve 'Not A Real City, NY'"}
```

OSRM failure with no `ORS_API_KEY`, HTTP 502:

```json
{"detail": "Routing failed: NoRoute"}
```

The text after `Routing failed:` is the provider code or the HTTP error.

Infeasible fuel plan, HTTP 200. The route is still drawn. `fuel_stops` is empty and `total_fuel_cost` is `0`:

```json
{
  "feasible": false,
  "fuel_stops": [],
  "total_gallons": 0,
  "total_fuel_cost": 0,
  "gap": {
    "from_mile": 600.0,
    "to_mile": 1200.0,
    "reason": "No station within 500 miles"
  }
}
```

`from_mile` is where a full tank from the last reachable pump runs out. `to_mile` is the next pump, or the destination.
