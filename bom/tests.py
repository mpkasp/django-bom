import csv
import io
import json
from decimal import Decimal
from re import finditer
from unittest import skip
from unittest.mock import patch

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from . import constants
from django.core.files.uploadedfile import SimpleUploadedFile

from .forms import AddSubpartForm, BOMCSVForm, PartFormSemiIntelligent, PartInfoForm, SellerPartForm, sanitize_cost
from .helpers import (
    create_a_fake_assembly,
    create_a_fake_organization,
    create_a_fake_part_revision,
    create_a_fake_subpart,
    create_some_fake_manufacturers,
    create_some_fake_part_classes,
    create_some_fake_part_revision_property_definitions,
    create_some_fake_parts,
    create_user_and_organization,
)
from .models import Manufacturer, Part, PartClass, Seller, SellerPart, Subpart

TEST_FILES_DIR = "bom/test_files"

@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestBomAuth(TransactionTestCase):
    def setUp(self):
        self.client = Client()

    def test_create_organization(self):
        User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.client.login(username='kasper', password='ghostpassword')

        organization_form_data = {
            'name': 'Kasper Inc.',
            'number_scheme': 'S',
            'number_class_code_len': 3,
            'number_item_len': 4,
            'number_variation_len': 2,
        }

        response = self.client.post(reverse('bom:organization-create'), organization_form_data)
        self.assertEqual(response.status_code, 302)

    def test_create_organization_intelligent(self):
        User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.client.login(username='kasper', password='ghostpassword')

        organization_form_data = {
            'name': 'Kasper Inc.',
            'number_scheme': 'I',
        }

        response = self.client.post(reverse('bom:organization-create'), organization_form_data)
        self.assertEqual(response.status_code, 302)

    def test_create_organization_intelligent_with_fields(self):
        User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.client.login(username='kasper', password='ghostpassword')

        organization_form_data = {
            'name': 'Kasper Inc.',
            'number_scheme': 'I',
            'number_class_code_len': 3,
            'number_item_len': 4,
            'number_variation_len': 2,
        }

        response = self.client.post(reverse('bom:organization-create'), organization_form_data)
        self.assertEqual(response.status_code, 302)

@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestBOM(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()
        self.profile = self.user.bom_profile(organization=self.organization)
        self.profile.role = 'A'
        self.profile.save()
        self.client.login(username='kasper', password='ghostpassword')

    def test_home(self):
        response = self.client.post(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)

        # Make sure only one part shows up
        decoded_content = response.content.decode('utf-8')
        main_content = decoded_content[decoded_content.find('<main>')+len('<main>'):decoded_content.rfind('</main>')]
        occurances = [m.start() for m in finditer(p1.full_part_number(), main_content)]
        self.assertEqual(len(occurances), 1)

        response = self.client.get(reverse('bom:home'), {'q': p1.primary_manufacturer_part.manufacturer_part_number})
        self.assertEqual(response.status_code, 200)

        # Test search
        response = self.client.get(reverse('bom:home'), {'q': f'"{p1.full_part_number()}"'})
        self.assertEqual(len(response.context['part_revs']), 1)

    def test_part_info_renders_stored_datasheet(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        mp = p1.primary_manufacturer_part
        mp.datasheet_url = 'https://example.com/ds.pdf'
        mp.save()
        response = self.client.get(reverse('bom:part-info', kwargs={'part_id': p1.id}))
        self.assertContains(response, 'https://example.com/ds.pdf')

    def test_part_info(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:part-info', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-info', kwargs={'part_id': p2.id}))
        self.assertEqual(response.status_code, 200)

        # test having no revisions
        response = self.client.post(reverse('bom:part-info', kwargs={'part_id': p4.id}))
        self.assertEqual(response.status_code, 200)

        # set quantity
        response = self.client.post(reverse('bom:part-info', kwargs={'part_id': p1.id}), {'quantity': 1000})
        self.assertEqual(response.status_code, 200)

        # test cache hit - TODO: probably want to make sure cache works
        response = self.client.post(reverse('bom:part-info', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

    def test_part_manage_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse('bom:part-manage-bom', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id, }))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse('bom:part-manage-bom', kwargs={'part_id': p2.id, 'part_revision_id': p1.latest().id, }))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse('bom:part-manage-bom', kwargs={'part_id': p3.id, 'part_revision_id': p3.latest().id, }))
        self.assertEqual(response.status_code, 200)

    def test_part_export_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:part-export-bom', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-export-bom-sourcing', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-export-bom-sourcing-detailed', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-revision-export-bom-sourcing', kwargs={'part_revision_id': p3.latest().id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-revision-export-bom-sourcing-detailed', kwargs={'part_revision_id': p3.latest().id}))
        self.assertEqual(response.status_code, 200)

    def test_part_revision_export_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:part-revision-export-bom', kwargs={'part_revision_id': p1.latest().id}))
        self.assertEqual(response.status_code, 200)

    def test_part_revision_export_bom_flat(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:part-revision-export-bom-flat', kwargs={'part_revision_id': p1.latest().id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-revision-export-bom-flat-sourcing', kwargs={'part_revision_id': p1.latest().id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:part-revision-export-bom-flat-sourcing-detailed', kwargs={'part_revision_id': p1.latest().id}))
        self.assertEqual(response.status_code, 200)

    def test_export_parts(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:home'), {'download': ''}, follow=True)
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse('bom:home'), {'download': f'{p1.id}'}, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_part_upload_bom(self):
        sheen, voltage, _, _, _ = create_some_fake_part_revision_property_definitions(self.organization, False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        test_file = 'test_bom.csv' if self.organization.number_variation_len > 0 else 'test_bom_6_no_variations.csv'
        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p2.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertNotEqual(msg.tags, "error", msg.message)

        subparts = p2.latest().assembly.subparts.all()

        expected_pn = '200-3333-00' if self.organization.number_variation_len > 0 else '200-3333'
        self.assertEqual(subparts[0].part_revision.part.full_part_number(), expected_pn)
        self.assertEqual(subparts[0].count, 104)  # append 4, 99, 1

        expected_pn = '500-5555-00' if self.organization.number_variation_len > 0 else '500-5555'
        self.assertEqual(subparts[1].part_revision.part.full_part_number(), expected_pn)
        self.assertEqual(subparts[1].reference, 'U3, IC2, IC3')
        self.assertEqual(subparts[1].count, 3)
        self.assertEqual(subparts[1].do_not_load, False)

        self.assertEqual(subparts[2].part_revision.part.full_part_number(), expected_pn)
        self.assertEqual(subparts[2].reference, 'R1, R2')
        self.assertEqual(subparts[2].count, 2)
        self.assertEqual(subparts[2].do_not_load, True)

        with open(f'{TEST_FILES_DIR}/test_bom_2.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        message_texts = [str(msg.message) for msg in response.context.get('messages')]
        self.assertIn("Row 5 - manufacturer_part_number: Uploading of this subpart skipped. No part found for manufacturer part number.", message_texts)
        self.assertIn("Row 6 - manufacturer_part_number: Uploading of this subpart skipped. No part found for manufacturer part number.", message_texts)

        p1.refresh_from_db()
        bom = p1.latest().indented()
        self.assertEqual(len(bom.parts), 3)

    def test_part_upload_bom_with_properties(self):
        sheen, voltage, _, _, _ = create_some_fake_part_revision_property_definitions(self.organization)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        with open(f'{TEST_FILES_DIR}/test_bom_7_properties.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p2.id}), {'file': test_csv},
                                        follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertNotEqual(msg.tags, "error", msg.message)

    def test_upload_bom(self):
        create_some_fake_part_revision_property_definitions(self.organization, some_required=False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # Test OK page visit
        response = self.client.get(reverse('bom:upload-bom'))
        self.assertEqual(response.status_code, 200)

        # Test OK upload
        test_file = 'test_full_bom.csv' if self.organization.number_variation_len > 0 else 'test_full_bom_no_variations.csv'
        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:upload-bom'), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            reader = csv.DictReader(test_csv)
            test_list = list(reader)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "info")
            self.assertNotEqual(msg.tags, "error")

        parent_part_number = '100-0001-02' if self.organization.number_variation_len > 0 else '100-0001'
        parent_part = Part.from_part_number(parent_part_number, organization=self.organization)
        bom = parent_part.indented()
        bom_list = list(bom.parts.values())
        for idx, row in enumerate(test_list):
            self.assertEqual(row['part_number'], bom_list[idx].part.full_part_number(), f'Row {idx + 1}')
            self.assertIsNot(bom_list[idx].part.latest().synopsis(), '')

        # Check that we successfully updated an existing part (only tested for semi-intelligent scheme for now)
        if self.organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
            p2.refresh_from_db()
            p2_rev = p2.latest()
            p2_mp = p2.primary_manufacturer_part
            self.assertEqual(p2_rev.revision, '88')  # previously 1
            self.assertEqual(p2_rev.description, "123")  # previously 'Brown dog'
            self.assertEqual(p2_mp.manufacturer.name, "a new manufacturer name")  # previously None
            self.assertEqual(p2_mp.manufacturer_part_number, "a new mpn")  # previously 'GRM1555C1H100JA01D'

        # Check that parts get uploaded correctly
        for idx, item in enumerate(test_list):
            assertion_message = f'Index: {idx}, CSV PN: {item["part_number"]}, BOM PN: {bom_list[idx].part.full_part_number()}'
            self.assertEqual(int(float(item['level'])), bom_list[idx].indent_level, assertion_message)
            self.assertEqual(item['part_number'], bom_list[idx].part.full_part_number(), assertion_message)
            self.assertEqual(item['revision'], bom_list[idx].part_revision.revision, assertion_message)
            mfg_name = bom_list[idx].part.primary_manufacturer_part.manufacturer.name if bom_list[
                idx].part.primary_manufacturer_part else ''
            self.assertEqual(item['manufacturer_name'], mfg_name, assertion_message)
            mpn = bom_list[idx].part.primary_manufacturer_part.manufacturer_part_number if bom_list[
                idx].part.primary_manufacturer_part else ''
            self.assertEqual(item['manufacturer_part_number'], mpn, assertion_message)
            if bom_list[idx].indent_level > 0:
                self.assertEqual(float(item['quantity']), bom_list[idx].subpart.count, assertion_message)

        # Test OK upload with parent part number
        test_file = 'test_full_bom.csv' if self.organization.number_variation_len > 0 else 'test_full_bom_no_variations.csv'
        p4_rev = create_a_fake_part_revision(p4, create_a_fake_assembly())
        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:upload-bom'), {'file': test_csv, 'parent_part_number': p4.full_part_number()}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "info", msg.message)
            self.assertNotEqual(msg.tags, "error", msg.message)

        p4.refresh_from_db()
        p4_rev.refresh_from_db()
        self.assertEqual(len(p4_rev.indented().parts), 36)

        # Test errors get thrown
        test_file = 'test_full_bom_with_errors.csv' if self.organization.number_variation_len > 0 else 'test_full_bom_no_variations_with_errors.csv'
        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:upload-bom'), {'file': test_csv, 'parent_part_number': p3.full_part_number()}, follow=True)
        self.assertEqual(response.status_code, 200)

        # Each validation error is surfaced as its own plain-text message (no nested errorlist HTML).
        message_texts = [str(msg.message) for msg in response.context.get('messages')]

        if self.organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
            self.assertIn("Row 38 - part_number: Uploading of this subpart skipped. Couldn't parse part number.", message_texts)
            self.assertIn("Row 34 - code: Ensure this value has at most 3 characters (it has 9).", message_texts)
            self.assertIn("Row 33 - part_number: Uploading of this subpart skipped. Couldn't parse part number.", message_texts)
            self.assertIn("Row 35 - part_number: Uploading of this subpart skipped. Couldn't parse part number.", message_texts)
            self.assertIn("Row 36 - part_number: Uploading of this subpart skipped. Couldn't parse part number.", message_texts)
            self.assertIn("Row 37 - part_number: Uploading of this subpart skipped. Couldn't parse part number.", message_texts)
        self.assertIn("Row 39 - count: Ensure this value is greater than or equal to 0.", message_texts)
        self.assertIn("Row 40 - level: Assembly levels must decrease by no more than 1 from sequential rows.", message_texts)

        # Check that 2 rows of 103-0002-00 in one assembly gets combined into one part, and added to the 2 that already exist = 2 + 1 + 1
        parent_part_number = '107-0003-22' if self.organization.number_variation_len > 0 else '107-0003'
        parent_part = Part.from_part_number(parent_part_number, organization=self.organization)
        bom = parent_part.indented()
        part_number_to_check = '103-0002-00' if self.organization.number_variation_len > 0 else '103-0002'
        self.assertEqual(list(bom.parts.values())[6].part.full_part_number(), part_number_to_check)
        self.assertEqual(list(bom.parts.values())[6].subpart.count, 4)

        # Test infinite recursion error gets thrown
        test_file = 'test_full_bom_with_errors_infinite_recursion.csv' if self.organization.number_variation_len > 0 else 'test_full_bom_no_variations_with_errors_infinite_recursion.csv'
        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:upload-bom'), {'file': test_csv, 'parent_part_number': p3.full_part_number()}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for idx, msg in enumerate(messages):
            self.assertTrue("it would cause infinite recursion. Uploading of this subpart skipped." in str(msg.message))
            self.assertTrue("Row 15" in str(msg.message))

    def test_upload_bom_skipped_parent_does_not_crash_children(self):
        # Regression: a level-1 row that gets skipped (here, an unknown manufacturer part
        # number) followed by a deeper level-2 row used to push a None parent onto the tree
        # and crash with "'NoneType' object has no attribute 'assembly'". The child of a
        # skipped parent must instead be skipped/warned, never mis-nested or crashed. The
        # skip trigger is scheme-independent so this runs under both number schemes.
        create_some_fake_part_revision_property_definitions(self.organization, some_required=False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        csv_content = (
            "level,part_number,manufacturer_part_number,quantity\n"
            "1,,NONEXISTENT-MPN-XYZ,1\n"                # unknown mpn -> skipped (no valid parent)
            f"2,{p1.full_part_number()},,1\n"           # valid part, but its parent above was skipped
        )
        uploaded = SimpleUploadedFile("orphan_bom.csv", csv_content.encode("utf-8"), content_type="text/csv")

        part_count_before = Part.objects.filter(organization=self.organization).count()

        form = BOMCSVForm({}, {"file": uploaded}, parent_part=None, organization=self.organization)
        form.is_valid()  # runs clean(); must not raise or produce an "unexpected error"

        error_texts = [str(e) for errs in form.errors.values() for e in errs]
        for text in error_texts:
            self.assertNotIn("NoneType", text)
            self.assertNotIn("unexpected error", text.lower())

        # The unresolvable parent is reported, and its orphaned child is skipped with a warning.
        self.assertTrue(any("No part found for manufacturer part number" in t for t in error_texts))
        self.assertTrue(
            any("parent row above it was not uploaded" in w for w in form.warnings),
            msg=f"Expected an orphaned-child skip warning, got warnings: {form.warnings}",
        )

        # The orphaned child is skipped before saving, so no new part is created (and it is
        # certainly not rooted as a top-level part).
        self.assertEqual(Part.objects.filter(organization=self.organization).count(), part_count_before)

    def test_upload_bom_reference_designator_warnings(self):
        # Regression: duplicate reference designators emit a warning via a code path that
        # previously called a non-existent self.add_warning(), raising AttributeError that was
        # swallowed as an "unexpected error" and silently failed the row. Warnings must not
        # block a valid subpart from uploading.
        create_some_fake_part_revision_property_definitions(self.organization, some_required=False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # Duplicate reference ("R1, R1") with a matching quantity (2) so SubpartForm accepts it
        # and processing reaches the duplicate-designator warning.
        csv_content = (
            "part_number,quantity,reference\n"
            f'{p1.full_part_number()},2,"R1, R1"\n'
        )
        uploaded = SimpleUploadedFile("ref_warnings.csv", csv_content.encode("utf-8"), content_type="text/csv")

        form = BOMCSVForm({}, {"file": uploaded}, parent_part=p2, organization=self.organization)
        self.assertTrue(form.is_valid(), msg=f"Warnings must not invalidate the form: {dict(form.errors)}")

        error_texts = [str(e) for errs in form.errors.values() for e in errs]
        self.assertFalse(any("unexpected error" in t.lower() for t in error_texts), msg=error_texts)

        self.assertTrue(any("Duplicate reference designators" in w for w in form.warnings), msg=form.warnings)

        # The subpart was actually uploaded despite the warning.
        p2.refresh_from_db()
        subpart_pns = [sp.part_revision.part.full_part_number() for sp in p2.latest().assembly.subparts.all()]
        self.assertIn(p1.full_part_number(), subpart_pns)

    def test_upload_bom_with_properties(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # Test OK upload
        test_file = 'test_full_bom.csv'
        with open(f'{TEST_FILES_DIR}/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:upload-bom'), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_part_upload_bom_corner_cases(self):
        create_some_fake_part_revision_property_definitions(self.organization, False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        with open(f'{TEST_FILES_DIR}/test_bom_3_recursion.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "error")
            self.assertTrue("recursion" in str(msg.message))

        with open(f'{TEST_FILES_DIR}/test_bom_4_no_part_rev.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertNotEqual(msg.tags, "error", msg.message)  # Should be OK since we will default revision to 1

    def test_export_part_list(self):
        create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:export-part-list'))
        self.assertEqual(response.status_code, 200)

    def test_create_edit_part_class(self):
        part_class_code = 978
        part_class_form_data = {
            'submit-part-class-create': '',
            'code': part_class_code,
            'name': 'test part name',
            'comment': 'this test part class description!'
        }

        response = self.client.post(reverse('bom:settings'), part_class_form_data)
        self.assertEqual(response.status_code, 200)

        part_classes = PartClass.objects.filter(code=part_class_code)
        self.assertEqual(part_classes.count(), 1)
        part_class = part_classes[0]

        # Test edit with property definitions
        _, prop_def, _, _, _ = create_some_fake_part_revision_property_definitions(self.organization, False)
        part_class_form_data['name'] = 'edited test part name'
        part_class_form_data.update({
            'prop-def-TOTAL_FORMS': '1',
            'prop-def-INITIAL_FORMS': '0',
            'prop-def-MIN_NUM_FORMS': '0',
            'prop-def-MAX_NUM_FORMS': '1000',
            'prop-def-0-property_definition': prop_def.id,
        })

        response = self.client.post(reverse('bom:part-class-edit', kwargs={'part_class_id': part_class.id}), part_class_form_data)
        self.assertEqual(response.status_code, 302)

        part_class.refresh_from_db()
        self.assertEqual(part_class.name, part_class_form_data['name'])
        self.assertEqual(part_class.property_definitions.count(), 1)
        prop_def = part_class.property_definitions.first()
        self.assertEqual(prop_def.name, 'Voltage')

        # Test deleting property definition
        part_class_form_data.update({
            'prop-def-INITIAL_FORMS': '1',
            'prop-def-0-property_definition': prop_def.id,
            'prop-def-0-DELETE': 'on',
        })
        response = self.client.post(reverse('bom:part-class-edit', kwargs={'part_class_id': part_class.id}),
                                    part_class_form_data)
        self.assertEqual(response.status_code, 302)
        part_class.refresh_from_db()
        self.assertEqual(part_class.property_definitions.count(), 0)

    def test_create_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': new_part_mpn,
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': str(p1.number_class),
            'number_item': '',
            'number_variation': '',
            'configuration': 'D',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        try:
            created_part_id = response.url[6:-1]
            created_part = Part.objects.get(id=created_part_id)
        except IndexError:
            self.assertFalse(True, "Part maybe not created? Url looks like: {}".format(response.url))

        self.assertEqual(created_part.latest().description, 'IC, MCU 32 Bit')
        self.assertEqual(created_part.manufacturer_parts().first().manufacturer_part_number, new_part_mpn)

        new_part_form_data = {
            'manufacturer_part_number': 'STM32F401',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': str(p1.number_class),
            'number_item': '9999',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        if self.organization.number_variation_len > 0:
            new_part_form_data['number_variation'] = '01'

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_class': str(p1.number_class),
            'number_item': '',
            'number_variation': '',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_class': str(p1.number_class),
            'number_item': '1234',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        if self.organization.number_variation_len > 0:
            new_part_form_data['number_variation'] = 'AZ'

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_class': str(p1.number_class),
            'number_item': '1235',
            'number_variation': '',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        # fail nicely - if manufacturer part number exists, so should a manufacturer
        new_part_form_data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': '',
            'number_class': str(p1.number_class),
            'number_item': '',
            'number_variation': '',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 200)

        # Make sure only one part shows up
        response = self.client.get(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)
        decoded_content = response.content.decode('utf-8')
        main_content = decoded_content[decoded_content.find('<main>')+len('<main>'):decoded_content.rfind('</main>')]

        occurances = [m.start() for m in finditer(p1.full_part_number(), main_content)]
        self.assertEqual(len(occurances), 1)

    def test_create_part_with_image(self):
        from PIL import Image

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), color='red').save(buffer, format='PNG')
        image = SimpleUploadedFile('part.png', buffer.getvalue(), content_type='image/png')

        new_part_form_data = {
            'configuration': 'D',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
            'image': image,
        }
        if self.organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
            new_part_form_data['number_class'] = str(p1.number_class)
            new_part_form_data['number_item'] = ''
            new_part_form_data['number_variation'] = ''
        else:
            new_part_form_data['number_item'] = 'IMG-PART'

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        created_part = Part.objects.get(id=response.url[6:-1])
        self.assertTrue(created_part.image)
        self.assertTrue(created_part.image.name.startswith('part_images/'))

        # The part-info page should render the uploaded picture.
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(created_part.image.url, response.content.decode('utf-8'))

        # Removing the picture clears the field and deletes the file from storage.
        image_storage = created_part.image.storage
        image_name = created_part.image.name
        response = self.client.get(reverse('bom:part-image-delete', kwargs={'part_id': created_part.id}))
        self.assertEqual(response.status_code, 302)
        created_part.refresh_from_db()
        self.assertFalse(created_part.image)
        self.assertFalse(image_storage.exists(image_name))

    def test_part_image_upload_replace_remove(self):
        from PIL import Image

        def make_image(color):
            buffer = io.BytesIO()
            Image.new('RGB', (8, 8), color=color).save(buffer, format='PNG')
            return SimpleUploadedFile(f'{color}.png', buffer.getvalue(), content_type='image/png')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # Upload
        response = self.client.post(reverse('bom:part-image-upload', kwargs={'part_id': p1.id}),
                                    {'image': make_image('red')})
        self.assertEqual(response.status_code, 302)
        p1.refresh_from_db()
        self.assertTrue(p1.image)
        first_name = p1.image.name
        self.assertTrue(p1.image.storage.exists(first_name))

        # Part page renders the editable thumbnail and the modal upload form.
        response = self.client.get(reverse('bom:part-info', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('part-image-modal', body)
        self.assertIn(p1.image.url, body)

        # Replace: new file is stored and the previous one is removed from storage.
        storage = p1.image.storage
        response = self.client.post(reverse('bom:part-image-upload', kwargs={'part_id': p1.id}),
                                    {'image': make_image('blue')})
        self.assertEqual(response.status_code, 302)
        p1.refresh_from_db()
        self.assertNotEqual(p1.image.name, first_name)
        self.assertTrue(storage.exists(p1.image.name))
        self.assertFalse(storage.exists(first_name))

        # Remove
        second_name = p1.image.name
        response = self.client.get(reverse('bom:part-image-delete', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)
        p1.refresh_from_db()
        self.assertFalse(p1.image)
        self.assertFalse(storage.exists(second_name))

    def test_create_part_variation(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': new_part_mpn,
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': (p1.number_class),
            'number_item': '2000',
            'number_variation': '01',
            'configuration': 'D',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        new_part_form_data['number_variation'] = '02'
        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        # Part should be created because the variation is different, redirect means part was created
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        # Part should NOT be created because the variation is the same, 200 means error
        self.assertEqual(response.status_code, 200)
        self.assertTrue('error' in str(response.content))
        self.assertTrue('already in use' in str(response.content))

    def test_create_part_no_manufacturer_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_class': str(p1.number_class),
            'number_item': '2000',
            'configuration': 'D',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        number_variation = None
        if self.organization.number_variation_len > 0:
            number_variation = '01'
            new_part_form_data['number_variation'] = number_variation

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        part = Part.objects.get(number_class=p1.number_class.id, number_item='2000', number_variation=number_variation)
        self.assertEqual(len(part.manufacturer_parts()), 0)

    def test_part_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:part-edit', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        edit_part_form_data = {
            'number_class': str(p1.number_class),
            'number_item': '',
            'number_variation': '',
        }

        response = self.client.post(reverse('bom:part-edit', kwargs={'part_id': p1.id}), edit_part_form_data)
        self.assertEqual(response.status_code, 302)

    def test_part_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:part-delete', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    def test_add_subpart(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # Submit with no form data
        response = self.client.post(reverse('bom:part-add-subpart', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id, }))
        self.assertEqual(response.status_code, 302)

        # Test adding two of the same subparts that also have assemblies. Make sure quantity gets incremented, and not 2 parts that are the same added
        form_data = {'subpart_part_number': p2.full_part_number(), 'count': 3, 'reference': '', 'do_not_load': False}
        response = self.client.post(reverse('bom:part-add-subpart', kwargs={'part_id': p3.id, 'part_revision_id': p3.latest().id, }), form_data)
        self.assertEqual(response.status_code, 302)

        # Below - make sure quantity gets incremented, not that there are > 1 parts
        repeat_part_revision = p2.latest()
        parts_p2 = 0
        qty_p2 = 0
        indented_bom = p3.latest().indented()
        for _, p in indented_bom.parts.items():
            if p.part_revision == repeat_part_revision:
                parts_p2 += 1
                qty_p2 = p.quantity
        self.assertEqual(1, parts_p2)
        self.assertEqual(7, qty_p2)

        # Test adding a third, but make it DNL
        form_data = {'subpart_part_number': p2.full_part_number(), 'count': 3, 'reference': '', 'do_not_load': True}
        response = self.client.post(reverse('bom:part-add-subpart', kwargs={'part_id': p3.id, 'part_revision_id': p3.latest().id, }), form_data)
        self.assertEqual(response.status_code, 302)

        # Below - make sure quantity gets incremented, not that there are > 1 parts
        repeat_part_revision = p2.latest()
        parts_p2 = 0
        qty_p2_load = 0
        qty_p2_do_not_load = 0
        indented_bom = p3.latest().indented()
        for _, p in indented_bom.parts.items():
            if p.part_revision == repeat_part_revision:
                parts_p2 += 1
            if p.part_revision == repeat_part_revision and p.do_not_load:
                qty_p2_do_not_load += p.quantity
            elif p.part_revision == repeat_part_revision:
                qty_p2_load += p.quantity

        self.assertEqual(2, parts_p2)
        self.assertEqual(3, qty_p2_do_not_load)
        self.assertEqual(7, qty_p2_load)

    def test_add_subpart_infinite_recursion(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # Test preventing infinite recursion
        form_data = {'subpart_part_number': p3.full_part_number(), 'count': 3, 'reference': '', 'do_not_load': False}
        response = self.client.post(reverse('bom:part-add-subpart', kwargs={'part_id': p3.id, 'part_revision_id': p3.latest().id, }), form_data)
        self.assertEqual(response.status_code, 302)
        found_error = False
        rejected_add = False
        for m in response.wsgi_request._messages:
            if 'Added' in str(m):
                found_error = True
            if "Infinite recursion!" in str(m):
                rejected_add = True
        self.assertFalse(found_error)
        self.assertTrue(rejected_add)

        # Test preventing infinite recursion - Check that a subpart doesnt exist in a parent's parent assy / deep recursion
        # p3 has p2 in its assy, dont let p2 add p3 to it
        form_data = {'subpart_part_number': p3.full_part_number(), 'count': 3, 'reference': '', 'do_not_load': False}
        response = self.client.post(reverse('bom:part-add-subpart', kwargs={'part_id': p2.id, 'part_revision_id': p2.latest().id, }), form_data)
        self.assertEqual(response.status_code, 302)
        found_error = False
        rejected_add = False
        for m in response.wsgi_request._messages:
            if 'Added' in str(m):
                found_error = True
            if "Infinite recursion!" in str(m):
                rejected_add = True
        self.assertFalse(found_error)
        self.assertTrue(rejected_add)

    def test_remove_subpart(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        s1 = create_a_fake_subpart(p1.latest(), count=10)

        response = self.client.post(
            reverse('bom:part-remove-subpart',
                    kwargs={'part_id': p1.id, 'subpart_id': s1.id, 'part_revision_id': p1.latest().id, }))
        self.assertEqual(response.status_code, 302)

    def test_remove_all_subparts(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        part = p3
        part_revision = part.latest()

        subparts = part_revision.assembly.subparts.all()
        subpart_ids = list(subparts.values_list('id', flat=True))

        response = self.client.post(
            reverse('bom:part-remove-all-subparts', kwargs={'part_id': part.id, 'part_revision_id': part_revision.id}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(0, len(part_revision.assembly.subparts.all()))

        subparts = Subpart.objects.filter(id__in=subpart_ids)
        self.assertEqual(0, len(subparts))

    def test_upload_parts(self):
        create_some_fake_part_classes(self.organization)

        # Should pass
        with open(f'{TEST_FILES_DIR}/test_new_parts.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv}, follow=True)
        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, 'info')
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 4)

        # Part revs should be created for each part
        for p in Part.objects.all():
            self.assertIsNotNone(p.latest())

        # Should fail because class doesn't exist
        with open(f'{TEST_FILES_DIR}/test_new_parts_2.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        found_error = False
        for m in response.wsgi_request._messages:
            if "Part class 216 in row 2" in str(m) and "Uploading of this part skipped." in str(m):
                found_error = True
        self.assertTrue(found_error)

        # Part should be skipped because it already exists
        with open(f'{TEST_FILES_DIR}/test_new_parts_3.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        found_error = False
        for m in response.wsgi_request._messages:
            if "Part already exists for manufacturer part 2 in row GhostBuster2000. Uploading of this part skipped." in str(m):
                found_error = True
        self.assertTrue(found_error)

    def test_upload_parts_break_too_many_characters(self):
        pc1, _, _ = create_some_fake_part_classes(self.organization)
        create_some_fake_part_revision_property_definitions(self.organization, part_class=pc1)

        # Should break with data error
        with open(f'{TEST_FILES_DIR}/test_new_parts_broken.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv}, follow=True)
        messages = list(response.context.get('messages'))

        self.assertTrue(len(messages) == 1)
        msg = messages[0]
        self.assertEqual(msg.tags, 'error', msg.message)
        self.assertIn('Error on Row 2, property_sheen: Ensure this value has at most 255 characters (it has 483)',
                      msg.message)

    def test_upload_part_with_sellers(self):
        create_some_fake_part_classes(self.organization)
        # Should pass
        initial_parts_count = Part.objects.all().count()
        with open('bom/test_files/test_new_parts_sellers.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)

        parts_count = Part.objects.all().count()
        self.assertEqual(parts_count - initial_parts_count, 4)

    def test_sanitize_cost(self):
        # Currency symbols and thousands separators are stripped to a plain decimal string;
        # empty/None are passed through so existing validation/skip behavior is preserved.
        self.assertEqual(sanitize_cost("A$1,190.00"), "1190")
        self.assertEqual(sanitize_cost("A$0.00"), "0")
        self.assertEqual(sanitize_cost("$1234.56"), "1234.56")
        self.assertEqual(sanitize_cost("1.2345"), "1.2345")
        self.assertEqual(sanitize_cost(""), "")
        self.assertIsNone(sanitize_cost(None))

    def test_upload_part_costs_with_currency_symbol_and_export_roundtrip(self):
        # Regression: costs were exported as "A$0.00"/"A$1,190.00", which failed re-import
        # ("costs must be decimal values only"). Export now emits plain decimal amounts, and
        # import tolerates a currency symbol / thousands separators.
        create_some_fake_part_classes(self.organization)

        if self.organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
            csv_content = (
                "part_class,description,revision,manufacturer,manufacturer_part_number,seller,unit_cost,part_nre_cost,moq,mpq\n"
                '200,Cost Test,A,MfgX,MPN-COST-1,SellerX,"A$1,190.00","A$0.00",100,10\n'
            )
        else:
            csv_content = (
                "part_number,description,revision,manufacturer,manufacturer_part_number,seller,unit_cost,part_nre_cost,moq,mpq\n"
                'PC-COST-1,Cost Test,A,MfgX,MPN-COST-1,SellerX,"A$1,190.00","A$0.00",100,10\n'
            )

        f = io.BytesIO(csv_content.encode())
        f.name = "costs.csv"
        response = self.client.post(reverse("bom:upload-parts"), {"file": f})
        self.assertEqual(response.status_code, 302)

        # The currency-formatted costs imported into a real SellerPart with the right amounts.
        seller_part = SellerPart.objects.get(manufacturer_part__manufacturer_part_number="MPN-COST-1")
        self.assertEqual(seller_part.unit_cost.amount, Decimal("1190"))
        self.assertEqual(seller_part.nre_cost.amount, Decimal("0"))

        # Export emits plain decimal amounts, not currency-formatted Money ("A$...").
        exported = seller_part.as_dict_for_export()
        self.assertEqual(exported["unit_cost"], Decimal("1190"))
        self.assertNotIn("$", str(exported["unit_cost"]))
        self.assertNotIn("$", str(exported["nre_cost"]))

    def test_upload_parts_no_manufacturer_column_does_not_create_blank_manufacturer(self):
        # Bug: uploading a parts CSV that has manufacturer_part_number but no manufacturer
        # column (or an empty manufacturer value) should NOT create a Manufacturer with a
        # blank/empty name, and the part should have no primary_manufacturer_part.
        create_some_fake_part_classes(self.organization)

        if self.organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
            csv_no_manufacturer_col = (
                "part_class,description,revision,manufacturer_part_number\n"
                "500,Test Widget,1,MPN-001\n"
            )
            csv_empty_manufacturer_val = (
                "part_class,description,revision,manufacturer,manufacturer_part_number\n"
                "500,Test Widget 2,1,,MPN-002\n"
            )
        else:
            csv_no_manufacturer_col = (
                "part_number,description,revision,manufacturer_part_number\n"
                "W001,Test Widget,1,MPN-001\n"
            )
            csv_empty_manufacturer_val = (
                "part_number,description,revision,manufacturer,manufacturer_part_number\n"
                "W002,Test Widget 2,1,,MPN-002\n"
            )

        for label, csv_content in [
            ("no manufacturer column", csv_no_manufacturer_col),
            ("empty manufacturer value", csv_empty_manufacturer_val),
        ]:
            with self.subTest(label=label):
                f = io.BytesIO(csv_content.encode())
                f.name = 'test.csv'
                response = self.client.post(reverse('bom:upload-parts'), {'file': f})
                self.assertEqual(response.status_code, 302, label)

                blank_manufacturers = Manufacturer.objects.filter(name='')
                self.assertEqual(blank_manufacturers.count(), 0, f"{label}: blank-named Manufacturer was created")

                null_manufacturers = Manufacturer.objects.filter(name__isnull=True)
                self.assertEqual(null_manufacturers.count(), 0, f"{label}: null-named Manufacturer was created")

                created_part = Part.objects.filter(organization=self.organization).last()
                self.assertIsNotNone(created_part, f"{label}: no part was created by the upload")
                self.assertIsNone(
                    created_part.primary_manufacturer_part,
                    f"{label}: part should have no primary_manufacturer_part",
                )

    def test_upload_bom_no_manufacturer_column_does_not_create_blank_manufacturer(self):
        # Bug: uploading a BOM CSV without a manufacturer column (or with an empty value)
        # should NOT create a Manufacturer with a blank/empty name.
        create_some_fake_part_revision_property_definitions(self.organization, some_required=False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # p4 has no assembly/children so it is safe to use as a standalone level-0 row.
        parent_pn = p4.full_part_number()

        csv_no_manufacturer_col = (
            f"part_number,level,quantity,revision,manufacturer_part_number\n"
            f"{parent_pn},0,1,1,MPN-BOM-001\n"
        )
        csv_empty_manufacturer_val = (
            f"part_number,level,quantity,revision,manufacturer_name,manufacturer_part_number\n"
            f"{parent_pn},0,1,1,,MPN-BOM-002\n"
        )

        for label, csv_content in [
            ("no manufacturer column", csv_no_manufacturer_col),
            ("empty manufacturer value", csv_empty_manufacturer_val),
        ]:
            with self.subTest(label=label):
                f = io.BytesIO(csv_content.encode())
                f.name = 'test.csv'
                response = self.client.post(reverse('bom:upload-bom'), {'file': f})
                self.assertEqual(response.status_code, 200, label)

                messages_list = list(response.context['messages']) if response.context and 'messages' in response.context else []
                error_messages = [m for m in messages_list if m.tags == 'error']
                self.assertEqual(len(error_messages), 0, f"{label}: upload had errors: {[str(m) for m in error_messages]}")

                blank_manufacturers = Manufacturer.objects.filter(name='')
                self.assertEqual(blank_manufacturers.count(), 0, f"{label}: blank-named Manufacturer was created")

                null_manufacturers = Manufacturer.objects.filter(name__isnull=True)
                self.assertEqual(null_manufacturers.count(), 0, f"{label}: null-named Manufacturer was created")

    def test_upload_bom_no_part_class_column_does_not_overwrite_class_name(self):
        # Bug: uploading a BOM CSV that has no `part_class` / `class` column (or has an
        # empty value) should NOT overwrite the existing PartClass name with a blank string.
        # PartClassForm is called with name=None which Django converts to '' and saves.
        if self.organization.number_scheme != constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
            self.skipTest('PartClass names only exist in semi-intelligent scheme')

        create_some_fake_part_revision_property_definitions(self.organization, some_required=False)
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        # p2 is 500-0001-00; its part class (code=500) has name='Wendy'.
        pc = PartClass.objects.get(code='500', organization=self.organization)
        original_name = pc.name
        self.assertEqual(original_name, 'Wendy')

        part_pn = p2.full_part_number()

        csv_no_class_col = (
            f"part_number,level,quantity,revision\n"
            f"{part_pn},0,1,1\n"
        )
        csv_empty_class_val = (
            f"part_number,level,quantity,revision,part_class\n"
            f"{part_pn},0,1,1,\n"
        )

        for label, csv_content in [
            ("no part_class column", csv_no_class_col),
            ("empty part_class value", csv_empty_class_val),
        ]:
            with self.subTest(label=label):
                # Restore the name before each subtest in case a previous run overwrote it
                pc.name = original_name
                pc.save()

                f = io.BytesIO(csv_content.encode())
                f.name = 'test.csv'
                response = self.client.post(reverse('bom:upload-bom'), {'file': f})
                self.assertEqual(response.status_code, 200, label)

                pc.refresh_from_db()
                self.assertEqual(
                    pc.name, original_name,
                    f"{label}: PartClass name was overwritten; got '{pc.name}'"
                )

    def test_upload_part_classes(self):
        # Should pass
        with open(f'{TEST_FILES_DIR}/test_part_classes.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        new_part_class_count = PartClass.objects.all().count()
        self.assertEqual(new_part_class_count, 37)

        # Should not hit 500 errors on anything below
        # Submit with no file
        response = self.client.post(reverse('bom:settings'), {'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        # Submit with blank header and comments
        with open(f'{TEST_FILES_DIR}/test_part_classes_no_comment.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue('Row 3: Part class 102 Resistor already defined.' in str(response.content))

        # Submit with a weird csv file that sort of works
        with open(f'{TEST_FILES_DIR}/test_part_classes_blank_rows.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue('Row 3: Missing code.' in str(response.content))
        self.assertTrue('Row 4: Missing code.' in str(response.content))

        # Submit with a csv file exported with a byte order mask, typically from MS word I think
        with open(f'{TEST_FILES_DIR}/test_part_classes_byte_order.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''}, follow=True)
        self.assertEqual(response.status_code, 200)
        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertTrue('None on row' not in str(msg.message))

    def test_upload_part_classes_sample(self):
        # Should pass
        with open(f'{TEST_FILES_DIR}/sample_part_classes.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        new_part_class_count = PartClass.objects.all().count()
        self.assertEqual(new_part_class_count, 37)

    def test_upload_part_classes_parts_and_boms(self):
        self.organization.number_item_len = 5
        self.organization.save()

        # Upload part classes
        with open(f'{TEST_FILES_DIR}/test_part_classes_4.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        new_part_class_count = PartClass.objects.all().count()
        self.assertEqual(new_part_class_count, 39)

        # Upload parts
        with open(f'{TEST_FILES_DIR}/test_new_parts_4.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv}, follow=True)
        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, 'info')

        self.assertEqual(response.status_code, 200)
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 88)
        for p in Part.objects.all():
            self.assertIsNotNone(p.latest())

        pcba_class = PartClass.objects.filter(code=652).first()
        pcba = Part.objects.filter(number_class=pcba_class, number_item='00003', number_variation='0A').first()

        with open(f'{TEST_FILES_DIR}/test_bom_652-00003-0A.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': pcba.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))

        for msg in messages:
            self.assertNotEqual(msg.tags, "error")
            self.assertEqual(msg.tags, "info")

        subparts = pcba.latest().assembly.subparts.all().order_by('id')
        self.assertEqual(subparts[0].reference, 'C1')
        self.assertEqual(subparts[1].reference, 'C2, C21')
        self.assertEqual(subparts[2].reference, 'C23')
        pcba = Part.objects.filter(number_class=pcba_class, number_item='00004', number_variation='0A').first()

        with open(f'{TEST_FILES_DIR}/test_bom_652-00004-0A.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': pcba.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for idx, msg in enumerate(messages):
            self.assertNotEqual(msg.tags, "error")
            self.assertEqual(msg.tags, "info")

        # Check that that rows that have a part number already used but which denote a distinct designator are
        # consolidated into one subpart with one part number but multiple designators and matching quantity counts.
        subparts = pcba.latest().assembly.subparts.all().order_by('id')
        self.assertEqual(subparts[0].reference, 'C1, C2')
        self.assertEqual(subparts[0].count, 2)
        self.assertEqual(subparts[1].reference, 'C3, C4, C5, C6, C11')
        self.assertEqual(subparts[1].count, 5)
        self.assertEqual(subparts[2].reference, 'C7, C8, C9, C10, C14, C18, C22, C33')
        self.assertEqual(subparts[2].count, 8)
        self.assertEqual(subparts[16].reference, 'Y1')
        self.assertEqual(subparts[16].count, 1)

    def test_edit_user_meta(self):
        response = self.client.post(reverse('bom:user-meta-edit', kwargs={'user_meta_id': self.user.bom_profile().id}))
        self.assertEqual(response.status_code, 200)

    def test_add_seller_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:manufacturer-part-add-sellerpart', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:manufacturer-part-add-sellerpart', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        new_sellerpart_form_data = {
            'seller': p1.optimal_seller().seller.id,
            'seller_part_number': p1.optimal_seller().seller_part_number,
            'minimum_order_quantity': 1000,
            'minimum_pack_quantity': 500,
            'unit_cost_0': '1.23',
            'unit_cost_1': 'USD',
            'lead_time_days': 25,
            'nre_cost_0': 2000,
            'nre_cost_1': 'USD',
            'ncnr': False,
        }

        response = self.client.post(reverse('bom:manufacturer-part-add-sellerpart',
                                            kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}),
                                    new_sellerpart_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

    def test_sellerpart_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        edit_sellerpart_form_data = {
            'seller': 'indabom',
            'seller_part_number': '123-45678',
            'minimum_order_quantity': 100,
            'minimum_pack_quantity': 200,
            'unit_cost_0': '1.2',
            'unit_cost_1': 'USD',
            'lead_time_days': 5,
            'nre_cost_0': 1000,
            'nre_cost_1': 'USD',
            'ncnr': True,
        }

        response = self.client.post(reverse('bom:seller-part-edit', kwargs={'seller_part_id': p1.optimal_seller().id}),
                                    edit_sellerpart_form_data)
        self.assertEqual(response.status_code, 302)

    def test_seller_part_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:seller-part-delete', kwargs={'seller_part_id': p1.optimal_seller().id}))

        self.assertEqual(response.status_code, 302)

    def test_add_manufacturer_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        # Test GET
        response = self.client.get(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': p1.id}))

        # Test POSTs
        mfg_form_data = {'manufacturer': p1.primary_manufacturer_part.manufacturer.name,
                         'manufacturer_part_number': p1.primary_manufacturer_part.manufacturer_part_number,
                         'approval_status': 'P'}
        response = self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': p2.id}), mfg_form_data)
        self.assertEqual(response.status_code, 302)

        mfg_form_data = {'manufacturer': "A new mfg name",
                         'manufacturer_part_number': "a new pn"}
        response = self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': p1.id}), mfg_form_data)
        self.assertEqual(response.status_code, 302)

    def test_manufacturers(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:manufacturers'))
        self.assertEqual(response.status_code, 200)

    def test_manufacturer_info(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:manufacturer-info', kwargs={'manufacturer_id': p1.primary_manufacturer_part.manufacturer.id}))
        self.assertEqual(response.status_code, 200)

    def test_manufacturer_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        manufacturer_data = {'name': 'kasper', 'approval_status': 'P'}
        response = self.client.post(
            reverse('bom:manufacturer-edit', kwargs={'manufacturer_id': p1.primary_manufacturer_part.manufacturer.id}),
            manufacturer_data)
        self.assertEqual(response.status_code, 302)

    def test_manufacturer_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:manufacturer-delete', kwargs={'manufacturer_id': p1.primary_manufacturer_part.manufacturer.id}))
        self.assertEqual(response.status_code, 302)

    def test_sellers(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:sellers'))
        self.assertEqual(response.status_code, 200)

    def test_seller_info(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:seller-info', kwargs={'seller_id': p2.primary_manufacturer_part.optimal_seller().seller_id}))
        self.assertEqual(response.status_code, 200)

    def test_seller_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:seller-edit', kwargs={'seller_id': p2.primary_manufacturer_part.optimal_seller().seller_id}),
            {'name': 'Mousah', 'approval_status': 'P'})
        self.assertEqual(response.status_code, 302)

    def test_seller_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:seller-delete', kwargs={'seller_id': p2.primary_manufacturer_part.optimal_seller().seller_id}))
        self.assertEqual(response.status_code, 302)

    def test_manufacturer_part_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.get(
            reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'name': '',
        }

        response = self.client.post(reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}), data)
        self.assertEqual(response.status_code, 302)

        data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'name': 'A new manufacturer',
        }

        old_id = p1.primary_manufacturer_part.manufacturer.id
        response = self.client.post(reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}), data)
        self.assertEqual(response.status_code, 302)
        p1.refresh_from_db()
        self.assertNotEqual(p1.primary_manufacturer_part.manufacturer.id, old_id)

        data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': '',
            'name': '',
        }

        response = self.client.post(
            reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}),
            data)
        self.assertEqual(response.status_code, 200)  # 200 means it failed validation

    def test_manufacturer_part_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:manufacturer-part-delete', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))

        self.assertEqual(response.status_code, 302)

    def test_part_revision_release(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(
            reverse('bom:part-revision-release', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse('bom:part-revision-release', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 302)

    def test_part_revision_draft(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.get(
            reverse('bom:part-revision-draft', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 302)

    def test_part_revision_new(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:part-revision-new', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        # Create new part revision from part without an existing part revision
        response = self.client.get(reverse('bom:part-revision-new', kwargs={'part_id': p4.id}))
        self.assertEqual(response.status_code, 200)

        new_part_revision_form_data = {
            'description': 'new rev',
            'revision': '4',
            'attribute': 'resistance',
            'value': '10k',
            'part': p1.id,
            'configuration': 'D',
            'copy_assembly': 'False',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(
            reverse('bom:part-revision-new', kwargs={'part_id': p1.id}), new_part_revision_form_data)

        self.assertEqual(response.status_code, 302)

        # Create new part revision, copy over the assembly, increment revision, then make sure the old revision
        # didn't change
        new_part_revision_form_data = {
            'description': 'new rev',
            'revision': '5',
            'part': p3.id,
            'configuration': 'D',
            'copy_assembly': 'true',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(
            reverse('bom:part-revision-new', kwargs={'part_id': p3.id}), new_part_revision_form_data)

        revs = p3.revisions().order_by('-id')
        latest = revs[0]
        previous = revs[1]
        previous_subpart_ids = previous.assembly.subparts.all().values_list('id', flat=True)
        new_subpart_ids = latest.assembly.subparts.all().values_list('id', flat=True)

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual([], new_subpart_ids)
        for nsid in new_subpart_ids:
            self.assertNotIn(nsid, previous_subpart_ids)

    def test_part_revision_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.get(
            reverse('bom:part-revision-edit', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 200)

        edit_part_revision_form_data = {
            'description': 'new rev',
            'revision': '4',
            'attribute': 'resistance',
            'value': '10k',
            'part': p1.id,
            'property_sheen': 'Flat',
            'property_voltage': '0',
        }

        response = self.client.post(
            reverse('bom:part-revision-edit', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}),
            edit_part_revision_form_data)

        self.assertEqual(response.status_code, 302)

    def test_part_revision_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:part-revision-delete', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 302)

@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestBOMIntelligent(TestBOM):
    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()
        self.profile = self.user.bom_profile(organization=self.organization)
        self.organization.number_scheme = constants.NUMBER_SCHEME_INTELLIGENT
        self.organization.save()
        self.client.login(username='kasper', password='ghostpassword')

    def test_create_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': new_part_mpn,
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_item': 'ABC1',
            'configuration': 'D',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        try:
            created_part_id = response.url[6:-1]
            created_part = Part.objects.get(id=created_part_id)
        except IndexError:
            self.assertFalse(True, "Part maybe not created? Url looks like: {}".format(response.url))

        self.assertEqual(created_part.latest().description, 'IC, MCU 32 Bit')
        self.assertEqual(created_part.manufacturer_parts().first().manufacturer_part_number, new_part_mpn)

        new_part_form_data = {
            'manufacturer_part_number': 'STM32F401',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_item': '9999',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_item': '5432',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_item': '1234A',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_item': '1235',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        # fail nicely
        new_part_form_data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': '',
            'number_item': p1.number_item,
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 200)

        # Make sure only one part shows up
        response = self.client.post(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)
        decoded_content = response.content.decode('utf-8')
        main_content = decoded_content[decoded_content.find('<main>')+len('<main>'):decoded_content.rfind('</main>')]
        occurances = [m.start() for m in finditer(p1.full_part_number(), main_content)]
        self.assertEqual(len(occurances), 1)

    @skip('Not applicable')
    def test_create_part_variation(self):
        pass

    def test_create_part_no_manufacturer_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_item': '2000',
            'configuration': 'D',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'property_sheen': 'flat',
            'property_voltage': '1.34',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        part = Part.objects.get(number_item='2000')
        self.assertEqual(len(part.manufacturer_parts()), 0)

    def test_part_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:part-edit', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        edit_part_form_data = {
            'number_item': 'HEYA',
        }

        response = self.client.post(reverse('bom:part-edit', kwargs={'part_id': p1.id}), edit_part_form_data)
        self.assertEqual(response.status_code, 302)

    def test_part_upload_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        p5, _ = Part.objects.get_or_create(number_item='500-5555-00', organization=self.organization)
        assy = create_a_fake_assembly()
        pr5 = create_a_fake_part_revision(part=p5, assembly=assy)

        p6, _ = Part.objects.get_or_create(number_item='200-3333-00', organization=self.organization)
        assy = create_a_fake_assembly()
        pr6 = create_a_fake_part_revision(part=p5, assembly=assy)

        with open(f'{TEST_FILES_DIR}/test_bom.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p2.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertNotEqual(msg.tags, "error", msg.message)

        subparts = p2.latest().assembly.subparts.all()

        self.assertEqual(subparts[0].part_revision.part.full_part_number(), '3333')
        self.assertEqual(subparts[0].count, 4)
        self.assertEqual(subparts[1].part_revision.part.full_part_number(), '500-5555-00')
        self.assertEqual(subparts[1].reference, 'U3, IC2, IC3')
        self.assertEqual(subparts[1].count, 3)
        self.assertEqual(subparts[1].do_not_load, False)
        self.assertEqual(subparts[2].part_revision.part.full_part_number(), '500-5555-00')
        self.assertEqual(subparts[2].reference, 'R1, R2')
        self.assertEqual(subparts[2].count, 2)
        self.assertEqual(subparts[2].do_not_load, True)

    def test_upload_parts(self):
        create_some_fake_part_classes(self.organization)

        # part_count = Part.objects.all().count()
        # Should pass
        with open(f'{TEST_FILES_DIR}/test_new_parts_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 4)

        # Part should be skipped because it already exists
        with open(f'{TEST_FILES_DIR}/test_new_parts_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        found_error = False
        for m in response.wsgi_request._messages:
            if "already exists" in str(m):
                found_error = True
        self.assertTrue(found_error)

        # Only one part should exist
        self.assertEqual(Part.objects.filter(number_item='C0402X5R10V001').count(), 1)

        # Uploading this BOM should work, and multiple parts should not be created
        p = Part.objects.first()
        with open(f'{TEST_FILES_DIR}/test_bom_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertTrue("This should not happen." not in msg.message, msg=msg.message)

    def test_upload_part_with_sellers(self):
        # Should pass
        initial_parts_count = Part.objects.all().count()
        with open('bom/test_files/test_new_parts_sellers_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)

        parts_count = Part.objects.all().count()
        self.assertEqual(parts_count - initial_parts_count, 4)

    @skip('not applicable')
    def test_upload_part_classes(self):
        pass

    @skip('not applicable')
    def test_part_upload_bom_corner_cases(self):
        pass

    def test_upload_part_classes_parts_and_boms(self):
        # TODO: Make this more robust
        self.organization.number_item_len = 5
        self.organization.save()

        with open(f'{TEST_FILES_DIR}/test_new_parts_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv}, follow=True)
        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, 'info')

        self.assertEqual(response.status_code, 200)
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 4)

        pcba = Part.objects.get(number_item='DYSON-123')

        with open(f'{TEST_FILES_DIR}/test_bom_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': pcba.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))

        for msg in messages:
            self.assertNotEqual(msg.tags, "error")
            self.assertEqual(msg.tags, "info")

        subparts = pcba.latest().assembly.subparts.all().order_by('id')
        self.assertEqual(subparts[0].reference, 'C1, C2, C3')
        self.assertEqual(subparts[1].reference, 'C4, C5')
        self.assertEqual(subparts[2].reference, '')

        pt1, pt2, pt3, pt4 = create_some_fake_parts(self.organization)
        with open(f'{TEST_FILES_DIR}/test_bom_5_intelligent_no_reference.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': pt1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)
        subparts = pt1.latest().assembly.subparts.all().order_by('id')
        self.assertNotEqual(subparts[0].count, 0)
        self.assertNotEqual(subparts[1].count, 0)
        self.assertNotEqual(subparts[2].count, 0)

@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestBOMNoVariation(TestBOM):
    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()
        self.profile = self.user.bom_profile(organization=self.organization)
        self.organization.number_variation_len = 0
        self.organization.save()
        self.client.login(username='kasper', password='ghostpassword')

    @skip('not applicable')
    def test_create_part_variation(self):
        pass

    @skip('too specific of a test case for now...')
    def test_upload_part_classes_parts_and_boms(self):
        pass

    @skip('not applicable')
    def test_part_upload_bom_corner_cases(self):
        pass

    @skip('too specific of a test case for now...')
    def test_part_upload_bom_with_properties(self):
        pass

    @skip('too specific of a test case for now...')
    def test_upload_parts_break_too_many_characters(self):
        pass

    @skip('too specific of a test case for now...')
    def test_part_upload_bom(self):
        pass


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestForms(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.organization = create_a_fake_organization(self.user)
        self.profile = self.user.bom_profile(organization=self.organization)

    def test_part_info_form(self):
        form_data = {'quantity': 10}
        form = PartInfoForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_part_info_form_blank(self):
        form = PartInfoForm({})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'quantity': [u'This field is required.'],
        })

    def test_part_form(self):
        (pc1, pc2, pc3) = create_some_fake_part_classes(self.organization)
        form_data = {
            'number_class': str(pc1),
            'description': "ASSY, ATLAS WRISTBAND 10",
            'revision': 'AA'
        }

        form = PartFormSemiIntelligent(data=form_data, organization=self.organization)
        self.assertTrue(form.is_valid())

        (m1, m2, m3) = create_some_fake_manufacturers(self.organization)

        form_data = {
            'number_class': str(pc2),
            'description': "ASSY, ATLAS WRISTBAND 5",
            'revision': '1',
        }

        form = PartFormSemiIntelligent(data=form_data, organization=self.organization)
        self.assertTrue(form.is_valid())

        new_part, created = Part.objects.get_or_create(
            number_class=form.cleaned_data['number_class'],
            number_item=form.cleaned_data['number_item'],
            number_variation=form.cleaned_data['number_variation'],
            organization=self.organization)

        self.assertTrue(created)
        self.assertEqual(new_part.number_class.id, pc2.id)

    def test_part_form_blank(self):
        (pc1, pc2, pc3) = create_some_fake_part_classes(self.organization)

        form = PartFormSemiIntelligent(data={}, organization=self.organization)

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'number_class': [u'This field is required.'],
        })

    def test_add_subpart_form(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        form_data = {'subpart_part_number': p1.full_part_number(), 'count': 10, 'reference': '', 'do_not_load': False}
        form = AddSubpartForm(organization=self.organization, data=form_data, part_revision_id=p2.latest().id)
        self.assertTrue(form.is_valid())

    def test_add_subpart_form_blank(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        form = AddSubpartForm({}, organization=self.organization, part_revision_id=p1.latest().id)
        self.assertFalse(form.is_valid())
        self.assertTrue('subpart_part_number' in str(form.errors))
        self.assertTrue('This field is required.' in str(form.errors))

    def test_add_seller_part_form(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        form = SellerPartForm()
        self.assertFalse(form.is_valid())

        seller = Seller.objects.filter(organization=self.organization)[0]

        form_data = {
            'seller': seller.id,
            'seller_part_number': '123-45678',
            'minimum_order_quantity': 1000,
            'minimum_pack_quantity': 100,
            'unit_cost_0': 1.2332,
            'unit_cost_1': 'USD',
            'lead_time_days': 14,
            'nre_cost_0': 1000,
            'nre_cost_1': 'USD',
            'ncnr': True,
        }

        filled_form = SellerPartForm(form_data, organization=self.organization)
        self.assertTrue(filled_form.is_valid(), filled_form.errors)

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        sp = p1.optimal_seller()
        sp.unit_cost = 10
        sp.nre_cost = 22
        sp.save()

        filled_form = SellerPartForm(instance=sp, organization=self.organization)
        self.assertFalse("$10.0" in filled_form.as_ul())
        self.assertFalse("$22.0" in filled_form.as_ul())

@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestJsonViews(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.organization = create_a_fake_organization(self.user)
        self.profile = self.user.bom_profile(organization=self.organization)
        self.client.login(username='kasper', password='ghostpassword')

    def test_sourcing_match_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        self.assertGreaterEqual(len(p3.latest().assembly.subparts.all()), 1)
        response = self.client.get(reverse('json:sourcing-match-bom', kwargs={'part_revision_id': p3.latest().id}))

        self.assertEqual(response.status_code, 200)

    def test_sourcing_match_bom_honors_org_provider(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        self.organization.sourcing_provider = 'nexar'
        self.organization.save()

        with patch('bom.third_party_apis.sourcing.NexarProvider.match', return_value={}) as mock_match:
            response = self.client.get(reverse('json:sourcing-match-bom', kwargs={'part_revision_id': p3.latest().id}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_match.called)

    def _fake_match(self):
        from djmoney.money import Money

        from bom.third_party_apis.sourcing import Offer, PriceBreak

        def fake_match(manufacturer_parts, currency=None):
            currency = currency or 'USD'
            return {
                mp.id: [Offer(
                    seller_name='Mouser',
                    seller_part_number='SPN-1',
                    manufacturer_name=mp.manufacturer.name if mp.manufacturer else '',
                    price_breaks=[
                        PriceBreak(moq=1, unit_cost=Money('5.00', currency)),
                        PriceBreak(moq=100, unit_cost=Money('3.00', currency)),
                    ],
                    product_url='https://example.com/p',
                    data_sheet='https://example.com/ds.pdf',
                    stock=42,
                    lead_time_days=14,
                )]
                for mp in manufacturer_parts
            }
        return fake_match

    def test_sourcing_match_bom_attribution_and_reference_breaks(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        PartClass.objects.filter(organization=self.organization).update(sourcing_enabled=True)

        with patch('bom.views.json_views.build_provider') as mock_build:
            mock_build.return_value.match.side_effect = self._fake_match()
            response = self.client.get(reverse('json:sourcing-match-bom', kwargs={'part_revision_id': p3.latest().id}))

        self.assertEqual(response.status_code, 200)
        content = response.json()['content']
        self.assertIn('provider', content)
        self.assertIn('fetched_at', content)
        self.assertEqual(content['currency'], str(self.organization.currency))

        sourced = [part for part in content['flat_bom']['parts'].values() if part.get('api_info')]
        self.assertGreaterEqual(len(sourced), 1)
        line = sourced[0]
        api_info = line['api_info']
        self.assertEqual(api_info['provider'], content['provider'])
        # Internal key is 'nexar' but the user-facing label is 'Octopart'.
        self.assertEqual(content['provider_label'], 'Octopart')
        self.assertEqual(api_info['provider_label'], 'Octopart')
        self.assertEqual(api_info['product_detail_url'], 'https://example.com/p')
        # price_breaks is shipped for reference display only (server still does the roll-up).
        self.assertEqual([(pb['moq'], pb['unit_cost']) for pb in api_info['price_breaks']], [(1, 5.0), (100, 3.0)])
        # Every distributor offer is surfaced (for the Sourcing tab's all-vendors list).
        self.assertEqual(len(api_info['offers']), 1)
        self.assertEqual(api_info['offers'][0]['seller'], 'Mouser')
        self.assertEqual([(pb['moq'], pb['unit_cost']) for pb in api_info['offers'][0]['price_breaks']], [(1, 5.0), (100, 3.0)])
        # Exact/near-match flag is surfaced for the UI indicator.
        self.assertIn('is_exact', api_info)
        # Quantity/cost columns are exposed as clean numerics for the BOM table.
        self.assertIn('total_extended_quantity', line)
        self.assertIn('order_quantity', line)
        self.assertIn('order_cost', line)

    def test_sourcing_match_bom_surfaces_all_vendors(self):
        # Nexar aggregates multiple distributors; every offer must reach the client so the Sourcing
        # tab can list all vendors (not just the primary one).
        from djmoney.money import Money

        from bom.third_party_apis.sourcing import Offer, PriceBreak

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        PartClass.objects.filter(organization=self.organization).update(sourcing_enabled=True)

        def multi_vendor_match(manufacturer_parts, currency=None):
            return {mp.id: [
                Offer(seller_name='Digi-Key', seller_part_number='DK', manufacturer_name='',
                      price_breaks=[PriceBreak(moq=1, unit_cost=Money('5.00', 'USD'))]),
                Offer(seller_name='Mouser', seller_part_number='MO', manufacturer_name='',
                      price_breaks=[PriceBreak(moq=1, unit_cost=Money('4.00', 'USD'))]),
            ] for mp in manufacturer_parts}

        with patch('bom.views.json_views.build_provider') as mock_build:
            mock_build.return_value.match.side_effect = multi_vendor_match
            response = self.client.get(reverse('json:sourcing-match-bom', kwargs={'part_revision_id': p3.latest().id}))

        content = response.json()['content']
        sourced = [part for part in content['flat_bom']['parts'].values() if part.get('api_info')]
        self.assertGreaterEqual(len(sourced), 1)
        sellers = [offer['seller'] for offer in sourced[0]['api_info']['offers']]
        self.assertEqual(sellers, ['Digi-Key', 'Mouser'])

    def test_sourcing_match_bom_surfaces_unpriced_match(self):
        # A matched-but-unpriced part (obsolete / region-restricted) keeps its api_info -- with the
        # reason and no seller_part -- so the UI can explain the lack of price.
        from bom.third_party_apis.sourcing import Offer

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        PartClass.objects.filter(organization=self.organization).update(sourcing_enabled=True)
        from bom.models import SellerPart
        SellerPart.objects.filter(seller__organization=self.organization).delete()  # no stored price either

        def unpriced_match(manufacturer_parts, currency=None):
            return {mp.id: [Offer(seller_name='Mouser', seller_part_number='M', manufacturer_name='',
                                  price_breaks=[], product_url='https://mouser.com/p',
                                  unavailable_reason='Obsolete. Not sold in your region.')]
                    for mp in manufacturer_parts}

        with patch('bom.views.json_views.build_provider') as mock_build:
            mock_build.return_value.match.side_effect = unpriced_match
            response = self.client.get(reverse('json:sourcing-match-bom', kwargs={'part_revision_id': p3.latest().id}))

        content = response.json()['content']
        sourced = [part for part in content['flat_bom']['parts'].values() if part.get('api_info')]
        self.assertGreaterEqual(len(sourced), 1)
        line = sourced[0]
        self.assertEqual(line['api_info']['price_breaks'], [])
        self.assertIn('Obsolete', line['api_info']['unavailable_reason'])
        self.assertIsNone(line['seller_part'])  # nothing purchasable -> no optimal seller part

    def test_sourcing_match_bom_quantity_param_drives_pricing(self):
        # The browser re-requests with ?quantity=; the server must price for that quantity
        # (MOQ-aware) instead of the cached page quantity.
        from bom.models import SellerPart

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        PartClass.objects.filter(organization=self.organization).update(sourcing_enabled=True)
        # Drop stored seller parts so only the live offer breaks (moq1@5 / moq100@3) are candidates,
        # making the optimal selection deterministic per quantity.
        SellerPart.objects.filter(seller__organization=self.organization).delete()

        def sourced_unit_costs(flat_bom):
            return [float(part['seller_part']['unit_cost'])
                    for part in flat_bom['parts'].values()
                    if part.get('api_info') and part.get('seller_part')]

        url = reverse('json:sourcing-match-bom', kwargs={'part_revision_id': p3.latest().id})
        with patch('bom.views.json_views.build_provider') as mock_build:
            mock_build.return_value.match.side_effect = self._fake_match()
            low = self.client.get(url, {'quantity': 1}).json()['content']['flat_bom']
            high = self.client.get(url, {'quantity': 1000}).json()['content']['flat_bom']

        # qty 1 -> the moq=1 @ $5 break; qty 1000 -> the cheaper moq=100 @ $3 break.
        self.assertIn(5.0, sourced_unit_costs(low))
        self.assertIn(3.0, sourced_unit_costs(high))


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestProviderCredentialSchema(TestCase):
    def test_schema_exposes_per_provider_fields(self):
        from bom.third_party_apis.sourcing import provider_credential_schema

        schema = provider_credential_schema()
        # Mouser: single key field; Nexar: client id + secret. Single source of truth for the UI.
        mouser_attrs = [f['attr'] for f in schema['mouser']]
        nexar_attrs = [f['attr'] for f in schema['nexar']]
        self.assertEqual(mouser_attrs, ['sourcing_api_key'])
        self.assertEqual(nexar_attrs, ['sourcing_api_key', 'sourcing_api_secret'])
        self.assertEqual(schema['mouser'][0]['label'], 'API Key')
        self.assertEqual(schema['nexar'][0]['label'], 'Client ID')
        self.assertTrue(schema['mouser'][0]['help_url'])


FERNET_KEY = Fernet.generate_key().decode()


@override_settings(BOM_SOURCING_ENCRYPTION_KEYS=[FERNET_KEY])
class TestEncryptedTextField(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()

    def _reload(self):
        return self.organization.__class__.objects.get(pk=self.organization.pk)

    def test_round_trips_plaintext(self):
        self.organization.sourcing_api_key = 'super-secret-token'
        self.organization.save()
        self.assertEqual(self._reload().sourcing_api_key, 'super-secret-token')

    def test_db_column_holds_ciphertext(self):
        self.organization.sourcing_api_key = 'plaintext-secret'
        self.organization.save()

        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT sourcing_api_key FROM {self.organization._meta.db_table} WHERE id = %s',
                [self.organization.pk],
            )
            stored = cursor.fetchone()[0]

        self.assertNotIn('plaintext-secret', stored)
        decrypted = MultiFernet([Fernet(FERNET_KEY)]).decrypt(stored.encode()).decode()
        self.assertEqual(decrypted, 'plaintext-secret')

    def test_key_rotation_decrypts_old_values(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        with override_settings(BOM_SOURCING_ENCRYPTION_KEYS=[old_key]):
            self.organization.sourcing_api_key = 'rotate-me'
            self.organization.save()
        # New key first (encrypts new writes), old key retained so old rows still decrypt.
        with override_settings(BOM_SOURCING_ENCRYPTION_KEYS=[new_key, old_key]):
            self.assertEqual(self._reload().sourcing_api_key, 'rotate-me')

    def test_null_value_needs_no_key(self):
        with override_settings(BOM_SOURCING_ENCRYPTION_KEYS=None):
            self.organization.sourcing_api_key = None
            self.organization.save()  # must not touch Fernet
            self.assertIsNone(self._reload().sourcing_api_key)

    def test_missing_key_raises(self):
        with override_settings(BOM_SOURCING_ENCRYPTION_KEYS=None):
            self.organization.sourcing_api_key = 'needs-a-key'
            with self.assertRaises(ImproperlyConfigured):
                self.organization.save()


class TestGoogleDriveScope(TestCase):
    """django-bom owns the Drive integration; the Google login itself is the host's concern.
    The integration must distinguish an identity-only Google connection from one that was
    actually granted Drive access."""

    DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.file'

    def setUp(self):
        self.user, self.organization = create_user_and_organization()

    def _connect(self, extra_data):
        from social_django.models import UserSocialAuth
        return UserSocialAuth.objects.create(user=self.user, provider='google-oauth2', uid='kasper@x.com', extra_data=extra_data)

    def test_no_connection_has_no_drive_scope(self):
        from bom.third_party_apis.google_drive import has_drive_scope
        self.assertFalse(has_drive_scope(self.user))

    def test_identity_only_connection_has_no_drive_scope(self):
        from bom.third_party_apis.google_drive import has_drive_scope
        self._connect({'scopes': ['openid', 'email', 'profile']})
        self.assertFalse(has_drive_scope(self.user))

    def test_drive_grant_has_drive_scope(self):
        from bom.third_party_apis.google_drive import has_drive_scope
        self._connect({'scopes': ['email', 'profile', self.DRIVE_SCOPE]})
        self.assertTrue(has_drive_scope(self.user))

    def test_connection_without_tracked_scopes_fails_closed(self):
        # No 'scopes' key (identity-only, or predates scope tracking): assuming Drive was
        # granted would let the user hit a hard 403 at the Drive API, so we fail closed and
        # make them reconnect via the ?drive=1 link.
        from bom.third_party_apis.google_drive import has_drive_scope
        self._connect({'access_token': 'legacy'})
        self.assertFalse(has_drive_scope(self.user))

    def test_store_drive_scope_persists_granted_scopes(self):
        from bom.third_party_apis.google_drive import has_drive_scope, store_drive_scope
        self._connect({'access_token': 'x'})
        backend = type('B', (), {'name': 'google-oauth2'})()
        store_drive_scope(backend, self.user, {'scope': f'email profile {self.DRIVE_SCOPE}'})
        self.assertTrue(has_drive_scope(self.user))

    def test_initialize_parent_skips_without_drive_scope(self):
        from bom.third_party_apis import google_drive
        backend = type('B', (), {'name': 'google-oauth2'})()
        with patch.object(google_drive, 'create_root') as mock_create:
            google_drive.initialize_parent(backend, self.user, {'scope': 'email profile'})
        self.assertFalse(mock_create.called)

    def test_initialize_parent_provisions_with_drive_scope(self):
        from bom.third_party_apis import google_drive
        backend = type('B', (), {'name': 'google-oauth2'})()
        with patch.object(google_drive, 'create_root') as mock_create:
            google_drive.initialize_parent(backend, self.user, {'scope': f'email {self.DRIVE_SCOPE}'})
        self.assertTrue(mock_create.called)

    def _http_error(self, status, details):
        from googleapiclient.errors import HttpError
        resp = type('R', (), {'status': status, 'reason': ''})()
        content = json.dumps({'error': {'message': 'x', 'details': details}}).encode('utf-8')
        return HttpError(resp, content)

    def test_insufficient_scope_detected(self):
        from bom.third_party_apis.google_drive import _is_insufficient_scope
        err = self._http_error(403, [{'message': 'Insufficient Permission', 'reason': 'insufficientPermissions'}])
        self.assertTrue(_is_insufficient_scope(err))

    def test_other_403_not_treated_as_insufficient_scope(self):
        from bom.third_party_apis.google_drive import _is_insufficient_scope
        err = self._http_error(403, [{'message': 'Rate limit', 'reason': 'rateLimitExceeded'}])
        self.assertFalse(_is_insufficient_scope(err))

    def test_404_not_treated_as_insufficient_scope(self):
        from bom.third_party_apis.google_drive import _is_insufficient_scope
        err = self._http_error(404, [{'reason': 'notFound'}])
        self.assertFalse(_is_insufficient_scope(err))


@override_settings(BOM_SOURCING_ENCRYPTION_KEYS=[FERNET_KEY])
class TestSourcingSettings(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()  # owner -> admin role
        self.client.login(username='kasper', password='ghostpassword')

    def _reload(self):
        return self.organization.__class__.objects.get(pk=self.organization.pk)

    def _post(self, data):
        return self.client.post(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}), data)

    def test_settings_page_renders(self):
        response = self.client.get(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Live Sourcing')
        self.assertNotContains(response, 'id="integrations-nav"')
        # The provider <select> must carry an id (materializecss omits it) so the JS toggle
        # that shows/hides the secret field per provider can target it.
        self.assertContains(response, 'id="id_sourcing_provider"')

    def test_admin_can_set_provider_and_key(self):
        self._post({'submit-edit-sourcing': '', 'sourcing_provider': 'mouser', 'sourcing_api_key': 'my-key', 'sourcing_api_secret': ''})
        org = self._reload()
        self.assertEqual(org.sourcing_provider, 'mouser')
        self.assertEqual(org.sourcing_api_key, 'my-key')

    def test_blank_key_keeps_existing(self):
        self.organization.sourcing_api_key = 'keep-me'
        self.organization.save()
        self._post({'submit-edit-sourcing': '', 'sourcing_provider': 'nexar', 'sourcing_api_key': '', 'sourcing_api_secret': 'client-secret'})
        org = self._reload()
        self.assertEqual(org.sourcing_api_key, 'keep-me')       # unchanged on blank submit
        self.assertEqual(org.sourcing_api_secret, 'client-secret')
        self.assertEqual(org.sourcing_provider, 'nexar')

    def test_secret_never_rendered(self):
        self.organization.sourcing_api_key = 'top-secret-value'
        self.organization.save()
        response = self.client.get(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))
        self.assertNotContains(response, 'top-secret-value')

    def test_status_shows_connected_and_hides_form_when_configured(self):
        self.organization.sourcing_provider = 'mouser'
        self.organization.sourcing_api_key = 'my-key'
        self.organization.save()
        response = self.client.get(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))
        self.assertContains(response, 'Connected to')
        self.assertContains(response, 'Disconnect Mouser')
        self.assertContains(response, 'Test connection')
        # Form is hidden while connected.
        self.assertNotContains(response, 'name="submit-edit-sourcing"')

    def test_form_shown_when_not_connected(self):
        self.organization.sourcing_provider = 'mouser'
        self.organization.save()
        response = self.client.get(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))
        self.assertContains(response, 'name="submit-edit-sourcing"')
        self.assertNotContains(response, 'Disconnect')

    def test_nexar_needs_both_key_and_secret_to_be_connected(self):
        self.organization.sourcing_provider = 'nexar'
        self.organization.sourcing_api_key = 'client-id-only'
        self.organization.save()
        response = self.client.get(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))
        # Only the client id is set, so Nexar is not connected -- the form is still shown.
        self.assertContains(response, 'name="submit-edit-sourcing"')
        self.assertNotContains(response, 'Disconnect')

    def test_test_connection_success(self):
        self.organization.sourcing_provider = 'mouser'
        self.organization.sourcing_api_key = 'my-key'
        self.organization.save()
        with patch('bom.views.views.build_provider') as mock_build:
            mock_build.return_value.match.return_value = {}
            response = self._post({'submit-test-sourcing': '1'})
        self.assertTrue(mock_build.return_value.match.called)
        self.assertContains(response, 'connection OK')

    def test_test_connection_surfaces_provider_error(self):
        from bom.third_party_apis.base_api import BaseApiError

        self.organization.sourcing_provider = 'mouser'
        self.organization.sourcing_api_key = 'my-key'
        self.organization.save()
        with patch('bom.views.views.build_provider') as mock_build:
            mock_build.return_value.match.side_effect = BaseApiError("Nexar denied access ... Supply role/plan")
            response = self._post({'submit-test-sourcing': '1'})
        self.assertContains(response, 'connection failed')
        self.assertContains(response, 'Supply role/plan')

    def test_test_connection_requires_admin(self):
        self.organization.sourcing_provider = 'mouser'
        self.organization.sourcing_api_key = 'my-key'
        self.organization.save()
        viewer = User.objects.create_user('viewer2', 'v2@x.com', 'pw')
        viewer.bom_profile(organization=self.organization)
        self.client.logout()
        self.client.login(username='viewer2', password='pw')
        with patch('bom.views.views.build_provider') as mock_build:
            self._post({'submit-test-sourcing': '1'})
        self.assertFalse(mock_build.called)

    def test_clear_credentials(self):
        self.organization.sourcing_api_key = 'my-key'
        self.organization.sourcing_api_secret = 'my-secret'
        self.organization.save()
        self._post({'submit-clear-sourcing': '1'})
        org = self._reload()
        self.assertFalse(org.sourcing_api_key)
        self.assertFalse(org.sourcing_api_secret)

    def test_non_admin_cannot_edit(self):
        viewer = User.objects.create_user('viewer', 'v@x.com', 'pw')
        viewer.bom_profile(organization=self.organization)  # default role -> not admin
        self.client.logout()
        self.client.login(username='viewer', password='pw')
        self.client.post(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}),
                         {'submit-edit-sourcing': '', 'sourcing_provider': 'mouser', 'sourcing_api_key': 'sneaky', 'sourcing_api_secret': ''})
        self.assertNotEqual(self._reload().sourcing_api_key, 'sneaky')


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestImmutableRevisioning(TransactionTestCase):
    """Test immutable revisioning feature for Released PartRevisions."""

    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()
        self.profile = self.user.bom_profile(organization=self.organization)
        self.profile.role = 'A'
        self.profile.save()

        self.p1, self.p2, self.p3, self.p4 = create_some_fake_parts(organization=self.organization)
        self.pr1 = self.p1.latest()

        self.prop_def, _, _, _, _ = create_some_fake_part_revision_property_definitions(self.organization)

    def test_released_parts_are_locked(self):
        """Confirm Released parts reject changes to attributes, properties, and BOMs."""
        from django.core.exceptions import ValidationError
        from .models import PartRevisionProperty

        # 1. Setup a Draft part with data
        self.pr1.configuration = constants.CONFIGURATION_TYPE_DRAFT
        self.pr1.save()
        prop = PartRevisionProperty.objects.create(part_revision=self.pr1, property_definition=self.prop_def,
                                                   value_raw="10")
        subpart = self.pr1.assembly.subparts.first()  # Created by fake_parts

        # 2. Lock it (Release)
        self.pr1.configuration = constants.CONFIGURATION_TYPE_RELEASED
        self.pr1.save()

        # 3. Assert Attributes are Locked
        self.pr1.description = "Hacker was here"
        with self.assertRaisesRegex(ValidationError, "Cannot modify Released PartRevision"):
            self.pr1.save()

        # 4. Assert Properties are Locked (Edit/Add/Delete)
        prop.value_raw = "20"
        with self.assertRaisesRegex(ValidationError, "Cannot modify properties"):
            prop.save()

        with self.assertRaisesRegex(ValidationError, "Cannot modify properties"):
            PartRevisionProperty.objects.create(part_revision=self.pr1, property_definition=self.prop_def,
                                                value_raw="99")

        # 5. Assert BOM is Locked
        if subpart:
            subpart.count = 999
            with self.assertRaisesRegex(ValidationError, "Cannot modify subparts"):
                subpart.save()

    def test_state_transitions(self):
        """Test valid and invalid lifecycle movements."""
        from django.core.exceptions import ValidationError

        self.pr1.configuration = constants.CONFIGURATION_TYPE_RELEASED
        self.pr1.save()

        # Fail: Released -> Draft (Without Admin user passed)
        self.pr1.configuration = constants.CONFIGURATION_TYPE_DRAFT
        with self.assertRaisesRegex(ValidationError, "Only Admins"):
            self.pr1.save()

        # Pass: Released -> Draft (WITH Admin user passed)
        self.pr1.configuration = constants.CONFIGURATION_TYPE_DRAFT
        self.pr1.save(user=self.user)
        self.assertEqual(self.pr1.configuration, constants.CONFIGURATION_TYPE_DRAFT)

    def test_obsolete_constraints(self):
        """Test unique logic for Obsolete parts (cant be added to new BOMs)."""
        from django.core.exceptions import ValidationError

        self.pr1.configuration = constants.CONFIGURATION_TYPE_OBSOLETE
        self.pr1.save()

        # Try to use this obsolete part in a NEW BOM
        new_subpart = Subpart(part_revision=self.pr1, count=1)
        with self.assertRaisesRegex(ValidationError, "Cannot add Obsolete PartRevision"):
            new_subpart.save()

    def test_immutable_helper(self):
        """Verify is_immutable returns correct boolean for all states."""
        # DRAFT = False
        self.pr1.configuration = constants.CONFIGURATION_TYPE_DRAFT
        self.assertFalse(self.pr1.is_immutable())

        # IN_REVIEW = True (Fixed from AI's version)
        self.pr1.configuration = constants.CONFIGURATION_TYPE_IN_REVIEW
        self.assertTrue(self.pr1.is_immutable())

        # RELEASED = True
        self.pr1.configuration = constants.CONFIGURATION_TYPE_RELEASED
        self.assertTrue(self.pr1.is_immutable())
