import json
import requests
import hashlib

from django.conf import settings
from django.core.cache import cache


class BaseApi:
    def __init__(self, api_settings_key, root_url, api_key_query=None, cache_timeout=86400):
        self.api_key = None
        self.root_url = root_url
        self.api_key_query = api_key_query
        self.cache_timeout = cache_timeout
        try:
            self.api_key = settings.BOM_CONFIG[api_settings_key]
        except KeyError as e:
            raise ValueError('No API key for {} found on server. Contact administrator for help.'.format(api_settings_key))

    def request(self, suburl, data=None):
        cache_key = suburl
        if data is not None:
            data_md5 = hashlib.md5(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()
            cache_key += '-{}'.format(data_md5)
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            # print('Found cached data!')
            return cached_data

        url = self.root_url + suburl

        if self.api_key_query is None or self.api_key is None:
            raise ValueError('No API key, or api key query found on server. Contact administrator for help.')

        params = ((self.api_key_query, self.api_key), )
        headers = {'accept': 'application/json', }

        if data is not None:
            headers.update({'Content-Type': 'application/json'})
            r = requests.post(url, headers=headers, params=params, data=json.dumps(data))
        else:
            r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            raise("HTTP Response != 200")

        cache.set(cache_key, r.content, self.cache_timeout)
        return r.content


class BaseApiError(Exception):
    pass
