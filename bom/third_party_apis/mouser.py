import json

from .base_api import BaseApi, BaseApiError


class MouserApi(BaseApi):
    def __init__(self, api_key=None):
        super().__init__(api_key, root_url='https://api.mouser.com/api/v1', api_key_query='apiKey')

    @staticmethod
    def parse_and_check_for_errors(content):
        data = json.loads(content)
        errors = data['Errors']
        if len(errors) > 0:
            raise BaseApiError("Error(s): {}".format(errors))
        return data

    def search_keyword(self, keyword):
        content = self.request('/search/keyword', data={
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
        content = self.request('/search/manufacturerlist')
        data = self.parse_and_check_for_errors(content)
        return data["MouserManufacturerList"]

    def search_part(self, part_number):
        content = self.request('/search/partnumber', data={
            "SearchByPartRequest": {
                "mouserPartNumber": part_number,
                "partSearchOptions": "",
            }
        })
        data = self.parse_and_check_for_errors(content)
        return data["SearchResults"]

    def search_part_and_manufacturer(self, part_number, manufacturer_id):
        content = self.request('/search/partnumberandmanufacturer', data={
            "SearchByPartMfrRequest": {
                "manufacturerId": manufacturer_id,
                "mouserPartNumber": part_number,
                "partSearchOptions": "",
            }
        })
        data = self.parse_and_check_for_errors(content)
        return data["SearchResults"]
