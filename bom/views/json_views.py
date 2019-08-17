from django.contrib.auth.decorators import login_required
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
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
        subparts = part_revision.assembly.subparts.all()
        mouser = Mouser()

        seller_parts = []

        for subpart in subparts:
            pr = subpart.part_revision
            part = pr.part if pr is not None else None

            for manufacturer_part in part.manufacturer_parts():
                try:
                    part_seller_info = mouser.search_and_match(manufacturer_part.manufacturer_part_number,
                                                               manufacturer_part.manufacturer.name)
                    seller_parts.append(part_seller_info)
                except IOError as e:
                    self.response['errors'].append("Error communicating: {}".format(e))
                    continue
                except Exception as e:
                    self.response['errors'].append("Unknown Error: {}".format(e))
                    continue
        self.response['content'].update({'seller_parts': seller_parts})
        return JsonResponse(self.response)