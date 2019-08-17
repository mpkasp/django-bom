from django.test import TestCase
from .mouser import MouserApi


class TestMouser(TestCase):
    def setUp(self):
        self.api = MouserApi()

    def test_search_keyword(self):
        search = self.api.search_keyword(keyword='LSM6DSL')
        self.assertGreaterEqual(search['NumberOfResult'], 1)

    def test_get_manufacturer_list(self):
        manufacturers = self.api.get_manufacturer_list()
        self.assertGreaterEqual(manufacturers['Count'], 1)

    def search_part_and_manufacturer(self):
        manufacturers = self.api.get_manufacturer_list()
        self.assertGreaterEqual(manufacturers['Count'], 1)

