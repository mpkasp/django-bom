from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View

from bom.models import Part, PartClass, Subpart, SellerPart, Organization, Manufacturer, ManufacturerPart, User, \
    UserMeta, PartRevision, Assembly, AssemblySubparts
from bom.third_party_apis.mouser import Mouser


class BomJsonResponse(View):
    response = {'errors': [], 'content': {}}


@method_decorator(login_required, name='dispatch')
class MouserPartMatchBOM(BomJsonResponse):
    def get(self, request, part_revision_id):
        # TODO: instead of matching to BOM, match to all parts in the part-info view (including itsself) that are
        #  flagged to use Mouser
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
        subparts = part_revision.assembly.subparts.all()
        part_revision_ids = list(subparts.values_list('part_revision', flat=True))
        # print(list(part_revision_ids))
        part_revision_ids.append(part_revision_id)
        part_revisions = PartRevision.objects.filter(id__in=part_revision_ids)
        mouser = Mouser()

        part = part_revision.part
        qty_cache_key = str(part.id) + '_qty'
        quantity = cache.get(qty_cache_key, 100)

        seller_parts = []
        for pr in part_revisions:
            part = pr.part if pr is not None else None

            for manufacturer_part in part.manufacturer_parts():
                try:
                    part_seller_info = mouser.search_and_match(manufacturer_part.manufacturer_part_number,
                                                               quantity=quantity)
                    seller_parts.append(part_seller_info)
                except IOError as e:
                    self.response['errors'].append("Error communicating: {}".format(e))
                    continue
                except Exception as e:
                    self.response['errors'].append(
                        "Error matching part {}: {}".format(manufacturer_part.manufacturer_part_number, e))
                    continue
        self.response['content'].update({'seller_parts': seller_parts})
        return JsonResponse(self.response)
