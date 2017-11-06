import json
import urllib
import os

from .models import Part, Seller, SellerPart
from django.conf import settings

def match_part(part):
    # returns [{ seller: seller, }]
    # OCTOPART_API_KEY = os.environ.get('OCTOPART_API_KEY')
    OCTOPART_API_KEY = settings.BOM_CONFIG['octopart_api_key']
    print settings
    print OCTOPART_API_KEY

    if not OCTOPART_API_KEY:
        raise ValueError('No API key found on server. Contact administrator for help.')

    query = [{'mpn': part.manufacturer_part_number}]

    url = 'https://octopart.com/api/v3/parts/match?queries=%s' \
        % urllib.quote(json.dumps(query))
    url += '&apikey=' + OCTOPART_API_KEY

    try:
        data = urllib.urlopen(url).read()
    except Exception as e:
        raise

    response = json.loads(data)

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
                    seller = Seller.objects.filter(
                        name=offer['seller']['name'])[0]
                    ltd = offer['factory_lead_days']
                    if 'USD' in offer['prices']:
                        for price in offer['prices']['USD']:
                            moq = price[0]
                            price = price[1]
                            seller_parts.append(
                                SellerPart(
                                    seller=seller,
                                    part=part,
                                    minimum_order_quantity=moq,
                                    unit_cost=price,
                                    lead_time_days=ltd))

    return seller_parts