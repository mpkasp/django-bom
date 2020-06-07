from moneyed import Money

from bom.utils import parse_number
from djmoney.contrib.exchange.models import convert_money
from .base_api import BaseApi, BaseApiError
from ..models import SellerPart, Seller
import json


class MouserApi(BaseApi):
    def __init__(self, *args, **kwargs):
        api_settings_key = 'mouser_api_key'
        root_url='https://api.mouser.com/api/v1'
        api_key_query = 'apiKey'
        super().__init__(api_settings_key, root_url, api_key_query=api_key_query)

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


class Mouser:
    def __init__(self):
        self.api = MouserApi()

    def search_and_match(self, manufacturer_part, quantity=1, currency=None):
        manufacturer = manufacturer_part.manufacturer
        manufacturer_part_number = manufacturer_part.manufacturer_part_number
        if manufacturer:
            manufacturer_list = self.api.get_manufacturer_list()
            # TODO: possibly get manufacturer id from manufacturer list, do a fuzzy lookup using manufacturer name
            #  to reduce results
            mfg_id = manufacturer_list[manufacturer.name] if manufacturer.name in manufacturer_list else None
            if mfg_id:
                results = self.api.search_part_and_manufacturer(part_number=manufacturer_part_number, manufacturer_id=mfg_id)
            else:
                results = self.api.search_part(part_number=manufacturer_part_number)
        else:
            results = self.api.search_part(part_number=manufacturer_part_number)

        mouser_parts = []
        optimal_part = None
        seller_parts = []
        for part in results['Parts']:
            seller = Seller(name='Mouser')
            try:
                quantity_available = [int(s) for s in part['Availability'].split() if s.isdigit()][0]
                mouser_part = {
                    'part_number': part['ManufacturerPartNumber'],
                    'manufacturer': part['Manufacturer'],
                    'description': part['Description'],
                    'data_sheet': part['DataSheetUrl'],
                    'stock': part['Availability'],
                    'stock_parsed': quantity_available,
                    'lead_time': part['LeadTime'],
                    'seller_parts': [],
                    'product_detail_url': part['ProductDetailUrl'],
                }

                lead_time_days = [int(s) for s in part['LeadTime'].split() if s.isdigit()][0]  # TODO: Make sure it's actually days
                for pb in part['PriceBreaks']:
                    moq = int(pb['Quantity'])
                    unit_price_raw = parse_number(pb['Price'])
                    unit_currency = pb['Currency']
                    unit_cost = Money(unit_price_raw, unit_currency)
                    if currency:
                        unit_cost = convert_money(unit_cost, currency)
                    seller_part = SellerPart(
                        seller=seller,
                        manufacturer_part=manufacturer_part,
                        minimum_order_quantity=moq,
                        minimum_pack_quantity=1,
                        data_source='Mouser',
                        unit_cost=unit_cost,
                        lead_time_days=lead_time_days,
                        nre_cost=Money(0, currency),
                        ncnr=True)
                    mouser_part['seller_parts'].append(seller_part.as_dict())
                    seller_parts.append(seller_part)
                mouser_parts.append(mouser_part)
            except (KeyError, AttributeError, IndexError):
                continue
        local_seller_parts = list(manufacturer_part.seller_parts())
        seller_parts.extend(local_seller_parts)
        return {
            'mouser_parts': mouser_parts,
            'optimal_seller_part': SellerPart.optimal(seller_parts, quantity),
        }
