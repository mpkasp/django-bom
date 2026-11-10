import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from bom.csv_headers import BOMIndentedCSVHeaders, PartsListCSVHeadersSemiIntelligent, SellerPartCSVHeaders
from bom.forms import BOMCSVForm, PartsImportForm, VendorPricesImportForm, open_csv_reader
from bom.models import ChangeItem, Part
from bom.services import parts
from bom.services.errors import ServiceError

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


def bom_upload_edits(organization, parent_part, csv_text, mode):
    """The subpart edits an indented-BOM CSV makes, as ``(kind, payload)`` pairs, plus the import's
    warnings. Only BOM lines become edits, so the CSV may not need parts or revisions that do not exist yet. A
    Released parent is previewed against the next Draft it would get when the edits are applied."""
    if mode not in (BOMCSVForm.MODE_ADD, BOMCSVForm.MODE_REPLACE):
        raise ServiceError(f"Unknown mode {mode!r}. Valid modes: {BOMCSVForm.MODE_ADD}, {BOMCSVForm.MODE_REPLACE}.")
    parent_revision = parent_part.latest()
    if parent_revision is None:
        raise ServiceError(f"Part {parent_part.full_part_number()} has no revisions.")

    import_form = _preview_bom_upload(organization, parent_part, parent_revision, csv_text.encode(), mode)
    errors = [str(error) for field_errors in import_form.errors.values() for error in field_errors]
    if errors:
        raise ServiceError(f"The CSV has {len(errors)} error{'s' if len(errors) != 1 else ''}; nothing was changed.\n"
                           + '\n'.join(f"- {error}" for error in errors))
    changes = import_form.changes()
    missing = [f"{part['part_number']} rev {part['revision']}" for part in changes['parts']]
    if missing:
        raise ServiceError(
            f"The CSV needs parts or revisions that do not exist yet: {', '.join(missing)}. Create them with "
            f"create_part first, and give each row a revision that exists.")

    edits = []
    for assembly in changes['assemblies']:
        revision = parent_revision.revision if assembly['part_id'] == parent_part.id else assembly['revision']
        target = {'assembly_part_number': assembly['part_number'], 'assembly_revision': revision}
        for line in assembly['lines']:
            edits.append(_line_edit(organization, target, line))
    if not edits:
        raise ServiceError(f"The CSV makes no changes to the BOM of {parent_part.full_part_number()}.")
    return edits, import_form.warnings


def _preview_bom_upload(organization, parent_part, parent_revision, csv_bytes, mode):
    with transaction.atomic():
        if parent_revision.is_immutable():
            parts.create_next_revision(parent_revision)
        import_form = _build_import_form(
            organization, 'bom', csv_bytes, {'mode': mode, 'parent_part_id': parent_part.id})
        import_form.is_valid()
        transaction.set_rollback(True)
    return import_form


def _line_edit(organization, target, line):
    subpart = dict(target, subpart_part_number=line['part_number'])
    if line['change'] == 'removed':
        return ChangeItem.KIND_REMOVE_SUBPART, subpart
    latest = parts.revision_from_number(organization, line['part_number'])
    if latest.revision != line['revision']:
        raise ServiceError(
            f"{line['part_number']} is listed at rev {line['revision']}, but BOM lines always use a part's latest "
            f"revision, rev {latest.revision}. Use rev {latest.revision} in the CSV.")
    if line['change'] == 'updated':
        return ChangeItem.KIND_UPDATE_SUBPART, dict(subpart, count=line['new_count'], reference=line['new_reference'])
    return ChangeItem.KIND_ADD_SUBPART, dict(subpart, count=line['new_count'], reference=line['new_reference'],
                                              do_not_load=line['do_not_load'])


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
