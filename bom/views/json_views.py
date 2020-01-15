from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View

from bom.models import Part, PartClass, Subpart, SellerPart, Organization, Manufacturer, ManufacturerPart, User, UserMeta, PartRevision, Assembly, AssemblySubparts
from bom.third_party_apis.mouser import Mouser


class BomJsonResponse(View):
    response = {'errors': [], 'content': {}}


@method_decorator(login_required, name='dispatch')
class MouserPartMatchBOM(BomJsonResponse):
    def get(self, request, part_revision_id):
        # TODO: instead of matching to BOM, match to all parts in the part-info view (including itsself) that are
        #  flagged to use Mouser
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)  # get all of the pricing for manufacturer parts, marked with mouser in this part
        subparts = part_revision.assembly.subparts.all()
        part_revision_ids = list(subparts.values_list('part_revision', flat=True))
        part_ids = list(subparts.values_list('part_revision__part', flat=True))

        part_revision_ids.append(part_revision_id)
        part_ids.append(part_revision.part_id)

        part_revisions = PartRevision.objects.filter(id__in=part_revision_ids)
        manufacturer_parts = ManufacturerPart.objects.filter(part__in=part_ids, source_mouser=True)
        part = part_revision.part
        qty_cache_key = str(part.id) + '_qty'
        assy_quantity = cache.get(qty_cache_key, 100)

        flat_bom = part_revision.flat(assy_quantity, sort=False)

        mp_lookup = {}
        for mp in manufacturer_parts:
            mp_lookup[mp.part_id] = mp

        bom_dict = {}
        for pr in part_revisions:
            try:
                bom_dict[pr.id] = {
                    'part_revision': part_revision,
                    'manufacturer_part': mp_lookup[pr.part_id],
                    'quantity_extended': flat_bom[pr.id]['quantity'] * assy_quantity,
                    'quantity': flat_bom[pr.id]['quantity'],
                }
            except KeyError:  # No manufacturer part to care about
                continue

        mouser = Mouser()

        seller_parts = {}
        for part_revision_id, bd in bom_dict.items():
            try:
                print('attempting to match: {} {}'.format(bd['manufacturer_part'].manufacturer_part_number, bd['quantity']))
                part_seller_info = mouser.search_and_match(bd['manufacturer_part'].manufacturer_part_number, quantity=bd['quantity_extended'])
                # bd['part_seller_info'] = part_seller_info
                part_seller_info.update({
                    'quantity': bd['quantity'],
                    'quantity_extended': bd['quantity_extended'],
                })
                seller_parts[part_revision_id] = part_seller_info
            except IOError as e:
                self.response['errors'].append("Error communicating: {}".format(e))
                continue
            except Exception as e:
                self.response['errors'].append("Error matching part {}: {}".format(bd['manufacturer_part'].manufacturer_part_number, e))
                continue
        self.response['content'].update({'seller_parts': seller_parts})
        return JsonResponse(self.response)
