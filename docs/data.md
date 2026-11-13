# Station data

`data/stations.json` is the file the API loads. One object per OPIS Truckstop ID. Reviewers do not geocode anything to run the app.

## How this file was built

`scripts/geocode_stations.py`, also `python manage.py geocode_stations`.

1. Read `data/fuel-prices.csv` (8,151 rows). Collapse to one row per OPIS Truckstop ID. Price is the minimum `Retail Price`. Name and address are the most common values for that ID. Result: 6,738 stops. There is no timestamp and no fuel-grade column, so the minimum can mix grades.
2. City coordinate. If `City, ST` is in `data/us_places.json` (US Census populated-place interior points, 32,225 places), use that. Otherwise one Photon search, and only a hit whose name and state match. Canadian towns are Photon-only.
3. One Photon search per stop, biased to that city. A hit is kept as `poi` only when its name shares a distinctive word with the truck stop (brand words; "travel" and "center" do not count) and it is within 25 miles of the city. A fuel amenity with a different name is rejected.
4. If several stops with different addresses landed on the same coordinate, those rows are not a specific site. They fall back to the city coordinate.
5. Anything left with a city coordinate and no accepted POI is `city_centroid`. No coordinate at all is `unresolved` and the planner ignores it.

The POI pass was stopped so the take-home could be submitted. It is resumable.

## Coverage in the committed file

| `geocode_quality` | Stops | Used by the planner |
|---|---|---|
| `poi` | 860 | yes |
| `city_centroid` | 5,823 | yes, if within `corridor_miles` of the route |
| `unresolved` | 55 | no |

6,683 stops have coordinates.

California in the CSV is the Imperial Valley and Coachella Valley only (El Centro, Calexico, Brawley, Westmorland, Thermal, Jacumba, Coachella). A city centroid in Phoenix or another large city is often more than 10 miles from the interstate, so it never becomes a stop. That is intentional: a downtown point is not an exit.

## Regenerate

Progress files `data/geocode/cities.json` and `data/geocode/pois.json` are gitignored. The command rewrites `data/stations.json` as it goes and skips work already saved.

```bash
python3 scripts/geocode_stations.py --cities-only
python3 scripts/geocode_stations.py --poi-only
python manage.py geocode_stations
```

Photon is queried at about one request every 0.4 seconds, with backoff on 429 and 403. A full POI pass is a few thousand calls. Do not run it against the public server unless you mean to.

`scripts/build_places.py` rebuilds `data/us_places.json` from the Census national places gazetteer zip. The committed JSON is enough for `City, ST` endpoints.
