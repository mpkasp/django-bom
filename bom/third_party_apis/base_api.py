import json
import requests
import hashlib

from django.conf import settings
from django.core.cache.backends.locmem import LocMemCache


# Per-instance, ephemeral cache used only for request-burst dedup (a BOM reuses the
# same MPN across lines). Distributor pricing must never be written to a shared or
# persistent cache (ToS), so we deliberately use a local-memory cache rather than the
# project-wide Django cache. TTL is short and configurable; 0 disables caching.
_sourcing_cache = LocMemCache('bom-sourcing', {})

DEFAULT_SOURCING_CACHE_SECONDS = 300


def _cache_seconds():
    return settings.BOM_CONFIG.get('sourcing_cache_seconds', DEFAULT_SOURCING_CACHE_SECONDS)


class BaseApi:
    def __init__(self, api_settings_key, root_url, api_key_query=None):
        self.api_key = None
        self.root_url = root_url
        self.api_key_query = api_key_query
        try:
            self.api_key = settings.BOM_CONFIG[api_settings_key]
        except KeyError:
            raise ValueError('No API key for {} found on server. Contact administrator for help.'.format(api_settings_key))

    def request(self, suburl, data=None):
        cache_key = suburl
        if data is not None:
            data_md5 = hashlib.md5(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()
            cache_key += '-{}'.format(data_md5)

        cache_seconds = _cache_seconds()
        if cache_seconds:
            cached_data = _sourcing_cache.get(cache_key)
            if cached_data is not None:
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
            raise BaseApiError(f"HTTP Response != 200. Returned: {r.status_code} {r.reason}")

        if cache_seconds:
            _sourcing_cache.set(cache_key, r.content, cache_seconds)
        return r.content


class BaseApiError(Exception):
    pass
