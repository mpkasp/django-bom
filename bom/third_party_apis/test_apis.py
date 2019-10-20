from django.test import TestCase
from .mouser import MouserApi
from unittest import skip


class TestMouser(TestCase):
    def setUp(self):
        self.api = MouserApi()

    @skip
    def test_search_keyword(self):
        search = self.api.search_keyword(keyword='LSM6DSL')
        self.assertGreaterEqual(search['NumberOfResult'], 1)

    @skip
    def test_get_manufacturer_list(self):
        manufacturers = self.api.get_manufacturer_list()
        self.assertGreaterEqual(manufacturers['Count'], 1)

    @skip
    def search_part_and_manufacturer(self):
        manufacturers = self.api.get_manufacturer_list()
        self.assertGreaterEqual(manufacturers['Count'], 1)

