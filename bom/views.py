import csv
import codecs
import logging
import os
import sys

from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotFound
from django.template.response import TemplateResponse
from django.db import IntegrityError, connection
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.encoding import smart_str

from social_django.models import UserSocialAuth

from json import loads, dumps
from math import ceil

from .convert import full_part_number_to_broken_part
from .models import Part, PartClass, Subpart, SellerPart, Organization, Manufacturer, ManufacturerPart, User, \
    UserMeta, PartRevision, Assembly, AssemblySubparts
from .forms import PartInfoForm, PartForm, AddSubpartForm, SubpartForm, FileForm, AddSellerPartForm, ManufacturerForm, \
    ManufacturerPartForm, SellerPartForm, UserForm, UserProfileForm, OrganizationForm, PartRevisionForm, \
    PartRevisionNewForm
from .octopart import match_part, get_latest_datasheets

logger = logging.getLogger(__name__)


@login_required
def home(request):
    profile = request.user.bom_profile()
    organization = profile.organization

    if profile.organization is None:
        organization, created = Organization.objects.get_or_create(
            owner=request.user,
            defaults={'name': request.user.first_name + ' ' + request.user.last_name,
                      'subscription': 'F'},
        )

        profile.organization = organization
        profile.role = 'A'
        profile.save()

    title = '{}Parts List'.format(organization.name + ' ')

    parts = Part.objects.filter(organization=organization).order_by('number_class__code',
                                                                    'number_item', 'number_variation')

    part_ids = list(parts.values_list('id', flat=True))

    part_rev_query = "select max(pr.id) as id from bom_partrevision as pr " \
                     "left join bom_part as p on pr.part_id = p.id " \
                     "left join bom_partclass as pc on pc.id = p.number_class_id " \
                     "where p.id in ({}) " \
                     "group by pr.part_id " \
                     "order by pc.code, p.number_item, p.number_variation"

    part_list = ','.join(map(str, part_ids)) if len(part_ids) > 0 else "NULL"
    q = part_rev_query.format(part_list)
    part_revs = PartRevision.objects.raw(q)

    manufacturer_part = ManufacturerPart.objects.filter(part__in=parts)

    autocomplete_dict = {}
    for part in part_revs:
        autocomplete_dict.update({part.description.replace('"', ''): None})
        # autocomplete_dict.update({ part.full_part_number(): None }) # TODO: query full part number

    for mpn in manufacturer_part:
        if mpn.manufacturer_part_number:
            autocomplete_dict.update({mpn.manufacturer_part_number.replace('"', ''): None})
        if mpn.manufacturer is not None and mpn.manufacturer.name:
            autocomplete_dict.update({mpn.manufacturer.name.replace('"', ''): None})

    autocomplete = dumps(autocomplete_dict)

    def numbers_from_part_string(s):
        number_class = None
        number_item = None
        number_variation = None

        if len(s) >= 3:
            number_class = s[:3]
            if len(s) >= 8 and s[3] == '-':
                number_item = s[4:8]
                if len(s) >= 10 and s[8] == '-':
                    number_variation = s[9:]

        return (number_class, number_item, number_variation)

    query = request.GET.get('q', '')
    if query:
        rq = query.strip()
        (number_class, number_item, number_variation) = numbers_from_part_string(rq)
        part_description_ids = PartRevision.objects.filter(description__icontains=query).values_list("part",
                                                                                                     flat=True)
        if number_class and number_item and number_variation:
            parts = parts.filter(
                Q(number_class__code=number_class, number_item=number_item, number_variation=number_variation) |
                Q(id__in=part_description_ids) |
                Q(primary_manufacturer_part__manufacturer_part_number__icontains=query) |
                Q(primary_manufacturer_part__manufacturer__name__icontains=query))
        elif number_class and number_item:
            parts = parts.filter(
                Q(number_class__code=number_class, number_item=number_item) |
                Q(id__in=part_description_ids) |
                Q(primary_manufacturer_part__manufacturer_part_number__icontains=query) |
                Q(primary_manufacturer_part__manufacturer__name__icontains=query))
        else:
            parts = parts.filter(
                Q(id__in=part_description_ids) |
                Q(primary_manufacturer_part__manufacturer_part_number__icontains=query) |
                Q(primary_manufacturer_part__manufacturer__name__icontains=query) |
                Q(number_class__code=query))

        part_ids = list(parts.values_list('id', flat=True))
        part_list = ','.join(map(str, part_ids)) if len(part_ids) > 0 else "NULL"
        q = part_rev_query.format(part_list)
        part_revs = PartRevision.objects.raw(q)

    return TemplateResponse(request, 'bom/dashboard.html', locals())


def error(request):
    msgs = messages.get_messages(request)
    return TemplateResponse(request, 'bom/error.html', locals())


@login_required
def bom_signup(request):
    user = request.user
    organization = user.bom_profile().organization
    title = 'Set Up Your BOM Organization'

    if organization is not None:
        return HttpResponseRedirect(reverse('bom:home'))

    return TemplateResponse(request, 'bom/bom-signup.html', locals())


@login_required
def bom_settings(request, tab_anchor=None):
    user = request.user
    organization = user.bom_profile().organization
    title = 'Settings'
    action = reverse('bom:settings')

    users_in_organization = User.objects.filter(
        id__in=UserMeta.objects.filter(organization=organization).values_list('user', flat=True)).order_by(
        'first_name', 'last_name', 'email')
    google_authentication = UserSocialAuth.objects.filter(user=user).first()
    user_form = UserForm(instance=user)
    organization_form = OrganizationForm(instance=organization)

    if request.method == 'POST':
        if 'submit-user' in request.POST:
            user_form = UserForm(request.POST, instance=user)
            organization_form = OrganizationForm(instance=organization)
            if user_form.is_valid():
                user = user_form.save()
            else:
                messages.error(request, user_form.errors)
        if 'submit-organization' in request.POST:
            organization_form = OrganizationForm(request.POST, instance=organization)
            user_form = UserForm(instance=user)
            if organization_form.is_valid():
                organization_form.save()
            else:
                messages.error(request, organization_form.errors)

    return TemplateResponse(request, 'bom/settings.html', locals())


@login_required
def part_info(request, part_id, part_revision_id=None):
    order_by = request.GET.get('order_by', 'indented')
    tab_anchor = request.GET.get('tab_anchor', None)

    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    part = get_object_or_404(Part, pk=part_id)

    part_revision = None
    if part_revision_id is None:
        part_revision = part.latest()
    else:
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)

    revisions = PartRevision.objects.filter(part=part_id).order_by('-id')

    if part.organization != organization:
        messages.error(request, "Cant access a part that is not yours!")
        return HttpResponseRedirect(reverse('bom:error'))

    qty_cache_key = str(part_id) + '_qty'
    qty = cache.get(qty_cache_key, 100)
    part_info_form = PartInfoForm(initial={'quantity': qty})
    upload_file_to_part_form = FileForm()

    if request.method == 'POST':
        part_info_form = PartInfoForm(request.POST)
        if part_info_form.is_valid():
            qty = request.POST.get('quantity', 100)

    cache.set(qty_cache_key, qty, 3600)

    # if part.primary_manufacturer_part is not None:
    #     try:
    #         datasheets = get_latest_datasheets(part.primary_manufacturer_part.manufacturer_part_number)
    #     except Exception as e:
    #         messages.warning(request, "Octopart error: {}".format(e))
    #         datasheets = []

    try:
        parts = part_revision.indented()
    except RuntimeError:
        messages.error(request, "Error: infinite recursion in part relationship. Contact info@indabom.com to resolve.")
        parts = []

    extended_cost_complete = True
    unit_cost = 0
    unit_nre = 0
    unit_out_of_pocket_cost = 0
    for item in parts:
        extended_quantity = int(qty) * item['total_quantity']
        item['extended_quantity'] = extended_quantity

        subpart = item['part']
        seller = subpart.optimal_seller(quantity=extended_quantity)
        order_qty = extended_quantity
        if seller is not None and seller.minimum_order_quantity is not None and extended_quantity > seller.minimum_order_quantity:
            order_qty = ceil(extended_quantity / float(seller.minimum_order_quantity)) * seller.minimum_order_quantity

        item['seller_price'] = seller.unit_cost if seller is not None else 0
        item['seller_nre'] = seller.nre_cost if seller is not None else 0
        item['seller_part'] = seller
        item['seller_moq'] = seller.minimum_order_quantity if seller is not None else 0
        item['order_quantity'] = order_qty

        # then extend that price
        item['extended_cost'] = extended_quantity * \
                                seller.unit_cost if seller is not None and seller.unit_cost is not None and extended_quantity is not None else None
        item['out_of_pocket_cost'] = order_qty * \
                                     float(
                                         seller.unit_cost) if seller is not None and seller.unit_cost is not None else 0

        unit_cost = (
                unit_cost +
                seller.unit_cost *
                item['quantity']) if seller is not None and seller.unit_cost is not None else unit_cost
        unit_out_of_pocket_cost = unit_out_of_pocket_cost + \
                                  item['out_of_pocket_cost']
        unit_nre = (
                unit_nre +
                item['seller_nre']) if item['seller_nre'] is not None else unit_nre
        if seller is None:
            extended_cost_complete = False

    # seller_price, seller_nre

    extended_cost = unit_cost * int(qty)
    total_out_of_pocket_cost = unit_out_of_pocket_cost + float(unit_nre)

    where_used = part_revision.where_used()
    where_used_part = part.where_used()
    seller_parts = part.seller_parts()

    if order_by != 'defaultOrderField' and order_by != 'indented':
        # tab_anchor = 'bom'
        parts = sorted(parts, key=lambda k: k[order_by], reverse=True)
    # elif order_by == 'indented':
    #     # anchor = 'bom'
    #     tab_anchor = None

    return TemplateResponse(request, 'bom/part-info.html', locals())


@login_required
def part_export_bom(request, part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "Part object does not exist.")
        return HttpResponseRedirect(reverse('bom:error'))

    if part.organization != organization:
        messages.error(request, "Cant export a part that is not yours!")
        return HttpResponseRedirect(reverse('bom:error'))

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="{}_indabom_parts_indented.csv"'.format(
        part.full_part_number())

    bom = part.indented()
    qty_cache_key = str(part_id) + '_qty'
    qty = cache.get(qty_cache_key, 1000)
    unit_cost = 0
    unit_out_of_pocket_cost = 0
    unit_nre = 0

    fieldnames = [
        'level',
        'part_number',
        'quantity',
        'reference',
        'part_description',
        'part_revision',
        'part_manufacturer',
        'part_manufacturer_part_number',
        'part_ext_qty',
        'part_order_qty',
        'part_seller',
        'part_cost',
        'part_moq',
        'part_ext_cost',
        'part_out_of_pocket_cost',
        'part_nre',
        'part_lead_time_days', ]

    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    for item in bom:
        extended_quantity = int(qty) * item['total_quantity']
        item['extended_quantity'] = extended_quantity

        subpart = item['part']
        seller = subpart.optimal_seller(quantity=extended_quantity)
        order_qty = extended_quantity
        if seller is not None and seller.minimum_order_quantity is not None and extended_quantity > seller.minimum_order_quantity:
            order_qty = ceil(extended_quantity / float(seller.minimum_order_quantity)) * seller.minimum_order_quantity

        item['seller_price'] = seller.unit_cost if seller is not None else 0
        item['seller_nre'] = seller.nre_cost if seller is not None else 0
        item['seller_part'] = seller
        item['seller_moq'] = seller.minimum_order_quantity if seller is not None else 0
        item['order_quantity'] = order_qty
        item['seller_lead_time_days'] = seller.lead_time_days if seller is not None else 0

        # then extend that price
        item['extended_cost'] = extended_quantity * \
                                seller.unit_cost if seller is not None and seller.unit_cost is not None and extended_quantity is not None else None
        item['out_of_pocket_cost'] = order_qty * \
                                     float(
                                         seller.unit_cost) if seller is not None and seller.unit_cost is not None else 0

        unit_cost = (
                unit_cost +
                seller.unit_cost *
                item['quantity']) if seller is not None and seller.unit_cost is not None else unit_cost
        unit_out_of_pocket_cost = unit_out_of_pocket_cost + \
                                  item['out_of_pocket_cost']
        unit_nre = (
                unit_nre +
                item['seller_nre']) if item['seller_nre'] is not None else unit_nre
        if seller is None:
            extended_cost_complete = False

        row = {
            'level': item['indent_level'],
            'part_number': item['part'].full_part_number(),
            'quantity': item['quantity'],
            'reference': item['reference'],
            'part_description': item['part'].latest().description,
            'part_revision': item['part'].latest().revision,
            'part_manufacturer': item['part'].primary_manufacturer_part.manufacturer.name if item[
                                                                                                 'part'].primary_manufacturer_part is not None and
                                                                                             item[
                                                                                                 'part'].primary_manufacturer_part.manufacturer is not None else '',
            'part_manufacturer_part_number': item['part'].primary_manufacturer_part.manufacturer_part_number if item[
                                                                                                                    'part'].primary_manufacturer_part is not None else '',
            'part_ext_qty': item['extended_quantity'],
            'part_order_qty': item['order_quantity'],
            'part_seller': item['seller_part'].seller.name if item['seller_part'] is not None else '',
            'part_cost': item['seller_price'] if item['seller_price'] is not None else 0,
            'part_moq': item['seller_moq'] if item['seller_moq'] is not None else 0,
            'part_ext_cost': item['extended_cost'] if item['extended_cost'] is not None else 0,
            'part_out_of_pocket_cost': item['out_of_pocket_cost'],
            'part_nre': item['seller_nre'] if item['seller_nre'] is not None else 0,
            'part_lead_time_days': item['seller_lead_time_days'],
        }
        writer.writerow({k: smart_str(v) for k, v in row.items()})
    return response


@login_required
def part_upload_bom(request, part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "No part found with given part_id.")
        return HttpResponseRedirect(reverse('bom:error'))

    if request.method == 'POST':
        form = FileForm(request.POST, request.FILES)
        if form.is_valid():
            csvfile = request.FILES['file']
            # dialect = csv.Sniffer().sniff(csvfile.readline())
            csvfile.open()
            reader = csv.reader(codecs.iterdecode(csvfile, 'utf-8'))

            try:
                headers = [h.lower() for h in next(reader)]
            except UnicodeDecodeError as e:
                messages.error(request, "CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                    If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to fix your issue!")
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))
            # Subpart.objects.filter(assembly_part=part).delete()
            header_error = False
            if 'part_number' not in headers and 'manufacturer_part_number' not in headers:
                header_error = True
                messages.error(request, "Header `part_number` or `manufacturer_part_number` required for upload.")
            if 'quantity' not in headers:
                header_error = True
                messages.error(request, "Header `quantity` required for upload.")

            if header_error:
                return HttpResponseRedirect(reverse('bom:part-manage-bom', kwargs={'part_id': part_id}))

            for row in reader:
                partData = {}
                for idx, item in enumerate(row):
                    partData[headers[idx]] = item

                if 'dnp' in partData and partData['dnp'].lower() == 'dnp':
                    continue

                if 'part_number' in partData and 'quantity' in partData and len(partData['part_number']) > 0:

                    try:
                        civ = full_part_number_to_broken_part(
                            partData['part_number'])
                        subparts = Part.objects.filter(
                            number_class=civ['class'],
                            number_item=civ['item'],
                            number_variation=civ['variation'],
                            organization=organization)
                    except IndexError:
                        messages.error(
                            request, "Invalid part_number: {}".format(partData['part_number']))
                        continue

                    if len(subparts) == 0:
                        messages.info(
                            request, "Subpart: `{}` doesn't exist".format(
                                partData['part_number']))
                        continue
                    elif len(subparts) > 1:
                        messages.info(
                            request,
                            "Subpart: found {} entries for subpart `{}`. This should not happen. Please let info@indabom.com know.".format(
                                partData['part_number']))
                        continue

                    # TODO: handle more than one subpart
                    subpart = subparts[0]
                    count = partData['quantity']
                    revision = None

                    if 'rev' in partData:
                        revision = partData['rev']
                    elif 'revision' in partData:
                        revision = partData['rev']

                    reference = ''
                    if 'reference' in partData:
                        reference = partData['reference']
                    elif 'designator' in partData:
                        reference = partData['designator']

                    if part == subpart:
                        messages.error(
                            request, "Recursive part association: a part cant be a subpart of itsself")
                        return HttpResponseRedirect(reverse('bom:part-manage-bom', kwargs={'part_id': part_id}))

                    pr = part.latest()
                    if revision is not None:
                        pres = PartRevision.objects.filter(part=part, revision=revision)
                        if len(pres) > 0:
                            pr = pres[0]

                    Subpart.objects.create(
                        part_revision=pr,
                        count=count,
                        reference=reference,
                    )
                elif 'manufacturer_part_number' in partData and 'quantity' in partData:
                    mpn = partData['manufacturer_part_number']
                    manufacturer_parts = ManufacturerPart.objects.filter(manufacturer_part_number=mpn,
                                                                         part__organization=organization)

                    if len(manufacturer_parts) == 0:
                        messages.info(
                            request, "Part with manufacturer part number: `{}` doesn't exist, you must create the part "
                                     "before we can add it to an assembly".format(partData['manufacturer_part_number']))
                        continue

                    subpart = manufacturer_parts[0].part
                    count = partData['quantity']
                    if part == subpart:
                        messages.error(
                            request, "Recursive part association: a part cant be a subpart of itsself")
                        return HttpResponseRedirect(reverse('bom:part-manage-bom', kwargs={'part_id': part_id}))

                    reference = ''
                    if 'reference' in partData:
                        reference = partData['reference']
                    elif 'designator' in partData:
                        reference = partData['designator']

                    pr = part.latest()
                    Subpart.objects.create(
                        part_revision=pr,
                        count=count,
                        reference=reference,
                    )
        else:
            messages.error(
                request,
                "File form not valid: {}".format(
                    form.errors))
            return HttpResponseRedirect(reverse('bom:part-manage-bom', kwargs={'part_id': part_id}))

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))


@login_required
def upload_parts(request):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = 'Upload Parts'
    partclasses = PartClass.objects.all()
    if request.method == 'POST' and request.FILES['file'] is not None:
        form = FileForm(request.POST, request.FILES)
        if form.is_valid():
            csvfile = request.FILES['file']
            try:
                csvline_decoded = csvfile.readline().decode('utf-8')
                dialect = csv.Sniffer().sniff(csvline_decoded)
                csvfile.open()
                reader = csv.reader(codecs.iterdecode(csvfile, 'utf-8'), dialect)
                headers = [h.lower() for h in next(reader)]

                for row in reader:
                    part_data = {}
                    for idx, item in enumerate(row):
                        part_data[headers[idx]] = item
                    if 'part_class' in part_data and 'description' in part_data and 'revision' in part_data:
                        mpn = ''
                        mfg = None
                        if 'manufacturer_part_number' in part_data:
                            mpn = part_data['manufacturer_part_number']
                        elif 'mpn' in part_data:
                            mpn = part_data['mpn']

                        if 'manufacturer' in part_data:
                            mfg_name = part_data['manufacturer'] if part_data['manufacturer'] is not None else ''
                            mfg, created = Manufacturer.objects.get_or_create(name=mfg_name, organization=organization)
                        elif 'mfg' in part_data:
                            mfg_name = part_data['mfg'] if part_data['mfg'] is not None else ''
                            mfg, created = Manufacturer.objects.get_or_create(name=mfg_name, organization=organization)

                        manufacturer_part = ManufacturerPart.objects.filter(manufacturer_part_number=mpn,
                                                                            manufacturer=mfg)
                        if mpn != '' and manufacturer_part.count() > 0:
                            messages.warning(request, "Part already exists for manufacturer part: {}, skipping creating"
                                                      " this part.".format(mpn))
                            continue

                        try:
                            part_class = PartClass.objects.get(code=part_data['part_class'])
                        except PartClass.DoesNotExist:
                            messages.error(request, "Part Class {} doesn't exist.".format(part_data['part_class']))
                            return TemplateResponse(request, 'bom/upload-parts.html', locals())

                        if len(part_data['revision']) > 2:
                            messages.error(request, "Revision {} is more than the maximum 2 characters.".format(
                                part_data['revision']))
                            return TemplateResponse(request, 'bom/upload-parts.html', locals())

                        part = Part.objects.create(number_class=part_class, organization=organization)

                        pr = PartRevision.objects.create(part=part,
                                                         description=part_data['description'],
                                                         revision=part_data['revision'])

                        manufacturer_part, created = ManufacturerPart.objects.get_or_create(part=part,
                                                                                            manufacturer_part_number=mpn,
                                                                                            manufacturer=mfg)

                        if part.primary_manufacturer_part is None and manufacturer_part is not None:
                            part.primary_manufacturer_part = manufacturer_part
                            part.save()

                        messages.info(request, "{} - {} created.".format(part.full_part_number(), pr.description))
                    else:
                        messages.error(request, "File must contain at least the 3 columns (with headers): 'part_class',"
                                                " 'description', and 'revision'.")
                        return TemplateResponse(request, 'bom/upload-parts.html', locals())
            except UnicodeDecodeError as e:
                messages.error(request, "CSV File Encoding error, try encoding your file as utf-8, and upload again. \
                    If this keeps happening, reach out to info@indabom.com with your csv file and we'll do our best to \
                    fix your issue!")
                messages.error(request, "Specific Error: {}".format(e))
                logger.warning("UnicodeDecodeError: {}".format(e))
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))
        else:
            messages.error(request, "Invalid form input.")
            return TemplateResponse(request, 'bom/upload-parts.html', locals())
    else:
        form = FileForm()
        return TemplateResponse(request, 'bom/upload-parts.html', locals())

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))


@login_required
def export_part_list(request):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="indabom_parts.csv"'

    parts = Part.objects.filter(
        organization=organization).order_by(
        'number_class__code',
        'number_item',
        'number_variation')

    fieldnames = [
        'part_number',
        'part_description',
        'part_revision',
        'part_manufacturer',
        'part_manufacturer_part_number',
    ]

    writer = csv.DictWriter(response, fieldnames=fieldnames)
    writer.writeheader()
    for item in parts:
        try:
            row = {
                'part_number': item.full_part_number(),
                'part_description': item.latest().description,
                'part_revision': item.latest().revision,
                'part_manufacturer': item.primary_manufacturer_part.manufacturer.name if item.primary_manufacturer_part is not None and item.primary_manufacturer_part.manufacturer is not None else '',
                'part_manufacturer_part_number': item.primary_manufacturer_part.manufacturer_part_number if item.primary_manufacturer_part is not None and item.primary_manufacturer_part.manufacturer is not None else '',
            }
            writer.writerow({k: smart_str(v) for k, v in row.items()})
        except AttributeError as e:
            messages.warning(request, "No change history for part: {}. Can't export.".format(item.full_part_number()))

    return response


@login_required
def part_octopart_match(request, part_id):
    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "No part found with given part_id.")
        return HttpResponseRedirect(reverse('bom:error'))

    manufacturer_parts = ManufacturerPart.objects.filter(part=part)
    for manufacturer_part in manufacturer_parts:
        seller_parts = []
        try:
            seller_parts = match_part(manufacturer_part, request.user.bom_profile().organization)
        except IOError as e:
            messages.error(request, "Error communicating with Octopart. {}".format(e))
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            messages.error(request, "Error - {}: {}, ({}, {})".format(exc_type, e, fname, exc_tb.tb_lineno))
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))

        if len(seller_parts) > 0:
            SellerPart.objects.filter(manufacturer_part=manufacturer_part, data_source='octopart').delete()
            for sp in seller_parts:
                try:
                    sp.save()
                except IntegrityError:
                    continue
        else:
            messages.info(
                request,
                "Octopart wasn't able to find any parts with manufacturer part number: {}".format(
                    manufacturer_part.manufacturer_part_number))

    if request.META.get('HTTP_REFERER', None) is not None and '/part/' in request.META.get('HTTP_REFERER', None):
        return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part_id}) + '?tab_anchor=sourcing')

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:part-info', kwargs={
        'part_id': part_id}) + '?tab_anchor=sourcing'))


@login_required
def manufacturer_part_octopart_match(request, manufacturer_part_id):
    try:
        manufacturer_part = ManufacturerPart.objects.get(id=manufacturer_part_id)
    except ObjectDoesNotExist:
        messages.error(request, "No manufacturer part found with given part_id.")
        return HttpResponseRedirect(reverse('bom:error'))

    seller_parts = []
    try:
        seller_parts = match_part(manufacturer_part, request.user.bom_profile().organization)
    except IOError as e:
        messages.error(request, "Error communicating with Octopart. {}".format(e))
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')) + '#sourcing')
    except Exception as e:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        messages.error(request, "Error - {}: {}, ({}, {})".format(exc_type, e, fname, exc_tb.tb_lineno))
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')) + '#sourcing')

    if len(seller_parts) > 0:
        SellerPart.objects.filter(manufacturer_part=manufacturer_part, data_source='octopart').delete()
        for sp in seller_parts:
            try:
                sp.save()
            except IntegrityError:
                continue
    else:
        messages.info(
            request,
            "Octopart wasn't able to find any parts with manufacturer part number: {}".format(
                manufacturer_part.manufacturer_part_number))

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')) + '#sourcing')


@login_required
def part_octopart_match_bom(request, part_id):
    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "No part found with given part_id.")
        return HttpResponseRedirect(reverse('bom:error'))

    subparts = part.latest().assembly.subparts.all()
    seller_parts = []

    for subpart in subparts:
        pr = subpart.part_revision
        part = pr.part if pr is not None else None
        if part is None:
            messages.error(request, "No part found for subpart `{}` of part `{}`.".format(pr.id, pr.part))
            continue
        for manufacturer_part in part.manufacturer_parts():
            try:
                seller_parts = match_part(manufacturer_part, request.user.bom_profile().organization)
            except IOError as e:
                messages.error(request, "Error communicating with Octopart.")
                continue
            except Exception as e:
                messages.error(request, "Unknown Error: {}".format(e))
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')) + '#sourcing')

            if len(seller_parts) > 0:
                SellerPart.objects.filter(manufacturer_part=manufacturer_part, data_source='octopart').delete()
                for sp in seller_parts:
                    try:
                        sp.save()
                    except IntegrityError:
                        continue
            else:
                messages.info(
                    request,
                    "Octopart wasn't able to find any parts with manufacturer part number: {}".format(
                        manufacturer_part.manufacturer_part_number))
                continue

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('bom:home')))


@login_required
def create_part(request):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = 'Create New Part'

    if request.method == 'POST':
        part_form = PartForm(request.POST)
        manufacturer_form = ManufacturerForm(request.POST)
        manufacturer_part_form = ManufacturerPartForm(request.POST, organization=organization)
        if part_form.is_valid() and manufacturer_form.is_valid() and manufacturer_part_form.is_valid():
            mpn = manufacturer_part_form.cleaned_data['manufacturer_part_number']
            old_manufacturer = manufacturer_part_form.cleaned_data['manufacturer']
            new_manufacturer_name = manufacturer_form.cleaned_data['name']

            manufacturer = None
            if mpn:
                if old_manufacturer and new_manufacturer_name == '':
                    manufacturer = old_manufacturer
                elif new_manufacturer_name != '' and not old_manufacturer:
                    manufacturer, created = Manufacturer.objects.get_or_create(name=new_manufacturer_name,
                                                                               organization=organization)
                else:
                    messages.error(request, "Either create a new manufacturer, or select an existing manufacturer.")
                    return TemplateResponse(request, 'bom/create-part.html', locals())
            elif old_manufacturer or new_manufacturer_name != '':
                messages.warning(request,
                                 "No manufacturer was selected or created, no manufacturer part number was assigned.")

            new_part = part_form.save(commit=False)
            new_part.organization = organization
            new_part.save()

            updated_data = request.POST.copy()
            updated_data.update({'part': new_part})
            part_revision_form = PartRevisionForm(updated_data)
            if part_revision_form.is_valid():
                pr = part_revision_form.save(commit=False)
                pr.part = new_part
                pr.save()

            manufacturer_part = None
            if manufacturer is None:
                manufacturer, created = Manufacturer.objects.get_or_create(organization=organization,
                                                                           name=organization.name)

            manufacturer_part, created = ManufacturerPart.objects.get_or_create(
                part=new_part,
                manufacturer_part_number=new_part.full_part_number() if mpn == '' else mpn,
                manufacturer=manufacturer)

            new_part.primary_manufacturer_part = manufacturer_part
            new_part.save()

            return HttpResponseRedirect(
                reverse('bom:part-info', kwargs={'part_id': str(new_part.id)}))
    else:
        part_form = PartForm(initial={'organization': organization})
        part_revision_form = PartRevisionForm(initial={'revision': 1, 'organization': organization})
        manufacturer_form = ManufacturerForm(initial={'organization': organization})
        manufacturer_part_form = ManufacturerPartForm(organization=organization)

    return TemplateResponse(request, 'bom/create-part.html', locals())


@login_required
def part_edit(request, part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    part = get_object_or_404(Part, pk=part_id)
    title = 'Edit Part {}'.format(part.full_part_number())

    action = reverse('bom:part-edit', kwargs={'part_id': part_id})

    if request.method == 'POST':
        form = PartForm(request.POST, instance=part)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part_id}))
    else:
        form = PartForm(instance=part)

    return TemplateResponse(request, 'bom/bom-form.html', locals())


@login_required
def manage_bom(request, part_id, part_revision_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = 'Manage BOM'

    part = get_object_or_404(Part, pk=part_id)

    part_revision = get_object_or_404(PartRevision, pk=part_revision_id)

    if part.organization != organization:
        messages.error(request, "Cant access a part that is not yours!")
        return HttpResponseRedirect(reverse('bom:error'))

    add_subpart_form = AddSubpartForm(initial={'count': 1, }, organization=organization, part_id=part_id)
    upload_subparts_csv_form = FileForm()

    parts = part_revision.indented()

    qty_cache_key = str(part_id) + '_qty'
    qty = cache.get(qty_cache_key, 100)

    for item in parts:
        extended_quantity = qty * item['total_quantity']
        seller = item['part'].optimal_seller(quantity=extended_quantity)
        item['seller_price'] = seller.unit_cost if seller is not None else None
        item['seller_part'] = seller

    return TemplateResponse(request, 'bom/part-rev-manage-bom.html', locals())


@login_required
def part_delete(request, part_id):
    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "No part found with given part_id.")
        return HttpResponseRedirect(reverse('bom:error'))

    part.delete()

    return HttpResponseRedirect(reverse('bom:home'))


@login_required
def add_subpart(request, part_id, part_revision_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    part_revision = get_object_or_404(PartRevision, pk=part_revision_id)

    if request.method == 'POST':
        form = AddSubpartForm(request.POST, organization=organization, part_id=part_id)
        if form.is_valid():
            if form.cleaned_data['subpart_part'].latest() is None:
                PartRevision.objects.create(part=form.cleaned_data['subpart_part'], description="", revision="1")

            new_part = Subpart.objects.create(
                part_revision=form.cleaned_data['subpart_part'].latest(),
                count=form.cleaned_data['count'],
                reference=form.cleaned_data['reference'])

            part_revision.assembly.subparts.add(new_part)
            part_revision.assembly.save()
        else:
            messages.error(request, form.errors)
    return HttpResponseRedirect(
        reverse('bom:part-manage-bom', kwargs={'part_id': part_id, 'part_revision_id': part_revision_id}))


@login_required
def remove_subpart(request, part_id, part_revision_id, subpart_id):
    subpart = get_object_or_404(Subpart, pk=subpart_id)
    subpart.delete()
    return HttpResponseRedirect(
        reverse('bom:part-manage-bom', kwargs={'part_id': part_id, 'part_revision_id': part_revision_id}))


@login_required
def edit_subpart(request, part_id, part_revision_id, subpart_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    action = reverse('bom:part-edit-subpart', kwargs={'part_id': part_id, 'subpart_id': subpart_id,
                                                      'part_revision_id': part_revision_id})

    part = get_object_or_404(Part, pk=part_id)
    subpart = get_object_or_404(Subpart, pk=subpart_id)

    title = "Edit Subpart"
    h1 = "{} {}".format(subpart.part_revision.part.full_part_number(), subpart.part_revision.description)

    if request.method == 'POST':
        form = SubpartForm(request.POST, instance=subpart, organization=organization,
                           part_id=subpart.part_revision.part.id)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(reverse('bom:part-manage-bom', kwargs={'part_id': part_id,
                                                                               'part_revision_id': part_revision_id}))
    else:
        form = SubpartForm(instance=subpart, organization=organization, part_id=subpart.part_revision.part.id)

    return TemplateResponse(request, 'bom/bom-form.html', locals())


@login_required
def remove_all_subparts(request, part_id, part_revision_id):
    subparts = Subpart.objects.filter(part_revision=part_revision_id)

    for subpart in subparts:
        subpart.delete()

    return HttpResponseRedirect(
        reverse('bom:part-manage-bom', kwargs={'part_id': part_id, 'part_revision_id': part_revision_id}))


@login_required
def add_sellerpart(request, manufacturer_part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = 'Add Seller Part'

    manufacturer_part = get_object_or_404(ManufacturerPart, pk=manufacturer_part_id)
    title = "Add Seller Part to {}".format(manufacturer_part)

    if request.method == 'POST':
        form = SellerPartForm(request.POST, manufacturer_part=manufacturer_part, organization=organization)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(
                reverse('bom:part-info', kwargs={'part_id': manufacturer_part.part.id}) + '#sourcing')
    else:
        form = SellerPartForm(organization=organization)

    return TemplateResponse(request, 'bom/bom-form.html', locals())


@login_required
def add_manufacturer_part(request, part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = 'Add Manufacturer Part'

    part = get_object_or_404(Part, pk=part_id)

    if request.method == 'POST':
        manufacturer_form = ManufacturerForm(request.POST)
        manufacturer_part_form = ManufacturerPartForm(request.POST, organization=organization)
        if manufacturer_form.is_valid() and manufacturer_part_form.is_valid():
            manufacturer_part_number = manufacturer_part_form.cleaned_data['manufacturer_part_number']
            manufacturer = manufacturer_part_form.cleaned_data['manufacturer']
            new_manufacturer_name = manufacturer_form.cleaned_data['name']

            if manufacturer is None and new_manufacturer_name == '':
                messages.error(request,
                               "Must either select an existing manufacturer, or enter a new manufacturer name.")
                return TemplateResponse(request, 'bom/add-manufacturer-part.html', locals())

            if new_manufacturer_name != '' and new_manufacturer_name is not None:
                manufacturer, created = Manufacturer.objects.get_or_create(name=new_manufacturer_name,
                                                                           organization=organization)
                manufacturer_part_form.cleaned_data['manufacturer'] = manufacturer

            manufacturer_part, created = ManufacturerPart.objects.get_or_create(part=part,
                                                                                manufacturer_part_number=manufacturer_part_number,
                                                                                manufacturer=manufacturer)

            if part.primary_manufacturer_part is None and manufacturer_part is not None:
                part.primary_manufacturer_part = manufacturer_part
                part.save()

            return HttpResponseRedirect(
                reverse('bom:part-info', kwargs={'part_id': str(part.id)}) + '?tab_anchor=sourcing')
        else:
            messages.error(request, "{}".format(manufacturer_form.is_valid()))
            messages.error(request, "{}".format(manufacturer_part_form.is_valid()))
    else:
        manufacturer_form = ManufacturerForm(initial={'organization': organization})
        manufacturer_part_form = ManufacturerPartForm(organization=organization)

    return TemplateResponse(request, 'bom/add-manufacturer-part.html', locals())


@login_required
def manufacturer_part_edit(request, manufacturer_part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = 'Edit Manufacturer Part'

    manufacturer_part = get_object_or_404(ManufacturerPart, pk=manufacturer_part_id)
    part = manufacturer_part.part

    if request.method == 'POST':
        manufacturer_part_form = ManufacturerPartForm(request.POST, instance=manufacturer_part,
                                                      organization=organization)
        manufacturer_form = ManufacturerForm(request.POST, instance=manufacturer_part.manufacturer)
        if manufacturer_part_form.is_valid() and manufacturer_form.is_valid():
            manufacturer_part_number = manufacturer_part_form.cleaned_data['manufacturer_part_number']
            manufacturer = manufacturer_part_form.cleaned_data['manufacturer']
            new_manufacturer_name = manufacturer_form.cleaned_data['name']

            if manufacturer is None and new_manufacturer_name == '':
                messages.error(request,
                               "Must either select an existing manufacturer, or enter a new manufacturer name.")
                return TemplateResponse(request, 'bom/edit-manufacturer-part.html', locals())

            new_manufacturer = None
            if new_manufacturer_name != '' and new_manufacturer_name is not None:
                new_manufacturer, created = Manufacturer.objects.get_or_create(name=new_manufacturer_name,
                                                                               organization=organization)
                manufacturer_part = manufacturer_part_form.save(commit=False)
                manufacturer_part.manufacturer = new_manufacturer
                manufacturer_part.save()
            else:
                manufacturer_part = manufacturer_part_form.save()

            if part.primary_manufacturer_part is None and manufacturer_part is not None:
                part.primary_manufacturer_part = manufacturer_part
                part.save()
            return HttpResponseRedirect(
                reverse('bom:part-info', kwargs={'part_id': manufacturer_part.part.id}) + '?tab_anchor=sourcing')
        else:
            messages.error(request, manufacturer_part_form.errors)
            messages.error(request, manufacturer_form.errors)
    else:
        if manufacturer_part.manufacturer is None:
            manufacturer_form = ManufacturerForm(instance=manufacturer_part.manufacturer,
                                                 initial={'organization': organization})
        else:
            manufacturer_form = ManufacturerForm(initial={'organization': organization})

        manufacturer_part_form = ManufacturerPartForm(instance=manufacturer_part, organization=organization, )

    return TemplateResponse(request, 'bom/edit-manufacturer-part.html', locals())


@login_required
def manufacturer_part_delete(request, manufacturer_part_id):
    manufacturer_part = get_object_or_404(ManufacturerPart, pk=manufacturer_part_id)
    part = manufacturer_part.part
    manufacturer_part.delete()

    return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part.id}) + '?tab_anchor=sourcing')


@login_required
def sellerpart_edit(request, sellerpart_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    title = "Edit Seller Part"
    action = reverse('bom:sellerpart-edit', kwargs={'sellerpart_id': sellerpart_id})
    sellerpart = get_object_or_404(SellerPart, pk=sellerpart_id)

    if request.method == 'POST':
        form = SellerPartForm(request.POST, instance=sellerpart, organization=organization)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(reverse('bom:part-info', kwargs={
                'part_id': sellerpart.manufacturer_part.part.id}) + '?tab_anchor=sourcing')
    else:
        form = SellerPartForm(instance=sellerpart, organization=organization)

    return TemplateResponse(request, 'bom/bom-form.html', locals())


@login_required
def sellerpart_delete(request, sellerpart_id):
    sellerpart = get_object_or_404(SellerPart, pk=sellerpart_id)
    part = sellerpart.manufacturer_part.part
    sellerpart.delete()
    return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part.id}) + '?tab_anchor=sourcing')


@login_required
def part_revision_release(request, part_id, part_revision_id):
    part = get_object_or_404(Part, pk=part_id)
    part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
    action = reverse('bom:part-revision-release', kwargs={'part_id': part.id, 'part_revision_id': part_revision.id})
    title = 'Promote {} Rev {} {} from <b>Working</b> to <b>Released</b>?'.format(part.full_part_number(),
                                                                                  part_revision.revision,
                                                                                  part_revision.description)

    subparts = part_revision.assembly.subparts.filter(part_revision__configuration="W")
    release_warning = subparts.count() > 0

    if request.method == 'POST':
        part_revision.configuration = 'R'
        part_revision.save()
        return HttpResponseRedirect(reverse('bom:part-info-history', kwargs={'part_id': part.id,
                                                                             'part_revision_id': part_revision.id}))

    return TemplateResponse(request, 'bom/part-revision-release.html', locals())


@login_required
def part_revision_revert(request, part_id, part_revision_id):
    part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
    part_revision.configuration = 'W'
    part_revision.save()
    return HttpResponseRedirect(reverse('bom:part-info-history', kwargs={'part_id': part_id,
                                                                         'part_revision_id': part_revision_id}))


@login_required
def part_revision_new(request, part_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization
    part = get_object_or_404(Part, pk=part_id)
    title = 'New Revision for {}'.format(part.full_part_number())
    action = reverse('bom:part-revision-new', kwargs={'part_id': part_id})

    latest_revision = part.latest()

    all_part_revisions = part.revisions()
    all_used_part_revisions = PartRevision.objects.filter(part=part)
    used_in_subparts = Subpart.objects.filter(part_revision__in=all_used_part_revisions)
    used_in_assembly_ids = AssemblySubparts.objects.filter(subpart__in=used_in_subparts).values_list('assembly',
                                                                                                     flat=True)
    all_used_in_prs = PartRevision.objects.filter(assembly__in=used_in_assembly_ids)
    used_part_revisions = all_used_in_prs.filter(configuration='W')

    if request.method == 'POST':
        form = PartRevisionNewForm(request.POST)
        if form.is_valid():
            new_part_revision = form.save()
            revisions_to_roll = request.POST.getlist('roll')
            # TODO: could optimize this, but probably shouldn't get too crazy so may be fine...
            for r_id in revisions_to_roll:
                subparts = PartRevision.objects.get(id=r_id).assembly.subparts \
                    .filter(part_revision__in=all_part_revisions)
                subparts.update(part_revision=new_part_revision)

            if form.cleaned_data['copy_assembly']:
                old_subparts = latest_revision.assembly.subparts.all() if latest_revision.assembly is not None else None
                new_assembly = latest_revision.assembly if latest_revision.assembly is not None else Assembly()
                new_assembly.pk = None
                new_assembly.save()

                form.cleaned_data['assembly'] = new_assembly

                new_part_revision.assembly = new_assembly
                new_part_revision.save()

                new_assembly.subparts.set(old_subparts)
            return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part_id}))
    else:
        next_revision_number = latest_revision.next_revision()
        messages.info(request, 'New revision automatically incremented to `{}` from your last revision `{}`.'
                      .format(next_revision_number, latest_revision.revision))
        next_revision = PartRevision(part=part,
                                     description=latest_revision.description,
                                     attribute=latest_revision.attribute,
                                     value=latest_revision.value,
                                     revision=next_revision_number)
        form = PartRevisionNewForm(instance=next_revision)

    return TemplateResponse(request, 'bom/part-revision-new.html', locals())


@login_required
def part_revision_edit(request, part_id, part_revision_id):
    user = request.user
    profile = user.bom_profile()
    organization = profile.organization

    part = get_object_or_404(Part, pk=part_id)
    part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
    title = 'Edit {} Rev {}'.format(part.full_part_number(), part_revision.revision)

    action = reverse('bom:part-revision-edit', kwargs={'part_id': part_id, 'part_revision_id': part_revision_id})

    if request.method == 'POST':
        form = PartRevisionForm(request.POST, instance=part_revision)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part_id}))
    else:
        form = PartRevisionForm(instance=part_revision)

    return TemplateResponse(request, 'bom/part-revision-edit.html', locals())


@login_required
def part_revision_delete(request, part_id, part_revision_id):
    user = request.user
    profile = user.bom_profile()

    part = get_object_or_404(Part, pk=part_id)

    if profile.role != 'A':
        messages.error(request, 'Only an admin can perform this action.')
        return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part.id}))

    part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
    part_revision.delete()
    messages.info(request, 'Deleted {} Rev {}'.format(part.full_part_number(), part_revision.revision))

    return HttpResponseRedirect(reverse('bom:part-info', kwargs={'part_id': part.id}))
