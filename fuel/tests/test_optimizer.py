from django.test import SimpleTestCase

from fuel.services.optimizer import Pump, plan_fuel


def pump(mile, price, name):
    return Pump(mile, price, {"name": name, "opis_id": name, "price_per_gallon": price})


class OptimizerTests(SimpleTestCase):
    def test_short_trip_buys_nothing(self):
        result = plan_fuel([pump(100, 3, "A")], 400, 500, 10)
        self.assertTrue(result.feasible)
        self.assertEqual(result.purchases, [])
        self.assertEqual(result.total_cost, 0)

    def test_start_full_is_not_billed(self):
        result = plan_fuel([pump(400, 4, "A")], 600, 500, 10)
        self.assertTrue(result.feasible)
        self.assertEqual(len(result.purchases), 1)
        self.assertAlmostEqual(result.purchases[0].gallons, 10)
        self.assertAlmostEqual(result.total_cost, 40)

    def test_prefers_cheaper_station_already_in_range(self):
        result = plan_fuel([pump(100, 5, "A"), pump(200, 1, "B")], 600, 500, 10)
        self.assertEqual([item.station["name"] for item in result.purchases], ["B"])
        self.assertAlmostEqual(result.total_cost, 10)

    def test_buys_only_enough_expensive_fuel_to_reach_a_cheaper_pump(self):
        result = plan_fuel([pump(400, 5, "A"), pump(700, 1, "B")], 900, 500, 10)
        self.assertEqual([item.station["name"] for item in result.purchases], ["A", "B"])
        self.assertAlmostEqual(result.purchases[0].gallons, 20)
        self.assertAlmostEqual(result.purchases[1].gallons, 20)
        self.assertAlmostEqual(result.total_cost, 120)

    def test_fills_when_the_next_pumps_are_more_expensive(self):
        result = plan_fuel([pump(100, 1, "A"), pump(450, 3, "B")], 900, 500, 10)
        self.assertAlmostEqual(result.purchases[0].gallons, 10)
        self.assertAlmostEqual(result.purchases[1].gallons, 30)
        self.assertAlmostEqual(result.total_cost, 100)

    def test_gap_longer_than_the_tank_is_infeasible(self):
        result = plan_fuel([pump(100, 2, "A")], 700, 500, 10)
        self.assertFalse(result.feasible)
        self.assertEqual(result.purchases, [])
        self.assertAlmostEqual(result.gap_from, 600)
        self.assertAlmostEqual(result.gap_to, 700)
