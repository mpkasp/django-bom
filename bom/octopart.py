import json
import urllib
import os

from .models import Part, Seller, SellerPart
from datetime import datetime
from django.conf import settings
from .settings import BOM_CONFIG


def request(suburl):
    try:
        OCTOPART_API_KEY = settings.BOM_CONFIG['octopart_api_key']
    except KeyError as e:
        raise ValueError('No API key found on server. Contact administrator for help.')

    if not OCTOPART_API_KEY:
        raise ValueError('No API key found on server. Contact administrator for help.')

    url = 'https://octopart.com/api/v3/' + suburl
    url += '&apikey=' + OCTOPART_API_KEY

    try:
        data = urllib.request.urlopen(url).read()
    except Exception as e:
        raise

    return json.loads(data)


def match_part(manufacturer_part, organization):
    query = [{'mpn': manufacturer_part.manufacturer_part_number}]

    suburl = 'parts/match?queries=%s' \
        % urllib.parse.quote(json.dumps(query))
    response = request(suburl)

    # need for each part: digi-key, mouser prices, moqs, lead times
    DIGI_KEY_SELLER_ID = '459'
    MOUSER_SELLER_ID = '2401'

    seller_parts = []

    # print mpn's
    for result in response['results']:
        for item in result['items']:
            for offer in item['offers']:
                if (offer['seller']['id'] == DIGI_KEY_SELLER_ID or
                        offer['seller']['id'] == MOUSER_SELLER_ID):
                    seller_name = offer['seller']['name']
                    seller, created = Seller.objects.get_or_create(
                        name__iexact=seller_name, 
                        organization=organization, 
                        defaults={'name': seller_name})
                    ltd = offer['factory_lead_days']
                    if 'USD' in offer['prices']:
                        for price in offer['prices']['USD']:
                            try:
                                moq = price[0]
                                price = price[1]
                                seller_parts.append(
                                    SellerPart(
                                        seller=seller,
                                        manufacturer_part=manufacturer_part,
                                        minimum_order_quantity=moq,
                                        unit_cost=price,
                                        lead_time_days=ltd,
                                        data_source='octopart'))
                            except Exception as e:
                                raise

    return seller_parts


def get_latest_datasheets(manufacturer_part_number):
    query = [{'mpn': manufacturer_part_number}]

    suburl = 'parts/match?queries=%s' \
        % urllib.parse.quote(json.dumps(query)) + '&include[]=datasheets'
    response = request(suburl)

    datasheets = {}

    for result in response['results']:
        for item in result['items']:
            for datasheet in item['datasheets']:
                try:
                    if datasheet['metadata']['last_updated'] is not None:
                        lu = datetime.strptime(datasheet['metadata']['last_updated'], '%Y-%m-%dT%H:%M:%SZ')
                    else:
                        lu = None

                    if datasheet['metadata']['date_created'] is not None:
                        dc = datetime.strptime(datasheet['metadata']['date_created'], '%Y-%m-%dT%H:%M:%SZ')
                    else:
                        dc = None

                    last_updated = lu if lu is not None and lu > dc else dc
                    name = datasheet['attribution']['sources'][0]['name']
                    url = datasheet['url']
                    num_pages = datasheet['metadata']['num_pages']
                    if (name not in datasheet) or (last_updated is not None and datasheet[name]['last_updated'] < last_updated):
                        datasheets[name] = {
                            'url': url,
                            'last_updated': last_updated,
                            'num_pages': num_pages,
                        }
                except TypeError as e:
                    continue

    return datasheets
