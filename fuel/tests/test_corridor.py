import math

from django.test import SimpleTestCase

from fuel.services.corridor import candidates
from fuel.services.geo import decode_polyline, encode_polyline


class CorridorTests(SimpleTestCase):
    def test_point_on_the_line_has_a_mile_marker(self):
        coords = [( -90.0, lat) for lat in (35 + i * 0.5 for i in range(11))]
        station = _station(37.5, -90.0, "on")
        pumps, distance = candidates(coords, _length(coords), [station], 10)
        self.assertEqual(len(pumps), 1)
        self.assertLess(pumps[0].station["cross_track_miles"], 1)
        self.assertGreater(pumps[0].mile, 100)
        self.assertLess(pumps[0].mile, distance)

    def test_southern_station_inside_the_corridor_is_kept(self):
        # At 29N, 9.5 miles east is about 0.16 degrees of longitude, wider than
        # corridor_miles/69. The box has to use latitude or this stop is dropped.
        coords = [(-95.0, 29.0), (-95.0, 30.0)]
        miles_per_lng = 69.0 * math.cos(math.radians(29.0))
        lng = -95.0 + 9.5 / miles_per_lng
        station = _station(29.5, lng, "south")
        pumps, _distance = candidates(coords, _length(coords), [station], 10)
        self.assertEqual(len(pumps), 1)
        self.assertLess(pumps[0].station["cross_track_miles"], 10)

    def test_point_fifty_miles_off_is_excluded(self):
        coords = [(-90.0, lat) for lat in (35 + i * 0.5 for i in range(11))]
        far = _station(37.5, -89.0, "far")
        pumps, _distance = candidates(coords, _length(coords), [far], 10)
        self.assertEqual(pumps, [])

    def test_unresolved_stations_are_excluded(self):
        coords = [(-90.0, 35.0), (-90.0, 40.0)]
        station = _station(37.5, -90.0, "nope")
        station["geocode_quality"] = "unresolved"
        pumps, _distance = candidates(coords, _length(coords), [station], 10)
        self.assertEqual(pumps, [])

    def test_polyline_round_trip(self):
        coords = [(-74.0, 40.7), (-87.6, 41.8), (-95.4, 29.8)]
        restored = decode_polyline(encode_polyline(coords))
        for original, decoded in zip(coords, restored):
            self.assertAlmostEqual(original[0], decoded[0], places=4)
            self.assertAlmostEqual(original[1], decoded[1], places=4)


def _station(lat, lng, name):
    return {
        "opis_id": name,
        "name": name,
        "address": "road",
        "city": "Town",
        "state": "KS",
        "price_per_gallon": 3.0,
        "lat": lat,
        "lng": lng,
        "geocode_quality": "poi",
    }


def _length(coords):
    from fuel.services.geo import mile_markers

    return mile_markers(coords)[-1]
