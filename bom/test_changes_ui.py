from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_user_and_organization, create_some_fake_parts
from bom.models import (APIToken, Change, ChangeItem, ChangeSettings, ManufacturerPart, Part, PartRevision,
                        PartRevisionPropertyDefinition, SellerPart)
from bom.services import parts as services_parts


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class StagingModeTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.assembly = self.parts[1]
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        PartRevisionPropertyDefinition.objects.update(required=False)
        self.leaf = services_parts.create_part(self.organization, 'Leaf', part_class_code=str(
            self.parts[0].number_class.code)).part

    def start_change(self):
        response = self.client.post(reverse('bom:change-start'), {'title': 'Rework', 'rationale': 'Why'})
        self.change = Change.objects.get(organization=self.organization)
        return response

    def test_start_sets_the_session_and_shows_the_banner_until_leaving(self):
        self.start_change()
        self.assertEqual(self.change.status, Change.STATUS_DRAFT)
        self.assertEqual(self.client.session['active_change'], self.change.id)

        page = self.client.get(reverse('bom:home'))
        self.assertContains(page, f'Editing in Change #{self.change.number}')
        self.assertContains(page, 'Rework')

        self.client.post(reverse('bom:change-leave'))
        self.assertNotIn('active_change', self.client.session)
        self.assertNotContains(self.client.get(reverse('bom:home')), 'Editing in Change')

    def test_create_part_is_staged_instead_of_saved(self):
        self.start_change()
        part_count = Part.objects.count()
        response = self.client.post(reverse('bom:create-part'), {
            'number_class': str(self.parts[0].number_class), 'number_item': '', 'number_variation': '',
            'description': 'Staged cap', 'revision': 'A',
            'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-1',
        }, follow=True)

        self.assertEqual(Part.objects.count(), part_count)
        item = self.change.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_CREATE_PART)
        self.assertEqual(item.payload['description'], 'Staged cap')
        self.assertEqual(item.payload['manufacturer'], 'Acme')
        self.assertContains(response, f'Staged in Change #{self.change.number}')

    def test_add_subpart_is_staged_instead_of_saved(self):
        self.start_change()
        assembly_revision = self.assembly.latest()
        line_count = assembly_revision.assembly.subparts.count() if assembly_revision.assembly else 0
        response = self.client.post(
            reverse('bom:part-add-subpart', kwargs={'part_id': self.assembly.id,
                                                    'part_revision_id': assembly_revision.id}),
            {'subpart_part_number': self.leaf.full_part_number(), 'count': 2, 'reference': 'U8, U9'}, follow=True)

        assembly_revision.refresh_from_db()
        self.assertEqual(assembly_revision.assembly.subparts.count() if assembly_revision.assembly else 0, line_count)
        item = self.change.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_ADD_SUBPART)
        self.assertEqual(item.payload['subpart_part_number'], self.leaf.full_part_number())
        self.assertEqual(item.payload['count'], 2)
        self.assertContains(response, f'Staged in Change #{self.change.number}')

    def test_edit_subpart_and_sourcing_are_staged(self):
        assembly_revision = self.assembly.latest()
        subpart = assembly_revision.assembly.subparts.first()
        self.start_change()
        self.client.post(reverse('bom:part-edit-subpart', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': assembly_revision.id, 'subpart_id': subpart.id}),
            {'part_revision': subpart.part_revision.id, 'count': 1, 'reference': 'R1'})
        subpart.refresh_from_db()
        self.assertNotEqual(subpart.reference, 'R1')

        manufacturer_part_count = ManufacturerPart.objects.count()
        self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': self.assembly.id}),
                         {'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-2'})
        self.assertEqual(ManufacturerPart.objects.count(), manufacturer_part_count)

        manufacturer_part = self.assembly.primary_manufacturer_part
        seller_part_count = SellerPart.objects.count()
        self.client.post(reverse('bom:manufacturer-part-add-sellerpart', kwargs={
            'manufacturer_part_id': manufacturer_part.id}), {
            'seller': 'Digi-Key', 'unit_cost_0': '0.50', 'unit_cost_1': 'USD', 'nre_cost_0': '0', 'nre_cost_1': 'USD',
            'minimum_order_quantity': 1, 'minimum_pack_quantity': 1})
        self.assertEqual(SellerPart.objects.count(), seller_part_count)
        self.assertEqual(
            list(self.change.items.values_list('kind', flat=True)),
            [ChangeItem.KIND_UPDATE_SUBPART, ChangeItem.KIND_ADD_MANUFACTURER_PART, ChangeItem.KIND_ADD_SELLER_PART])

    def test_disabled_review_hides_the_mode_and_saves_normally(self):
        self.start_change()
        config = dict(settings.BOM_CONFIG_DEFAULT, changes_review_enabled=False)
        with override_settings(BOM_CONFIG=config):
            self.assertNotContains(self.client.get(reverse('bom:home')), 'Editing in Change')
            part_count = Part.objects.count()
            self.client.post(reverse('bom:create-part'), {
                'number_class': str(self.parts[0].number_class), 'description': 'Real cap', 'revision': 'A'})
            self.assertEqual(Part.objects.count(), part_count + 1)
        self.assertFalse(self.change.items.exists())

    def test_staged_change_is_applied_from_the_review_page(self):
        self.start_change()
        self.client.post(reverse('bom:create-part'), {
            'number_class': str(self.parts[0].number_class), 'description': 'Staged cap', 'revision': 'A'})
        self.client.post(reverse('bom:change-apply', kwargs={'number': self.change.number}))
        self.assertTrue(PartRevision.objects.filter(description='Staged cap').exists())
        self.assertNotContains(self.client.get(reverse('bom:home')), 'Editing in Change')


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class SettingsTabsTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        self.editor = User.objects.create_user('editor', 'editor@example.com', 'ghostpassword')
        profile = self.editor.bom_profile(organization=self.organization)
        profile.role = constants.ROLE_TYPE_EDITOR
        profile.save()
        self.editor_client = Client()
        self.editor_client.login(username='editor', password='ghostpassword')

    def settings_url(self, tab_anchor):
        return reverse('bom:settings', kwargs={'tab_anchor': tab_anchor})

    def test_creating_a_token_shows_the_plaintext_once_and_lists_it(self):
        response = self.client.post(self.settings_url('api'), {
            'submit-create-token': '', 'name': 'Claude Code', 'level': 'propose', 'expires_at': '2099-01-31'})
        token = APIToken.objects.get(name='Claude Code')
        self.assertEqual(token.scope_list(), ['read', 'write'])
        self.assertEqual(token.expires_at.date().isoformat(), '2099-01-31')
        plaintext = response.context['new_api_token_plaintext']
        self.assertTrue(plaintext.startswith('ibom_'))
        self.assertContains(response, plaintext)
        self.assertContains(response, 'Write: propose')

        again = self.client.get(self.settings_url('api'))
        self.assertNotContains(again, plaintext)
        self.assertContains(again, token.prefix)

    def test_apply_level_token_gets_every_scope(self):
        self.client.post(self.settings_url('api'), {'submit-create-token': '', 'name': 'Bot', 'level': 'apply'})
        self.assertEqual(APIToken.objects.get(name='Bot').scope_list(), ['read', 'write', 'apply'])

    def test_revoke_removes_a_token_from_the_list(self):
        token, _ = APIToken.issue(self.user, self.organization, 'Old')
        self.client.post(self.settings_url('api'), {'submit-revoke-token': '', 'token_id': token.id})
        token.refresh_from_db()
        self.assertFalse(token.is_active())
        self.assertNotContains(self.client.get(self.settings_url('api')), token.prefix)

    def test_non_admins_only_see_and_revoke_their_own_tokens(self):
        admin_token, _ = APIToken.issue(self.user, self.organization, 'Admin token')
        own_token, _ = APIToken.issue(self.editor, self.organization, 'Editor token')

        page = self.editor_client.get(self.settings_url('api'))
        self.assertContains(page, own_token.prefix)
        self.assertNotContains(page, admin_token.prefix)

        self.editor_client.post(self.settings_url('api'), {'submit-revoke-token': '', 'token_id': admin_token.id})
        admin_token.refresh_from_db()
        self.assertTrue(admin_token.is_active())

        admin_page = self.client.get(self.settings_url('api'))
        self.assertContains(admin_page, own_token.prefix)
        self.assertContains(admin_page, admin_token.prefix)
        self.client.post(self.settings_url('api'), {'submit-revoke-token': '', 'token_id': own_token.id})
        own_token.refresh_from_db()
        self.assertFalse(own_token.is_active())

    def test_protections_form_saves_for_admins_only(self):
        page = self.client.get(self.settings_url('organization'))
        self.assertContains(page, 'Engineering Change Order')
        self.assertFalse(ChangeSettings.objects.exists())

        self.client.post(self.settings_url('organization'), {
            'submit-protections': '', 'protect_primary_manufacturer_part': 'on', 'protect_cost_delta': '2.5'})
        change_settings = ChangeSettings.objects.get(organization=self.organization)
        self.assertTrue(change_settings.protect_primary_manufacturer_part)
        self.assertEqual(str(change_settings.protect_cost_delta), '2.5000')

        self.client.post(self.settings_url('organization'), {'submit-protections': ''})
        change_settings.refresh_from_db()
        self.assertFalse(change_settings.protect_primary_manufacturer_part)
        self.assertIsNone(change_settings.protect_cost_delta)

        self.editor_client.post(self.settings_url('organization'), {
            'submit-protections': '', 'protect_primary_manufacturer_part': 'on'})
        change_settings.refresh_from_db()
        self.assertFalse(change_settings.protect_primary_manufacturer_part)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class ImportPreviewComponentTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        PartRevisionPropertyDefinition.objects.update(required=False)

    def test_parts_preview_uses_the_shared_item_list_and_confirms(self):
        part_class_code = self.parts[0].number_class.code
        csv_text = (f'part_class,description,revision,property_sheen,property_voltage\n'
                    f'{part_class_code},Imported cap,1,matte,3.3\n')
        upload = SimpleUploadedFile('parts.csv', csv_text.encode(), content_type='text/csv')
        preview = self.client.post(reverse('bom:import'), {'type': 'parts', 'file': upload})

        self.assertTrue(preview.context['token'], preview.context['errors'])
        self.assertContains(preview, 'Will import')
        self.assertContains(preview, 'Imported cap')
        self.client.post(reverse('bom:import-confirm'), {'token': preview.context['token']})
        self.assertTrue(PartRevision.objects.filter(description='Imported cap').exists())

    def test_bom_preview_lists_lines_through_the_shared_component(self):
        assembly, subpart = self.parts[1], self.parts[0]
        csv_text = f'part_number,quantity,reference\n{subpart.full_part_number()},1,X1\n'
        upload = SimpleUploadedFile('bom.csv', csv_text.encode(), content_type='text/csv')
        preview = self.client.post(reverse('bom:part-upload-bom', kwargs={'part_id': assembly.id}),
                                   {'file': upload, 'mode': 'replace'})

        self.assertTrue(preview.context['token'], preview.context['errors'])
        self.assertContains(preview, 'Update line')
        self.assertContains(preview, 'Will import')
        self.client.post(reverse('bom:import-confirm'), {'token': preview.context['token']})
        line = assembly.latest().assembly.subparts.get(part_revision__part=subpart)
        self.assertEqual(line.reference, 'X1')
