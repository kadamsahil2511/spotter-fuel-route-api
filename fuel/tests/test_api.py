from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from fuel.services.geo import encode_polyline
from fuel.services.routing import DrivingRoute, RoutingError
from fuel.services.stations import set_stations

NY = (40.662712, -73.938677)
LA = (34.019394, -118.410825)


def _line(samples=21):
    coords = []
    for index in range(samples):
        fraction = index / (samples - 1)
        lat = NY[0] + (LA[0] - NY[0]) * fraction
        lng = NY[1] + (LA[1] - NY[1]) * fraction
        coords.append((lng, lat))
    return coords


def _route(provider="osrm", distance=900.0):
    coords = _line()
    return DrivingRoute(distance, 36000, coords, encode_polyline(coords), provider)


def _station(fraction, price, name):
    lat = NY[0] + (LA[0] - NY[0]) * fraction
    lng = NY[1] + (LA[1] - NY[1]) * fraction
    return {
        "opis_id": name,
        "name": name,
        "address": "I-40",
        "city": "Town",
        "state": "OK",
        "price_per_gallon": price,
        "lat": lat,
        "lng": lng,
        "geocode_quality": "poi",
    }


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "api-tests"}})
class RouteApiTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        set_stations([_station(0.3, 3.0, "Pilot Town"), _station(0.7, 2.5, "Loves Town")])
        self.route_patch = patch("fuel.services.plan.fetch_route", return_value=_route())
        self.limit_patch = patch("fuel.services.plan.acquire_osrm_slot")
        self.fetch = self.route_patch.start()
        self.limit_patch.start()
        self.addCleanup(self.route_patch.stop)
        self.addCleanup(self.limit_patch.stop)
        self.addCleanup(lambda: set_stations(None))

    def test_gazetteer_route_calls_osrm_once_and_lists_stops(self):
        response = self.client.get("/api/v1/route", {"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["start"]["resolved_by"], "gazetteer")
        self.assertEqual(body["finish"]["resolved_by"], "gazetteer")
        self.assertEqual(body["external_calls"]["geocode"], 0)
        self.assertEqual(body["external_calls"]["route"], 1)
        self.assertEqual(body["cache"], "miss")
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertGreater(len(body["fuel_stops"]), 0)
        self.assertGreater(body["total_fuel_cost"], 0)
        self.assertEqual(self.fetch.call_count, 1)

        again = self.client.get("/api/v1/route", {"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["cache"], "hit")
        self.assertEqual(again.json()["external_calls"]["route"], 0)
        self.assertEqual(self.fetch.call_count, 1)

    def test_coordinates_do_not_call_photon(self):
        with patch("fuel.services.plan._photon") as photon:
            response = self.client.get(
                "/api/v1/route",
                {"start": f"{NY[0]},{NY[1]}", "finish": f"{LA[0]},{LA[1]}"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["start"]["resolved_by"], "coordinates")
        self.assertEqual(response.json()["external_calls"]["geocode"], 0)
        photon.assert_not_called()

    def test_street_address_calls_photon_once(self):
        with patch("fuel.services.plan._photon", return_value=NY) as photon:
            response = self.client.post(
                "/api/v1/route",
                data={"start": "350 5th Ave, New York, NY", "finish": "Los Angeles, CA"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["start"]["resolved_by"], "photon")
        self.assertEqual(response.json()["external_calls"]["geocode"], 1)
        self.assertEqual(photon.call_count, 1)
        self.assertEqual(self.fetch.call_count, 1)

    def test_canadian_text_is_rejected(self):
        response = self.client.get("/api/v1/route", {"start": "Toronto, ON", "finish": "Los Angeles, CA"})
        self.assertEqual(response.status_code, 400)
        self.fetch.assert_not_called()

    def test_coordinates_outside_the_us_are_rejected(self):
        response = self.client.get("/api/v1/route", {"start": "48.8566,2.3522", "finish": "Los Angeles, CA"})
        self.assertEqual(response.status_code, 400)

    def test_osrm_failure_is_502(self):
        self.fetch.side_effect = RoutingError("NoRoute")
        response = self.client.get("/api/v1/route", {"start": "Chicago, IL", "finish": "Houston, TX"})
        self.assertEqual(response.status_code, 502)

    def test_map_contains_the_stop_name(self):
        response = self.client.get("/api/v1/map", {"start": "New York, NY", "finish": "Los Angeles, CA"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pilot Town")
        self.assertContains(response, "leaflet")


class CatalogTests(SimpleTestCase):
    def test_duplicate_opis_id_keeps_the_minimum_price(self):
        from pathlib import Path
        import tempfile

        from fuel.services.catalog import collapse_csv

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            path.write_text(
                "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
                "105,TA SAGINAW,I-75,Bridgeport,MI,260,3.429\n"
                "105,TA SAGINAW,I-75,Bridgeport,MI,260,3.269\n"
            )
            rows = collapse_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_per_gallon"], 3.269)
