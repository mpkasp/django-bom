from django.test import TestCase, Client, TransactionTestCase
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User
from unittest import skip

from .helpers import create_some_fake_parts, create_a_fake_organization, \
    create_a_fake_subpart, create_a_fake_partfile, \
    create_some_fake_part_classes, create_some_fake_manufacturers
from .models import PartFile, Part
from .forms import PartInfoForm, PartForm, AddSubpartForm
from .octopart_parts_match import match_part


class TestBOM(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'kasper', 'kasper@McFadden.com', 'ghostpassword')
        self.organization = create_a_fake_organization(self.user)
        self.profile = self.user.bom_profile(organization=self.organization)

    def test_home(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_error(self):
        response = self.client.post(reverse('error'))
        self.assertEqual(response.status_code, 200)

    def test_part_info(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-info',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

    def test_part_export_bom(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-export-bom',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

    def test_part_upload_bom(self):
        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)
        with open('bom/test_parts.csv') as test_csv:
            response = self.client.post(
                reverse('part-upload-bom', kwargs={'part_id': p1.id}),
                {'file': test_csv})
        self.assertEqual(response.status_code, 302)

    def test_export_part_list(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('export-part-list'))
        self.assertEqual(response.status_code, 200)

    @skip("only test when we want to hit octopart's api")
    def test_match_part(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)
        a = match_part(p1)

        partExists = len(a) > 0

        self.assertEqual(partExists, True)

    @skip("only test when we want to hit octopart's api")
    def test_octopart_match_part_indented(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-octopart-match-bom',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    @skip("only test when we want to hit octopart's api")
    def test_part_octopart_match(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-octopart-match',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    def test_create_part(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(reverse('create-part'))
        self.assertEqual(response.status_code, 200)

    def test_part_edit(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-edit',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 200)

    def test_part_delete(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-delete',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    def test_add_subpart(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-add-subpart',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    def test_remove_subpart(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)
        s1 = create_a_fake_subpart(p1, p3, count=10)

        response = self.client.post(
            reverse(
                'part-remove-subpart',
                kwargs={
                    'part_id': p1.id,
                    'subpart_id': s1.id}))
        self.assertEqual(response.status_code, 302)

    def test_remove_all_subparts(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        response = self.client.post(
            reverse(
                'part-remove-all-subparts',
                kwargs={
                    'part_id': p1.id}))
        self.assertEqual(response.status_code, 302)

    def test_upload_file_to_part_and_delete(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)
        with open('bom/test_parts.csv') as test_csv:
            response = self.client.post(
                reverse('part-upload-partfile', kwargs={'part_id': p1.id}),
                {'file': test_csv})
        self.assertEqual(response.status_code, 302)

        partfiles = PartFile.objects.filter(part=p1)
        for pf in partfiles:
            response = self.client.post(
                reverse(
                    'part-delete-partfile',
                    kwargs={
                        'part_id': p1.id,
                        'partfile_id': pf.id}))
            self.assertEqual(response.status_code, 302)

    def test_delete_file_from_part(self):
        self.client.login(username='kasper', password='ghostpassword')

        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)
        with open('bom/test_parts.csv') as test_csv:
            pf1 = create_a_fake_partfile(test_csv, p1)
            response = self.client.post(
                reverse(
                    'part-delete-partfile',
                    kwargs={
                        'part_id': p1.id,
                        'partfile_id': pf1.id}))
        self.assertEqual(response.status_code, 302)

    def test_upload_parts(self):
        self.client.login(username='kasper', password='ghostpassword')

        with open('bom/test_new_parts.csv') as test_csv:
            response = self.client.post(
                reverse('upload-parts'), {'file': test_csv})
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

        form = PartForm(organization=self.organization, data=form_data)
        self.assertTrue(form.is_valid())

        (m1, m2, m3) = create_some_fake_manufacturers(self.organization)

        form_data = {
            'number_class': pc2.id,
            'description': "ASSY, ATLAS WRISTBAND 5",
            'revision': '1',
            'manufacturer': m1.id,
        }

        form = PartForm(organization=self.organization, data=form_data)
        self.assertTrue(form.is_valid())
        
        new_part, created = Part.objects.get_or_create(
                number_class=form.cleaned_data['number_class'],
                number_item=form.cleaned_data['number_item'],
                number_variation=form.cleaned_data['number_variation'],
                manufacturer_part_number=form.cleaned_data['manufacturer_part_number'],
                manufacturer=form.cleaned_data['manufacturer'],
                organization=self.organization,
                defaults={'description': form.cleaned_data['description'],
                          'revision': form.cleaned_data['revision'],
                          }
            )

        self.assertTrue(created)
        self.assertEqual(new_part.manufacturer, m1)

    def test_part_form_blank(self):
        form = PartForm({}, organization=self.organization)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'number_class': [u'This field is required.'],
            'description': [u'This field is required.'],
            'revision': [u'This field is required.']
        })

    def test_add_subpart_form(self):
        (p1, p2, p3) = create_some_fake_parts(organization=self.organization)

        form_data = {'assembly_subpart': p2.id, 'count': 10}
        form = AddSubpartForm(organization=self.organization, data=form_data, part_id=p1.id)
        self.assertTrue(form.is_valid())

    def test_add_subpart_form_blank(self):
        form = AddSubpartForm({}, organization=self.organization)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {
            'assembly_subpart': [u'This field is required.'],
            'count': [u'This field is required.'],
        })
