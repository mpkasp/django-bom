from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View

from bom.models import Part, PartClass, Subpart, SellerPart, Organization, Manufacturer, ManufacturerPart, User, UserMeta, PartRevision, Assembly, AssemblySubparts
from bom.third_party_apis.mouser import Mouser
from bom.third_party_apis.base_api import BaseApiError


class BomJsonResponse(View):
    response = {'errors': [], 'content': {}}


@method_decorator(login_required, name='dispatch')
class MouserPartMatchBOM(BomJsonResponse):
    def get(self, request, part_revision_id):
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)  # get all of the pricing for manufacturer parts, marked with mouser in this part
        user = request.user
        profile = user.bom_profile()
        organization = profile.organization

        # Goal is to search mouser for anything that we want from mouser, then update the part revision in the bom with that
        # To do that we can just get the manufacturer parts in this BOM
        part = part_revision.part
        qty_cache_key = str(part.id) + '_qty'
        assy_quantity = cache.get(qty_cache_key, 100)

        flat_bom = part_revision.flat(assy_quantity)

        mouser = Mouser()
        manufacturer_parts = flat_bom.mouser_parts()
        # Quantity is the same on flat and indented bom WRT sourcing, so we should only need to look up by part revision, or even part
        for bom_id, mp in manufacturer_parts.items():
            bom_part = flat_bom.parts[bom_id]
            bom_part_quantity = bom_part.total_extended_quantity

            try:
                part_seller_info = mouser.search_and_match(mp, quantity=bom_part_quantity, currency=organization.currency)
            except BaseApiError as err:
                self.response['errors'].append(err)
                continue

            try:
                bom_part.seller_part = part_seller_info['optimal_seller_part']
                bom_part.api_info = part_seller_info['mouser_parts'][0]
            except (KeyError, IndexError):
                continue

        flat_bom.update()
        flat_bom_dict = flat_bom.as_dict()
        self.response['content'].update({'flat_bom': flat_bom_dict})
        return JsonResponse(self.response)
