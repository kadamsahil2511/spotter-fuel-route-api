from django.test import SimpleTestCase

from fuel.services.poi_match import name_matches


class PoiMatchTests(SimpleTestCase):
    def test_shared_brand_token_matches(self):
        self.assertTrue(name_matches("FLYING J #792", "Flying J - Brooks"))

    def test_unrelated_fuel_poi_does_not_match(self):
        self.assertFalse(name_matches("FLYERS #3200", "Circle K"))

    def test_brand_without_the_store_number_matches(self):
        self.assertTrue(name_matches("FLYERS #3201", "Flyers"))
