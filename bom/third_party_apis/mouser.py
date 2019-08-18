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

    def search_part(self, part_number):
        content = self.api.request('/search/partnumber', data={
            "SearchByPartRequest": {
                "mouserPartNumber": part_number,
                "partSearchOptions": "",
            }
        })
        data = self.parse_and_check_for_errors(content)
        return data["SearchResults"]

    def search_part_and_manufacturer(self, part_number, manufacturer_id):
        content = self.api.request('/search/partnumberandmanufacturer', data={
            "SearchByPartMfrRequest": {
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

    def search_and_match(self, manufacturer_part_number, quantity=1):
        # manufacturer_list = self.api.get_manufacturer_list()
        # TODO: possibly get manufacturer id from manufacturer list, do a fuzzy lookup using manufacturer name
        #  to reduce results
        results = self.api.search_part(part_number=manufacturer_part_number)
        seller_parts = []
        optimal_part = None
        for part in results['Parts']:
            seller_part = {
                'part_number': part['ManufacturerPartNumber'],
                'manufacturer': part['Manufacturer'],
                'description': part['Description'],
                'data_sheet': part['DataSheetUrl'],
                'stock': part['Availability'],
                'lead_time': part['LeadTime'],
                'prices': [],
            }

            for pb in part['PriceBreaks']:
                moq = int(pb['Quantity'])
                price = float(pb['Price'].strip('$'))
                currency = pb['Currency']
                order_quantity = quantity if quantity > moq else moq
                order_price = order_quantity * price
                price_break = {
                    'price': price,
                    'order_quantity': order_quantity,
                    'order_price': order_price,
                    'moq': moq,
                }
                if optimal_part is None or (order_price < optimal_part['order_price'] and currency == 'USD'):
                    optimal_part = seller_part.copy()
                    optimal_part.update(price_break)
                seller_part['prices'].append(price_break)
            seller_parts.append(seller_part)

        match = {
            'seller_parts': seller_parts,
            'optimal_seller_part': optimal_part,
        }
        return match
