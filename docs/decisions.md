# Design decisions

## What we kept

**One road, then the cheapest pumps on it.** OSRM `alternatives=false`. A second highway would be another geometry and a different set of stops, and the map would have to pick one. The public demo is also happier with one call.

**Full tank at mile 0 is prepaid.** "Money spent" is fuel bought on this trip. A 400-mile trip costs $0. Gallons at a stop are only what is purchased there.

**Next-cheaper greedy.** Buy at an expensive pump only to reach a cheaper one. Fill up only when nothing ahead is cheaper. This is the standard linear refueling rule and the unit tests pin the numbers.

**Census first, Photon second.** `City, ST` for the sample routes never touches the network. Street addresses and towns missing from the gazetteer are one Photon call, cached.

**Stations geocoded offline.** Request time does not geocode 6,700 truck stops. A POI is kept only when the name matches, so a random fuel station in town is not given this stop's price. Different addresses that collapsed onto one coordinate fall back to the city centroid.

**Corridor, not detour fuel.** Ten miles off the line counts as "on the route". The drive from the exit to the pump is not added to gallons. The knob is `corridor_miles`.

**LocMem locally, Redis in Docker.** `runserver` is one process. Two Gunicorn workers cannot share an in-process cache or an in-process rate limit, so production sets `REDIS_URL`. The lease is `SET key NX PX 1000`.

**SQLite for Django only.** The route cache is Redis (or LocMem). Station coordinates are a JSON file, not rows. There is no relational workload.

**WhiteNoise.** Static files do not depend on which proxy is in front. The map itself is a Django template. The browser loads Leaflet and OSM tiles.

**Host nginx, not a second proxy.** The VPS already runs p1 behind systemd nginx and Certbot. Compose publishes `127.0.0.1:8001` only. `127.0.0.1` and `localhost` stay in `DJANGO_ALLOWED_HOSTS` so the container healthcheck is not HTTP 400.

**Resource caps.** `spotter-web` is 512 MB and 0.75 CPU. `spotter-redis` is 96 MB with a 64 MB `allkeys-lru` cap. p1 keeps the larger CPU share. A 2 GB swapfile is a host change and is not created by the deploy script.

## Trade-offs

- City centroids are wrong for a large city and often fall outside the corridor. That drops some real stops rather than inventing an exit.
- The POI pass is incomplete (860 of 6,738). Resuming it improves locations. It does not change the API.
- OSRM's demo can throttle `72.61.242.96` because p1 shares the address. Cached plans still answer. OpenRouteService is the escape hatch and needs a free key that is not required to run.
- Two workers duplicate the station grid inside the 512 MB cap. More workers would not raise the one-request-per-second OSRM limit.
- AOF on a 96 MB Redis can be tight during a rewrite. If that OOM-kills the container, drop `--appendonly` and keep `--save 60 1` instead of raising the cap into p1's memory.
- The US check is a box. Windsor, Ontario can pass if you send raw coordinates.

## Later

- Finish the POI pass (`python3 scripts/geocode_stations.py --poi-only`).
- A state polygon, so border coordinates are not accepted on the box test.
- Charge the off-highway detour, or snap stops to the exit.
- A second route from the same OSRM response (`alternatives=true` is still one HTTP call) if the product wants the cheaper highway, not only the cheapest pumps.
- Grade or diesel vs gasoline, if a future file says which price is which.
