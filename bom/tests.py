from django.test import TestCase, Client, TransactionTestCase
from django.urls import reverse
from django.contrib.auth.models import User
from unittest import skip

from re import finditer, search

from .helpers import create_some_fake_parts, create_a_fake_organization, create_a_fake_part_revision, create_user_and_organization, \
    create_a_fake_subpart, create_some_fake_part_classes, create_some_fake_manufacturers, create_some_fake_sellers, create_a_fake_assembly
from .models import Part, SellerPart, ManufacturerPart, Seller, PartClass, Subpart
from .forms import PartInfoForm, PartFormSemiIntelligent, AddSubpartForm, SellerPartForm

from . import constants


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


class TestBOM(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()
        self.profile = self.user.bom_profile(organization=self.organization)
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

        response = self.client.post(reverse('bom:home'), {'download': ''}, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_part_upload_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        test_file = 'test_bom.csv' if self.organization.number_variation_len > 0 else 'test_bom_6_no_variations.csv'
        with open(f'bom/test_files/{test_file}') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p2.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "error")  # Error loading 200-3333-00 via CSV because already in parent's BOM and has empty ref designators

        subparts = p2.latest().assembly.subparts.all()

        expected_pn = '200-3333-00' if self.organization.number_variation_len > 0 else '200-3333'
        self.assertEqual(subparts[0].part_revision.part.full_part_number(), expected_pn)
        self.assertEqual(subparts[0].count, 4)

        expected_pn = '500-5555-00' if self.organization.number_variation_len > 0 else '500-5555'
        self.assertEqual(subparts[1].part_revision.part.full_part_number(), expected_pn)
        self.assertEqual(subparts[1].reference, 'U3, IC2, IC3')
        self.assertEqual(subparts[1].count, 3)
        self.assertEqual(subparts[1].do_not_load, False)

        self.assertEqual(subparts[2].part_revision.part.full_part_number(), expected_pn)
        self.assertEqual(subparts[2].reference, 'R1, R2')
        self.assertEqual(subparts[2].count, 2)
        self.assertEqual(subparts[2].do_not_load, True)


        with open('bom/test_files/test_bom_2.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "error")
            self.assertTrue("does not exist" in str(msg.message))

    def test_part_upload_bom_corner_cases(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        with open('bom/test_files/test_bom_3_recursion.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "error")
            self.assertTrue("recursion" in str(msg.message))

        with open('bom/test_files/test_bom_4_no_part_rev.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "error")
            self.assertTrue("revision" in str(msg.message))

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

        # Test edit
        part_class_form_data['name'] = 'edited test part name'

        response = self.client.post(reverse('bom:part-class-edit', kwargs={'part_class_id': part_class.id}), part_class_form_data)
        self.assertEqual(response.status_code, 302)

        part_class = PartClass.objects.get(id=part_class.id)
        self.assertEqual(part_class.name, part_class_form_data['name'])

    def test_create_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': new_part_mpn,
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': str(p1.number_class),
            'number_item': '',
            'number_variation': '',
            'configuration': 'W',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'attribute': '',
            'value': ''
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
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        # fail nicely
        new_part_form_data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': '',
            'number_class': str(p1.number_class),
            'number_item': '',
            'number_variation': '',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
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

    def test_create_part_variation(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': new_part_mpn,
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': (p1.number_class),
            'number_item': '2000',
            'number_variation': '01',
            'configuration': 'W',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'attribute': '',
            'value': ''
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
            'configuration': 'W',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'attribute': '',
            'value': ''
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
        with open('bom/test_files/test_new_parts.csv') as test_csv:
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
        with open('bom/test_files/test_new_parts_2.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        found_error = False
        for m in response.wsgi_request._messages:
            if "Part class 216 in row 2" in str(m) and "Uploading of this part skipped." in str(m):
                found_error = True
        self.assertTrue(found_error)

        # Part should be skipped because it already exists
        with open('bom/test_files/test_new_parts_3.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        found_error = False
        for m in response.wsgi_request._messages:
            if "Part already exists for manufacturer part 2 in row GhostBuster2000. Uploading of this part skipped." in str(m):
                found_error = True
        self.assertTrue(found_error)

    def test_upload_parts_break_tolerance(self):
        create_some_fake_part_classes(self.organization)

        # Should break with data error
        with open('bom/test_files/test_new_parts_broken.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv}, follow=True)
        messages = list(response.context.get('messages'))

        self.assertTrue(len(messages) > 0)
        for msg in messages:
            self.assertEqual(msg.tags, 'error')

    def test_upload_part_classes(self):
        # Should pass
        with open('bom/test_files/test_part_classes.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        new_part_class_count = PartClass.objects.all().count()
        self.assertEqual(new_part_class_count, 37)

        # Should not hit 500 errors on anything below
        # Submit with no file
        response = self.client.post(reverse('bom:settings'), {'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        # Submit with blank header and comments
        with open('bom/test_files/test_part_classes_no_comment.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue('Part class 102 Resistor on row 3 is already defined. Uploading of this part class skipped.' in str(response.content))

        # Submit with a weird csv file that sort of works
        with open('bom/test_files/test_part_classes_blank_rows.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)
        self.assertTrue('in row 3 does not have a value. Uploading of this part class skipped.' in str(response.content))
        self.assertTrue('in row 4 does not have a value. Uploading of this part class skipped.' in str(response.content))

        # Submit with a csv file exported with a byte order mask, typically from MS word I think
        with open('bom/test_files/test_part_classes_byte_order.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''}, follow=True)
        self.assertEqual(response.status_code, 200)
        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertTrue('None on row' not in str(msg.message))

    def test_upload_part_classes_parts_and_boms(self):
        self.organization.number_item_len = 5
        self.organization.save()

        with open('bom/test_files/test_part_classes_4.csv') as test_csv:
            response = self.client.post(reverse('bom:settings'), {'file': test_csv, 'submit-part-class-upload': ''})
        self.assertEqual(response.status_code, 200)

        new_part_class_count = PartClass.objects.all().count()
        self.assertEqual(new_part_class_count, 39)

        with open('bom/test_files/test_new_parts_4.csv') as test_csv:
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

        with open('bom/test_files/test_bom_652-00003-0A.csv') as test_csv:
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

        with open('bom/test_files/test_bom_652-00004-0A.csv') as test_csv:
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

    def test_add_sellerpart(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:manufacturer-part-add-sellerpart', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:manufacturer-part-add-sellerpart', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        new_sellerpart_form_data = {
            'seller': p1.optimal_seller().seller.id,
            'minimum_order_quantity': 1000,
            'minimum_pack_quantity': 500,
            'unit_cost': '1.23',
            'lead_time_days': 25,
            'nre_cost': 2000,
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
            'new_seller': 'indabom',
            'minimum_order_quantity': 100,
            'minimum_pack_quantity': 200,
            'unit_cost': '1.2',
            'lead_time_days': 5,
            'nre_cost': 1000,
            'ncnr': True,
        }

        response = self.client.post(reverse('bom:sellerpart-edit', kwargs={'sellerpart_id': p1.optimal_seller().id}),
                                    edit_sellerpart_form_data)

        self.assertEqual(response.status_code, 302)

    def test_sellerpart_delete(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:sellerpart-delete', kwargs={'sellerpart_id': p1.optimal_seller().id}))

        self.assertEqual(response.status_code, 302)

    def test_add_manufacturer_part(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        # Test GET
        response = self.client.get(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': p1.id}))

        # Test POSTs
        mfg_form_data = {'name': p1.primary_manufacturer_part.manufacturer.name,
                         'manufacturer_part_number': p1.primary_manufacturer_part.manufacturer_part_number,
                         'part': p2.id}
        response = self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': p1.id}), mfg_form_data)
        self.assertEqual(response.status_code, 302)

        mfg_form_data = {'name': "A new mfg name",
                         'manufacturer_part_number': "a new pn",
                         'part': p2.id}
        response = self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': p1.id}), mfg_form_data)
        self.assertEqual(response.status_code, 302)

    def test_manufacturer_part_edit(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
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

    def test_part_revision_revert(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.get(
            reverse('bom:part-revision-revert', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

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
            'configuration': 'W',
            'copy_assembly': 'False'
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
            'configuration': 'W',
            'copy_assembly': 'true'
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
            'part': p1.id
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
            'configuration': 'W',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'attribute': '',
            'value': ''
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
            'configuration': 'W',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
            'attribute': '',
            'value': ''
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

        with open('bom/test_files/test_bom.csv') as test_csv:
            response = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': p2.id}), {'file': test_csv}, follow=True)
        self.assertEqual(response.status_code, 200)

        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, "error")  # Error loading 200-3333-00 via CSV because already in parent's BOM and has empty ref designators

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
        with open('bom/test_files/test_new_parts_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 4)

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

        with open('bom/test_files/test_new_parts_5_intelligent.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv}, follow=True)
        messages = list(response.context.get('messages'))
        for msg in messages:
            self.assertEqual(msg.tags, 'info')

        self.assertEqual(response.status_code, 200)
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 4)

        pcba = Part.objects.get(number_item='DYSON-123')

        with open('bom/test_files/test_bom_5_intelligent.csv') as test_csv:
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
        form = AddSubpartForm(organization=self.organization, data=form_data, part_id=p2.id)
        self.assertTrue(form.is_valid())

    def test_add_subpart_form_blank(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        form = AddSubpartForm({}, organization=self.organization, part_id=p1.id)
        self.assertFalse(form.is_valid())
        self.assertTrue('subpart_part_number' in str(form.errors))
        self.assertTrue('This field is required.' in str(form.errors))

    def test_add_sellerpart_form(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        form = SellerPartForm()
        self.assertFalse(form.is_valid())

        seller = Seller.objects.filter(organization=self.organization)[0]

        form_data = {
            'seller': seller.id,
            'minimum_order_quantity': 1000,
            'minimum_pack_quantity': 100,
            'unit_cost': 1.2332,
            'lead_time_days': 14,
            'nre_cost': 1000,
            'ncnr': True,
        }

        form = SellerPartForm(organization=self.organization, data=form_data)
        self.assertTrue(form.is_valid())


class TestJsonViews(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.organization = create_a_fake_organization(self.user)
        self.profile = self.user.bom_profile(organization=self.organization)
        self.client.login(username='kasper', password='ghostpassword')

    def test_mouser_part_match_bom(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        self.assertGreaterEqual(len(p3.latest().assembly.subparts.all()), 1)
        response = self.client.get(reverse('json:mouser-part-match-bom', kwargs={'part_revision_id': p3.latest().id}))

        self.assertEqual(response.status_code, 200)
