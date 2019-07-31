from django.test import TestCase, Client, TransactionTestCase
from django.urls import reverse
from django.contrib.auth.models import User
from unittest import skip

from re import finditer

from .helpers import create_some_fake_parts, create_a_fake_organization, create_a_fake_part_revision, \
    create_a_fake_subpart, create_some_fake_part_classes, create_some_fake_manufacturers, create_some_fake_sellers
from .models import Part, SellerPart, ManufacturerPart, Seller
from .forms import PartInfoForm, PartForm, AddSubpartForm, AddSellerPartForm
from .octopart import match_part


class TestBOM(TransactionTestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.organization = create_a_fake_organization(self.user)
        self.profile = self.user.bom_profile(organization=self.organization)

    def test_home(self):
        self.client.login(username='kasper', password='ghostpassword')

        response = self.client.post(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)

        # Make sure only one part shows up
        occurances = [m.start() for m in finditer(p1.full_part_number(), response.content.decode('utf-8'))]
        self.assertEqual(len(occurances), 1)

        response = self.client.get(reverse('bom:home'), {'q': p1.primary_manufacturer_part.manufacturer_part_number})
        self.assertEqual(response.status_code, 200)

    def test_error(self):
        response = self.client.post(reverse('bom:error'))
        self.assertEqual(response.status_code, 200)

    def test_part_info(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'bom:part-info',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse(
                'bom:part-info',
                kwargs={
                    'part_id': p2.id}))
        self.assertEqual(response.status_code, 200)

    def test_part_manage_bom(self):
        self.client.login(username='kasper', password='ghostpassword')

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
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'bom:part-export-bom',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

    def test_part_upload_bom(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        with open('bom/test_files/test_parts.csv') as test_csv:
            response = self.client.post(
                reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}),
                {'file': test_csv})
        self.assertEqual(response.status_code, 302)

        with open('bom/test_files/test_parts_2.csv') as test_csv:
            response = self.client.post(
                reverse('bom:part-upload-bom', kwargs={'part_id': p1.id}),
                {'file': test_csv})
        self.assertEqual(response.status_code, 302)

    def test_export_part_list(self):
        self.client.login(username='kasper', password='ghostpassword')

        create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:export-part-list'))
        self.assertEqual(response.status_code, 200)

    @skip("only test when we want to hit octopart's api")
    def test_match_part(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        a = match_part(p1.primary_manufacturer_part, self.organization)

        partExists = len(a) > 0

        self.assertEqual(partExists, True)

    @skip("only test when we want to hit octopart's api")
    def test_octopart_match_part_indented(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:part-octopart-match-bom', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('bom:part-octopart-match-bom', kwargs={'part_id': p2.id}))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('bom:part-octopart-match-bom', kwargs={'part_id': p3.id}))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('bom:part-octopart-match-bom', kwargs={'part_id': p4.id}))
        self.assertEqual(response.status_code, 302)

    # @skip("only test when we want to hit octopart's api")s
    def test_part_octopart_match(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'bom:part-octopart-match',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    # @skip("only test when we want to hit octopart's api")
    def test_manufacturer_part_octopart_match(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'bom:manufacturer-part-octopart-match',
                kwargs={
                    'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 302)

    def test_create_part(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        new_part_mpn = 'STM32F401-NEW-PART'
        new_part_form_data = {
            'manufacturer_part_number': new_part_mpn,
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': p1.number_class.id,
            'number_item': '',
            'number_variation': '',
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
        except IndexError:
            self.assertFalse(True, "Part maybe not created? Url looks like: {}".format(response.url))

        created_part = Part.objects.get(id=created_part_id)
        self.assertEqual(created_part.manufacturer_parts().first().manufacturer_part_number, new_part_mpn)

        new_part_form_data = {
            'manufacturer_part_number': 'STM32F401',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'number_class': p1.number_class.id,
            'number_item': '9999',
            'number_variation': '01',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_class': p1.number_class.id,
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
            'number_class': p1.number_class.id,
            'number_item': '1234',
            'number_variation': 'AZ',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        new_part_form_data = {
            'manufacturer_part_number': '',
            'manufacturer': '',
            'number_class': p1.number_class.id,
            'number_item': '1234',
            'number_variation': '',
            'description': 'IC, MCU 32 Bit',
            'revision': 'A',
        }

        response = self.client.post(reverse('bom:create-part'), new_part_form_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue('/part/' in response.url)

        # Make sure only one part shows up
        response = self.client.post(reverse('bom:home'))
        self.assertEqual(response.status_code, 200)

        occurances = [m.start() for m in finditer(p1.full_part_number(), response.content.decode('utf-8'))]
        self.assertEqual(len(occurances), 1)

    def test_part_edit(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:part-edit', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

        edit_part_form_data = {
            'number_class': p1.number_class.id,
            'number_item': '',
            'number_variation': '',
        }

        response = self.client.post(reverse('bom:part-edit', kwargs={'part_id': p1.id}), edit_part_form_data)
        self.assertEqual(response.status_code, 302)

    def test_part_delete(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('bom:part-delete', kwargs={'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    def test_add_subpart(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse('bom:part-add-subpart', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id, }))
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse('bom:part-add-subpart', kwargs={'part_id': p3.id, 'part_revision_id': p3.latest().id, }))
        self.assertEqual(response.status_code, 302)

    def test_remove_subpart(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        s1 = create_a_fake_subpart(p1.latest(), count=10)

        response = self.client.post(
            reverse('bom:part-remove-subpart',
                    kwargs={'part_id': p1.id, 'subpart_id': s1.id, 'part_revision_id': p1.latest().id, }))
        self.assertEqual(response.status_code, 302)

    def test_remove_all_subparts(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse('bom:part-remove-all-subparts', kwargs={'part_id': p3.id, 'part_revision_id': p3.latest().id}))
        self.assertEqual(response.status_code, 302)

    def test_upload_parts(self):
        self.client.login(username='kasper', password='ghostpassword')

        create_some_fake_part_classes()

        # part_count = Part.objects.all().count()
        # Should pass
        with open('bom/test_files/test_new_parts.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 302)
        new_part_count = Part.objects.all().count()
        self.assertEqual(new_part_count, 4)

        # Should fail because revision is 3 characters
        with open('bom/test_files/test_new_parts_2.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 200)

        # Should fail because part class doesnt exist
        with open('bom/test_files/test_new_parts_3.csv') as test_csv:
            response = self.client.post(reverse('bom:upload-parts'), {'file': test_csv})
        self.assertEqual(response.status_code, 200)

    def test_add_sellerpart(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(reverse('bom:manufacturer-part-add-sellerpart',
                                           kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('bom:manufacturer-part-add-sellerpart',
                                            kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
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
        self.client.login(username='kasper', password='ghostpassword')

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
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(reverse('bom:sellerpart-delete', kwargs={'sellerpart_id': p1.optimal_seller().id}))

        self.assertEqual(response.status_code, 302)

    def test_manufacturer_part_edit(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))
        self.assertEqual(response.status_code, 200)

        data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'name': '',
        }

        response = self.client.post(
            reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}),
            data)
        self.assertEqual(response.status_code, 302)

        data = {
            'manufacturer_part_number': 'ABC123',
            'manufacturer': p1.primary_manufacturer_part.manufacturer.id,
            'name': 'A new manufacturer',
        }

        old_id = p1.primary_manufacturer_part.manufacturer.id
        response = self.client.post(
            reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}),
            data)
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
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:manufacturer-part-delete', kwargs={'manufacturer_part_id': p1.primary_manufacturer_part.id}))

        self.assertEqual(response.status_code, 302)

    def test_part_revision_release(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        response = self.client.get(
            reverse('bom:part-revision-release', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse('bom:part-revision-release', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 302)

    def test_part_revision_revert(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.get(
            reverse('bom:part-revision-revert', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 302)

    def test_part_revision_new(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.get(
            reverse('bom:part-revision-new', kwargs={'part_id': p1.id}))

        self.assertEqual(response.status_code, 200)

        new_part_revision_form_data = {
            'description': 'new rev',
            'revision': '4',
            'attribute': 'resistance',
            'value': '10k',
            'part': p1.id
        }

        response = self.client.post(
            reverse('bom:part-revision-new', kwargs={'part_id': p1.id}), new_part_revision_form_data)

        self.assertEqual(response.status_code, 302)

    def test_part_revision_edit(self):
        self.client.login(username='kasper', password='ghostpassword')

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
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        response = self.client.post(
            reverse('bom:part-revision-delete', kwargs={'part_id': p1.id, 'part_revision_id': p1.latest().id}))

        self.assertEqual(response.status_code, 302)


class TestForms(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'kasper', 'kasper@McFadden.com', 'ghostpassword')
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
        (pc1, pc2, pc3) = create_some_fake_part_classes()

        form_data = {
            'number_class': pc1.id,
            'description': "ASSY, ATLAS WRISTBAND 10",
            'revision': 'AA'
        }

        form = PartForm(data=form_data)
        self.assertTrue(form.is_valid())

        (m1, m2, m3) = create_some_fake_manufacturers(self.organization)

        form_data = {
            'number_class': pc2.id,
            'description': "ASSY, ATLAS WRISTBAND 5",
            'revision': '1',
        }

        form = PartForm(data=form_data)
        self.assertTrue(form.is_valid())

        new_part, created = Part.objects.get_or_create(
            number_class=form.cleaned_data['number_class'],
            number_item=form.cleaned_data['number_item'],
            number_variation=form.cleaned_data['number_variation'],
            organization=self.organization)

        self.assertTrue(created)
        self.assertEqual(new_part.number_class.id, pc2.id)

    def test_part_form_blank(self):
        form = PartForm({})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'number_class': [u'This field is required.'],
        })

    def test_add_subpart_form(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)

        form_data = {'subpart_part': p1.id, 'count': 10, 'reference': ''}
        form = AddSubpartForm(organization=self.organization, data=form_data, part_id=p2.id)
        self.assertTrue(form.is_valid())

    def test_add_subpart_form_blank(self):
        form = AddSubpartForm({}, organization=self.organization)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'subpart_part': [u'This field is required.'],
            'count': [u'This field is required.'],
        })

    def test_add_sellerpart_form(self):
        (p1, p2, p3, p4) = create_some_fake_parts(organization=self.organization)
        form = AddSellerPartForm()
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

        form = AddSellerPartForm(organization=self.organization, data=form_data)
        self.assertTrue(form.is_valid())
