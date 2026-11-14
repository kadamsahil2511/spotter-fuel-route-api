# Testing

From the virtualenv:

```bash
DJANGO_DEBUG=1 python manage.py test fuel
```

25 tests, no network. Routing and Photon are mocked. `REDIS_URL` is unset, so the cache is the in-process one. `DJANGO_DEBUG=1` is required because the settings default is off and a missing secret then refuses to boot.

| File | What it locks |
|---|---|
| `fuel/tests/test_optimizer.py` | Trip under 500 miles costs $0. The starting full tank is not billed (600 miles, one stop at mile 400 and $4/gal, buys 10 gallons, $40). A cheaper station already in range wins. An expensive pump sells only enough fuel to reach a cheaper one. The tank is filled when every pump ahead costs at least as much. A gap longer than the tank is infeasible and returns no purchases |
| `fuel/tests/test_corridor.py` | A point on the line gets a mile marker. A point 50 miles off is excluded. `unresolved` is excluded. Polyline encode/decode round-trips. At 29°N, a stop 9.5 miles east is kept (longitude padding depends on latitude) |
| `fuel/tests/test_poi_match.py` | "Flying J" matches "Flying J - Brooks". "Flyers" does not match "Circle K". The store number is not required for a brand match |
| `fuel/tests/test_api.py` | `New York, NY` → `Los Angeles, CA` is a gazetteer hit, one routing call, stops and a GeoJSON line. The second GET is a cache hit and does not call the router. Coordinates do not call Photon. A street address calls Photon once. `Toronto, ON` and a coordinate in Europe are HTTP 400. A routing failure is HTTP 502. The map HTML escapes popup text |
| `fuel/tests/test_api.py` `CatalogTests` | Two CSV rows with one OPIS ID keep the lower price |
| `fuel/tests/test_settings_safety.py` | Debug defaults off. A missing or dev secret refuses to import when debug is off. A unique secret imports, and an unknown `LOG_LEVEL` falls back to `INFO`. Debug on allows a missing secret and logs to stdout |

The live curls in the README are not part of this suite. They need the public OSRM demo.
