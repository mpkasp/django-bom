from django.conf import settings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Prefetch, prefetch_related_objects

from bom.models import ManufacturerPart, SellerPart

LIST_PAGE_SELLER_QUANTITY = 100
LIST_PAGE_SELECT_RELATED = (
    "part",
    "part__organization",
    "part__number_class",
    "part__primary_manufacturer_part",
)


def get_list_page_size():
    return settings.BOM_CONFIG.get("admin_dashboard", {}).get("page_size", 25)


def prepare_part_revs_for_list_page(
    part_revs_page, quantity=LIST_PAGE_SELLER_QUANTITY
):
    """Prefetch seller data and attach one optimal_seller per part on a page."""
    object_list = list(part_revs_page.object_list)
    part_revs_page.object_list = object_list
    if not object_list:
        return part_revs_page

    sellerpart_qs = SellerPart.objects.select_related("seller")
    manufacturer_qs = ManufacturerPart.objects.select_related(
        "manufacturer"
    ).prefetch_related(Prefetch("sellerpart_set", queryset=sellerpart_qs))
    prefetch_related_objects(
        object_list,
        Prefetch("part__manufacturerpart_set", queryset=manufacturer_qs),
    )

    seen = {}
    for part_rev in object_list:
        part = part_rev.part
        if part.pk in seen:
            part._optimal_seller_result = seen[part.pk]
            part._optimal_seller_qty = quantity
            continue
        sellerparts = []
        for manufacturer_part in part.manufacturerpart_set.all():
            sellerparts.extend(manufacturer_part.sellerpart_set.all())
        seller = SellerPart.optimal(sellerparts, quantity)
        part._optimal_seller_result = seller
        part._optimal_seller_qty = quantity
        seen[part.pk] = seller
    return part_revs_page


def paginate_part_revs(request, part_revs, page_size):
    part_revs = part_revs.select_related(*LIST_PAGE_SELECT_RELATED)
    paginator = Paginator(part_revs, page_size)
    page = request.GET.get("page")
    try:
        part_revs = paginator.page(page)
    except PageNotAnInteger:
        part_revs = paginator.page(1)
    except EmptyPage:
        part_revs = paginator.page(paginator.num_pages)
    return prepare_part_revs_for_list_page(part_revs)


class UnpaginatedPartRevList:
    """Iterable stand-in for a Django Page when rendering the full list."""

    def __init__(self, object_list):
        self.object_list = object_list

    def has_other_pages(self):
        return False

    def __iter__(self):
        return iter(self.object_list)

    def __len__(self):
        return len(self.object_list)


def prepare_all_part_revs_for_list_page(
    part_revs, quantity=LIST_PAGE_SELLER_QUANTITY
):
    """Prefetch seller data for every matching row (no pagination)."""
    part_revs = part_revs.select_related(*LIST_PAGE_SELECT_RELATED)
    page = prepare_part_revs_for_list_page(
        UnpaginatedPartRevList(part_revs), quantity
    )
    attach_print_unit_costs(page)
    return page


def attach_print_unit_costs(part_revs_page):
    """Set print_unit_cost without walking BOM trees for raw materials."""
    for part_rev in part_revs_page:
        seller = part_rev.part.optimal_seller()
        material = part_rev.material
        if material in (None, "", "no_bom"):
            part_rev.print_unit_cost = seller.unit_cost if seller else None
        else:
            part_rev.print_unit_cost = part_rev.bom_unit_cost
    return part_revs_page
