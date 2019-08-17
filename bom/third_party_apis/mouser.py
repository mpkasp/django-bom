from .base_api import BaseApi
import json


class MouserApi:
    def __init__(self):
        self.api = BaseApi(api_settings_key='mouser_api_key',
                           root_url='https://api.mouser.com/api/v1',
                           api_key_query='apiKey')

    @staticmethod
    def parse_and_check_for_errors(content):
        data = json.loads(content)
        errors = data['Errors']
        if len(errors) > 0:
            raise "Error(s): {}".format(errors)
        return data

    def search_keyword(self, keyword):
        content = self.api.request('/search/keyword', data={
            "SearchByKeywordRequest": {
                "keyword": keyword,
                "records": 0,
                "startingRecord": 0,
                "searchOptions": "",
                "searchWithYourSignUpLanguage": ""
            }
        })
        data = self.parse_and_check_for_errors(content)
        return data["SearchResults"]

    def get_manufacturer_list(self):
        content = self.api.request('/search/manufacturerlist')
        data = self.parse_and_check_for_errors(content)
        return data["MouserManufacturerList"]

    def search_part_and_manufacturer(self, part_number, manufacturer_id):
        content = self.api.request('/search/keyword', data={
            "SearchByKeywordRequest": {
                "manufacturerId": manufacturer_id,
                "mouserPartNumber": part_number,
                "partSearchOptions": "",
            }
        })
        data = self.parse_and_check_for_errors(content)
        return data["SearchResults"]


class Mouser:
    def __init__(self):
        self.api = MouserApi()

    def search_and_match(self):
        manufacturer_list = self.api.get_manufacturer_list()
        # TODO: need to get manufacturer id from manufacturer list, do a fuzzy lookup using manufacturer name
        return None
