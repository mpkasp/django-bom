from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_user_and_organization, create_some_fake_parts
from bom.models import APIToken, ChangeSettings, PartRevision, PartRevisionPropertyDefinition


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
            'submit-create-token': '', 'name': 'Claude Code', 'level': 'edit', 'expires_at': '2099-01-31'})
        token = APIToken.objects.get(name='Claude Code')
        self.assertEqual(token.scope_list(), ['read', 'write'])
        self.assertEqual(token.expires_at.date().isoformat(), '2099-01-31')
        plaintext = response.context['new_api_token_plaintext']
        self.assertTrue(plaintext.startswith('ibom_'))
        self.assertContains(response, plaintext)
        self.assertContains(response, 'Edit')

        again = self.client.get(self.settings_url('api'))
        self.assertNotContains(again, plaintext)
        self.assertContains(again, token.prefix)

    def test_release_level_token_gets_every_scope(self):
        self.client.post(self.settings_url('api'), {'submit-create-token': '', 'name': 'Bot', 'level': 'release'})
        self.assertEqual(APIToken.objects.get(name='Bot').scope_list(), ['read', 'write', 'release'])

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

    def test_require_release_approval_is_saved_by_admins_and_hidden_without_the_host_flag(self):
        page = self.client.get(self.settings_url('organization'))
        self.assertContains(page, 'Require approval to release')

        self.client.post(self.settings_url('organization'), {
            'submit-protections': '', 'require_release_approval': 'on'})
        self.assertTrue(ChangeSettings.objects.get(organization=self.organization).require_release_approval)

        self.editor_client.post(self.settings_url('organization'), {'submit-protections': ''})
        self.assertTrue(ChangeSettings.objects.get(organization=self.organization).require_release_approval)

        with override_settings(BOM_CONFIG=dict(settings.BOM_CONFIG_DEFAULT, change_orders_enabled=False)):
            self.assertNotContains(self.client.get(self.settings_url('organization')), 'Require approval to release')
            self.client.post(self.settings_url('organization'), {'submit-protections': ''})
        self.assertTrue(ChangeSettings.objects.get(organization=self.organization).require_release_approval)


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
