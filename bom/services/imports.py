import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from bom.csv_headers import BOMIndentedCSVHeaders, PartsListCSVHeadersSemiIntelligent, SellerPartCSVHeaders
from bom.forms import BOMCSVForm, PartsImportForm, VendorPricesImportForm, open_csv_reader
from bom.models import Part

IMPORT_FORMS = {'bom': BOMCSVForm, 'parts': PartsImportForm, 'prices': VendorPricesImportForm}
IMPORT_LABELS = {'bom': 'BOM', 'parts': 'parts', 'prices': 'vendor prices'}
IMPORT_TYPE_NAMES = {'parts': 'Parts', 'prices': 'Vendor prices', 'bom': 'Bill of materials'}


def run_import(organization, kind, csv_bytes, options, expected_changes_digest=None):
    """Runs an import in one transaction and keeps it only when it matches the changes the user confirmed.

    Without a digest this is a dry run: rows are processed so the form can report its changes, then
    rolled back. Options hold the import form's data as plain values (for a BOM import: mode and
    parent_part_id) so a preview can be stored in the session and re-run on confirm.
    """
    import_form = _build_import_form(organization, kind, csv_bytes, options)
    with transaction.atomic():
        import_form.is_valid()
        committed = (expected_changes_digest is not None and not import_form.errors
                     and import_form.changes_digest() == expected_changes_digest)
        if not committed:
            transaction.set_rollback(True)
    return import_form, committed


def _build_import_form(organization, kind, csv_bytes, options):
    form_data = dict(options)
    form_kwargs = {'organization': organization}
    parent_part_id = form_data.pop('parent_part_id', None)
    if parent_part_id is not None:
        form_kwargs['parent_part'] = Part.objects.get(id=parent_part_id, organization=organization)
    upload = SimpleUploadedFile(f'{kind}.csv', csv_bytes, content_type='text/csv')
    return IMPORT_FORMS[kind](form_data, {'file': upload}, **form_kwargs)


def read_csv_header_names(csv_bytes):
    """The lower-cased header row of a CSV file, or an empty list when the file has no readable header.

    An unreadable file still goes through the import form, which reports the problem to the user.
    """
    try:
        return open_csv_reader(io.BytesIO(csv_bytes))[1]
    except (csv.Error, UnicodeDecodeError, StopIteration):
        return []


def detect_import_kind(header_names):
    """The import type a file's columns ask for, when the user hasn't chosen one.

    Part-level columns win over price columns because the parts-list export carries both.
    """
    bom_headers = BOMIndentedCSVHeaders()
    if any(bom_headers.count_matches(header_names, name) for name in ('level', 'quantity')):
        return 'bom'
    parts_headers = PartsListCSVHeadersSemiIntelligent()
    if any(parts_headers.count_matches(header_names, name) for name in ('description', 'revision', 'part_class')):
        return 'parts'
    if any(name.startswith('property_') for name in header_names):
        return 'parts'
    seller_part_headers = SellerPartCSVHeaders()
    if any(seller_part_headers.count_matches(header_names, name) for name in ('unit_cost', 'seller')):
        return 'prices'
    return 'parts'
