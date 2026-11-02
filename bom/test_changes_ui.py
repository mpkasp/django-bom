from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bom import constants
from bom.helpers import create_user_and_organization, create_some_fake_parts
from bom.models import (APIToken, Change, ChangeItem, ChangeSettings, ManufacturerPart, Part, PartRevision,
                        PartRevisionPropertyDefinition, SellerPart, Subpart)
from bom.services import parts as services_parts


class DirectEditTestBase(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.assembly = self.parts[1]
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        PartRevisionPropertyDefinition.objects.update(required=False)
        part_class_code = str(self.parts[0].number_class.code)
        self.leaf = services_parts.create_part(self.organization, 'Leaf', part_class_code=part_class_code).part
        self.other_leaf = services_parts.create_part(self.organization, 'Other leaf', part_class_code=part_class_code).part

    def add_subpart(self, part, subpart, **extra):
        revision = part.latest()
        return self.client.post(
            reverse('bom:part-add-subpart', kwargs={'part_id': part.id, 'part_revision_id': revision.id}),
            dict({'subpart_part_number': subpart.full_part_number(), 'count': 2, 'reference': 'U8, U9'}, **extra),
            follow=True)

    def line_count(self, part):
        revision = PartRevision.objects.get(pk=part.latest().pk)
        return revision.assembly.subparts.count() if revision.assembly else 0

    def direct_edit_changes(self):
        return Change.objects.filter(organization=self.organization, is_direct_edit=True)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class DirectEditRecordingTest(DirectEditTestBase):
    def test_add_subpart_on_a_draft_saves_and_records_an_applied_direct_edit(self):
        line_count = self.line_count(self.assembly)
        self.add_subpart(self.assembly, self.leaf)

        self.assertEqual(self.line_count(self.assembly), line_count + 1)
        change = self.direct_edit_changes().get()
        revision = self.assembly.latest()
        self.assertEqual(change.status, Change.STATUS_APPLIED)
        self.assertEqual(change.title, f'Edits to {self.assembly.full_part_number()} rev {revision.revision}')
        self.assertEqual(change.base_part_revision, revision)
        item = change.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_ADD_SUBPART)
        self.assertEqual(item.status, ChangeItem.STATUS_ACCEPTED)
        self.assertFalse(change.is_eco)

    def test_second_edit_within_an_hour_appends_and_a_later_one_starts_a_new_change(self):
        self.add_subpart(self.assembly, self.leaf)
        self.add_subpart(self.assembly, self.other_leaf)
        change = self.direct_edit_changes().get()
        self.assertEqual(change.items.count(), 2)

        Change.objects.filter(pk=change.pk).update(created_at=timezone.now() - timedelta(minutes=61))
        self.add_subpart(self.assembly, self.leaf)
        self.assertEqual(self.direct_edit_changes().count(), 2)

    def test_remove_subpart_on_a_draft_saves_and_records(self):
        revision = self.assembly.latest()
        subpart = revision.assembly.subparts.first()
        self.client.get(reverse('bom:part-remove-subpart', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': revision.id, 'subpart_id': subpart.id}))

        self.assertFalse(Subpart.objects.filter(pk=subpart.pk).exists())
        item = self.direct_edit_changes().get().items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_REMOVE_SUBPART)
        self.assertEqual(item.before['count'], subpart.count)

    def test_remove_all_records_one_item_per_line(self):
        revision = self.assembly.latest()
        lines = revision.assembly.subparts.count()
        self.client.get(reverse('bom:part-remove-all-subparts', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': revision.id}))

        self.assertEqual(self.line_count(self.assembly), 0)
        self.assertEqual(self.direct_edit_changes().get().items.filter(kind=ChangeItem.KIND_REMOVE_SUBPART).count(),
                         lines)

    def test_edit_subpart_and_revision_edit_record_what_they_changed(self):
        revision = self.assembly.latest()
        subpart = revision.assembly.subparts.first()
        self.client.post(reverse('bom:part-edit-subpart', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': revision.id, 'subpart_id': subpart.id}),
            {'part_revision': subpart.part_revision.id, 'count': 1, 'reference': 'R1'})
        self.client.post(reverse('bom:part-revision-edit', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': revision.id}),
            {'revision': revision.revision, 'description': 'Reworded'})

        revision.refresh_from_db()
        self.assertEqual(revision.description, 'Reworded')
        kinds = list(self.direct_edit_changes().get().items.values_list('kind', flat=True))
        self.assertEqual(kinds, [ChangeItem.KIND_UPDATE_SUBPART, ChangeItem.KIND_UPDATE_PART_REVISION])

    def test_sourcing_edits_are_recorded(self):
        manufacturer_part = self.assembly.primary_manufacturer_part
        self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': self.assembly.id}),
                         {'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-2'})
        self.client.post(reverse('bom:manufacturer-part-add-sellerpart', kwargs={
            'manufacturer_part_id': manufacturer_part.id}), {
            'seller': 'Digi-Key', 'unit_cost_0': '0.50', 'unit_cost_1': 'USD', 'nre_cost_0': '0', 'nre_cost_1': 'USD',
            'minimum_order_quantity': 1, 'minimum_pack_quantity': 1})
        seller_part = SellerPart.objects.filter(manufacturer_part=manufacturer_part, seller__name='Digi-Key').latest('id')
        self.client.get(reverse('bom:seller-part-delete', kwargs={'seller_part_id': seller_part.id}))
        added = ManufacturerPart.objects.get(manufacturer_part_number='ACME-2')
        self.client.get(reverse('bom:manufacturer-part-delete', kwargs={'manufacturer_part_id': added.id}))

        self.assertEqual(
            list(self.direct_edit_changes().get().items.values_list('kind', flat=True)),
            [ChangeItem.KIND_ADD_MANUFACTURER_PART, ChangeItem.KIND_ADD_SELLER_PART,
             ChangeItem.KIND_DELETE_SELLER_PART, ChangeItem.KIND_DELETE_MANUFACTURER_PART])

    def test_creating_a_part_and_a_revision_is_recorded(self):
        self.client.post(reverse('bom:create-part'), {
            'number_class': str(self.parts[0].number_class), 'description': 'Real cap', 'revision': 'A'})
        created = PartRevision.objects.get(description='Real cap')
        self.assertEqual(self.direct_edit_changes().get().items.get().kind, ChangeItem.KIND_CREATE_PART)

        self.client.post(reverse('bom:part-revision-new', kwargs={'part_id': created.part_id}), {
            'revision': 'B', 'description': 'Next', 'copy_assembly': 'on'})
        self.assertTrue(
            ChangeItem.objects.filter(kind=ChangeItem.KIND_CREATE_PART_REVISION, change__is_direct_edit=True).exists())

    def test_a_failure_to_record_never_breaks_the_edit(self):
        line_count = self.line_count(self.assembly)
        with mock.patch('bom.services.changes._record_direct_edit', side_effect=RuntimeError('boom')):
            with self.assertLogs('bom.services.changes', level='ERROR'):
                self.add_subpart(self.assembly, self.leaf)
        self.assertEqual(self.line_count(self.assembly), line_count + 1)
        self.assertFalse(self.direct_edit_changes().exists())

    def test_direct_edits_stay_out_of_requests_and_the_badge_and_show_under_applied(self):
        self.add_subpart(self.assembly, self.leaf)
        requests_page = self.client.get(reverse('bom:change-list'))
        self.assertEqual(requests_page.context['open_change_count'], 0)
        self.assertEqual(requests_page.context['rows'], [])

        applied_page = self.client.get(reverse('bom:change-list') + '?filter=applied')
        self.assertContains(applied_page, 'Direct edit')
        self.assertEqual(self.client.get(reverse('bom:change-list') + '?filter=ecos').context['rows'], [])

        part_page = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.assembly.id}))
        self.assertContains(part_page, 'Edits to')

    def test_nothing_is_recorded_when_changes_are_disabled(self):
        with override_settings(BOM_CONFIG=dict(settings.BOM_CONFIG_DEFAULT, changes_review_enabled=False)):
            line_count = self.line_count(self.assembly)
            self.add_subpart(self.assembly, self.leaf)
        self.assertEqual(self.line_count(self.assembly), line_count + 1)
        self.assertFalse(Change.objects.exists())

    def test_a_direct_edit_change_cannot_be_reverted(self):
        self.add_subpart(self.assembly, self.leaf)
        change = self.direct_edit_changes().get()
        self.assertContains(self.client.get(reverse('bom:change-review', kwargs={'number': change.number})),
                            'Direct edit')
        self.client.post(reverse('bom:change-revert', kwargs={'number': change.number}))
        self.assertEqual(Change.objects.count(), 1)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class StartEcoTest(DirectEditTestBase):
    def setUp(self):
        super().setUp()
        self.editor = User.objects.create_user('editor', 'editor@example.com', 'ghostpassword')
        profile = self.editor.bom_profile(organization=self.organization)
        profile.role = constants.ROLE_TYPE_EDITOR
        profile.save()
        self.editor_client = Client()
        self.editor_client.login(username='editor', password='ghostpassword')
        self.released = self.assembly.latest()
        PartRevision.objects.filter(pk=self.released.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        self.released.refresh_from_db()
        self.manage_url = reverse('bom:part-manage-bom', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id})

    def start_eco(self):
        response = self.client.post(reverse('bom:change-start-eco'), {'part_revision_id': self.released.id})
        self.change = Change.objects.get(organization=self.organization, is_direct_edit=False)
        return response

    def released_lines(self):
        return sorted(PartRevision.objects.get(pk=self.released.pk).assembly.subparts.values_list('id', 'count'))

    def test_released_revision_offers_the_eco_not_edit_bom(self):
        page = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.assembly.id}))
        self.assertContains(page, 'Start a change (ECO)')
        self.assertNotContains(page, self.manage_url)
        self.assertNotContains(page, 'Can\'t edit a released part')

        self.assertEqual(self.editor_client.get(self.manage_url).status_code, 302)

    def test_the_eco_is_hidden_when_a_flag_is_off(self):
        for flag in ('eco_enabled', 'changes_review_enabled'):
            with override_settings(BOM_CONFIG=dict(settings.BOM_CONFIG_DEFAULT, **{flag: False})):
                page = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.assembly.id}))
                self.assertNotContains(page, 'Start a change (ECO)')

    def test_starting_an_eco_opens_manage_bom_and_is_reused(self):
        response = self.start_eco()

        self.assertEqual(self.change.base_part_revision, self.released)
        self.assertEqual(self.change.status, Change.STATUS_DRAFT)
        self.assertEqual(self.change.title, f'Change to {self.assembly.full_part_number()} rev {self.released.revision}')
        self.assertRedirects(response, self.manage_url)
        self.assertEqual(self.client.session['active_change'], self.change.id)

        self.client.post(reverse('bom:change-start-eco'), {'part_revision_id': self.released.id})
        self.assertEqual(Change.objects.filter(is_direct_edit=False).count(), 1)

        page = self.client.get(self.manage_url)
        self.assertContains(page, f'Editing ECO Change #{self.change.number} for {self.assembly.full_part_number()}')
        self.assertContains(page, 'Edits to this part are held for review.')

    def test_leaving_clears_the_banner(self):
        self.start_eco()
        self.client.post(reverse('bom:change-leave'))
        self.assertNotIn('active_change', self.client.session)
        self.assertNotContains(self.client.get(reverse('bom:home')), 'Editing ECO')

    def test_bom_edits_to_the_ecos_part_are_staged_and_leave_the_released_revision_alone(self):
        self.start_eco()
        lines_before = self.released_lines()
        subpart = self.released.assembly.subparts.first()
        self.add_subpart(self.assembly, self.leaf)
        self.client.post(reverse('bom:part-edit-subpart', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id, 'subpart_id': subpart.id}),
            {'part_revision': subpart.part_revision.id, 'count': 7, 'reference': ''})
        self.client.get(reverse('bom:part-remove-subpart', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id, 'subpart_id': subpart.id}))

        self.assertEqual(self.released_lines(), lines_before)
        self.assertEqual(
            list(self.change.items.values_list('kind', flat=True)),
            [ChangeItem.KIND_ADD_SUBPART, ChangeItem.KIND_UPDATE_SUBPART, ChangeItem.KIND_REMOVE_SUBPART])
        self.assertFalse(self.direct_edit_changes().exists())

    def test_remove_all_stages_a_removal_per_line(self):
        self.start_eco()
        lines_before = self.released_lines()
        self.client.get(reverse('bom:part-remove-all-subparts', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id}))

        self.assertEqual(self.released_lines(), lines_before)
        self.assertEqual(self.change.items.filter(kind=ChangeItem.KIND_REMOVE_SUBPART).count(), len(lines_before))

    def test_revision_edit_stages_an_update_part_revision(self):
        self.start_eco()
        edit_url = reverse('bom:part-revision-edit', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id})
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        self.client.post(edit_url, {'revision': self.released.revision, 'description': 'Released rewording'})

        self.released.refresh_from_db()
        self.assertNotEqual(self.released.description, 'Released rewording')
        item = self.change.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_UPDATE_PART_REVISION)
        self.assertEqual(item.payload['description'], 'Released rewording')

    def test_sourcing_edits_to_the_ecos_part_are_staged(self):
        self.start_eco()
        manufacturer_part = self.assembly.primary_manufacturer_part
        seller_part = manufacturer_part.seller_parts().first()
        manufacturer_part_count = ManufacturerPart.objects.count()
        seller_part_count = SellerPart.objects.count()

        self.client.post(reverse('bom:part-add-manufacturer-part', kwargs={'part_id': self.assembly.id}),
                         {'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-2'})
        self.client.post(reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': manufacturer_part.id}),
                         {'manufacturer': 'Acme', 'manufacturer_part_number': 'RENAMED'})
        self.client.get(reverse('bom:manufacturer-part-delete', kwargs={'manufacturer_part_id': manufacturer_part.id}))
        self.client.post(reverse('bom:manufacturer-part-add-sellerpart', kwargs={
            'manufacturer_part_id': manufacturer_part.id}), {
            'seller': 'Digi-Key', 'unit_cost_0': '0.50', 'unit_cost_1': 'USD', 'nre_cost_0': '0', 'nre_cost_1': 'USD',
            'minimum_order_quantity': 1, 'minimum_pack_quantity': 1})
        self.client.post(reverse('bom:seller-part-edit', kwargs={'seller_part_id': seller_part.id}), {
            'seller': seller_part.seller.name, 'unit_cost_0': '0.25', 'unit_cost_1': 'USD', 'nre_cost_0': '0',
            'nre_cost_1': 'USD', 'minimum_order_quantity': 1, 'minimum_pack_quantity': 1})
        self.client.get(reverse('bom:seller-part-delete', kwargs={'seller_part_id': seller_part.id}))

        self.assertEqual(ManufacturerPart.objects.count(), manufacturer_part_count)
        self.assertEqual(SellerPart.objects.count(), seller_part_count)
        self.assertEqual(
            list(self.change.items.values_list('kind', flat=True)),
            [ChangeItem.KIND_ADD_MANUFACTURER_PART, ChangeItem.KIND_UPDATE_MANUFACTURER_PART,
             ChangeItem.KIND_DELETE_MANUFACTURER_PART, ChangeItem.KIND_ADD_SELLER_PART,
             ChangeItem.KIND_UPDATE_SELLER_PART, ChangeItem.KIND_DELETE_SELLER_PART])

    def test_edits_to_another_draft_part_save_directly_during_an_eco(self):
        self.start_eco()
        other = self.parts[2]
        line_count = self.line_count(other)
        self.add_subpart(other, self.leaf)

        self.assertEqual(self.line_count(other), line_count + 1)
        self.assertFalse(self.change.items.exists())
        self.assertEqual(self.direct_edit_changes().get().base_part_revision, other.latest())

    def test_creating_a_part_during_an_eco_stages_it(self):
        self.start_eco()
        part_count = Part.objects.count()
        self.client.post(reverse('bom:create-part'), {
            'number_class': str(self.parts[0].number_class), 'description': 'Staged cap', 'revision': 'A'})
        self.assertEqual(Part.objects.count(), part_count)
        self.assertEqual(self.change.items.get().kind, ChangeItem.KIND_CREATE_PART)

    def test_creating_a_revision_by_hand_is_refused_during_an_eco(self):
        self.start_eco()
        revision_count = PartRevision.objects.count()
        self.client.post(reverse('bom:part-revision-new', kwargs={'part_id': self.assembly.id}),
                         {'revision': '2', 'description': 'By hand'})
        self.assertEqual(PartRevision.objects.count(), revision_count)

    def test_applying_the_eco_creates_one_draft_revision_with_every_staged_edit(self):
        self.start_eco()
        removed = self.released.assembly.subparts.first()
        removed_part = removed.part_revision.part
        self.add_subpart(self.assembly, self.leaf)
        self.client.get(reverse('bom:part-remove-subpart', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id, 'subpart_id': removed.id}))
        self.client.post(reverse('bom:part-revision-edit', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id}),
            {'revision': self.released.revision, 'description': 'After the ECO'})
        lines_before = self.released_lines()

        self.client.post(reverse('bom:change-apply', kwargs={'number': self.change.number}), {'override': 'on'})

        self.change.refresh_from_db()
        self.assertEqual(self.change.status, Change.STATUS_APPLIED)
        self.assertTrue(self.change.is_eco)
        new_revision = self.assembly.latest()
        self.assertNotEqual(new_revision.pk, self.released.pk)
        self.assertEqual(new_revision.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.assertEqual(new_revision.description, 'After the ECO')
        new_parts = {line.part_revision.part_id for line in new_revision.assembly.subparts.all()}
        self.assertIn(self.leaf.id, new_parts)
        self.assertNotIn(removed_part.id, new_parts)
        self.assertEqual(self.released_lines(), lines_before)
        self.assertNotContains(self.client.get(reverse('bom:home')), 'Editing ECO')

    def test_applying_an_eco_that_removes_every_line_empties_the_new_revision(self):
        self.start_eco()
        self.client.get(reverse('bom:part-remove-all-subparts', kwargs={
            'part_id': self.assembly.id, 'part_revision_id': self.released.id}))
        self.client.post(reverse('bom:change-apply', kwargs={'number': self.change.number}), {'override': 'on'})
        self.assertEqual(self.line_count(self.assembly), 0)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class ProtectedPrimaryManufacturerPartTest(DirectEditTestBase):
    def setUp(self):
        super().setUp()
        self.editor = User.objects.create_user('editor', 'editor@example.com', 'ghostpassword')
        profile = self.editor.bom_profile(organization=self.organization)
        profile.role = constants.ROLE_TYPE_EDITOR
        profile.save()
        self.editor_client = Client()
        self.editor_client.login(username='editor', password='ghostpassword')
        ChangeSettings.objects.create(organization=self.organization, protect_primary_manufacturer_part=True)
        self.primary = self.assembly.primary_manufacturer_part
        self.edit_url = reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': self.primary.id})
        self.new_values = {'manufacturer': self.primary.manufacturer.name, 'manufacturer_part_number': 'NEW-MPN'}

    def test_a_non_admin_editing_the_primary_manufacturer_part_sends_a_change_request(self):
        response = self.editor_client.post(self.edit_url, self.new_values, follow=True)

        self.primary.refresh_from_db()
        self.assertNotEqual(self.primary.manufacturer_part_number, 'NEW-MPN')
        change = Change.objects.get(organization=self.organization)
        self.assertEqual(change.status, Change.STATUS_OPEN)
        self.assertFalse(change.is_direct_edit)
        self.assertEqual(change.items.get().kind, ChangeItem.KIND_UPDATE_MANUFACTURER_PART)
        self.assertContains(
            response, f'Changing the primary manufacturer part needs review. Sent as Change #{change.number}.')

        self.client.post(reverse('bom:change-apply', kwargs={'number': change.number}))
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.manufacturer_part_number, 'NEW-MPN')

    def test_a_non_admin_switching_the_primary_in_part_edit_sends_a_change_request(self):
        other = self.assembly.manufacturer_parts().exclude(pk=self.primary.pk).first()
        self.editor_client.post(reverse('bom:part-edit', kwargs={'part_id': self.assembly.id}), {
            'number_class': str(self.assembly.number_class), 'number_item': self.assembly.number_item,
            'number_variation': self.assembly.number_variation, 'primary_manufacturer_part': other.id})

        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.primary_manufacturer_part_id, self.primary.id)
        change = Change.objects.get(organization=self.organization)
        self.assertEqual(change.status, Change.STATUS_OPEN)
        self.assertEqual(change.items.get().kind, ChangeItem.KIND_UPDATE_PART)

        self.client.post(reverse('bom:change-apply', kwargs={'number': change.number}))
        self.assembly.refresh_from_db()
        self.assertEqual(self.assembly.primary_manufacturer_part_id, other.id)

    def test_an_admin_saves_directly_and_it_is_recorded(self):
        self.client.post(self.edit_url, self.new_values)

        self.primary.refresh_from_db()
        self.assertEqual(self.primary.manufacturer_part_number, 'NEW-MPN')
        self.assertEqual(self.direct_edit_changes().get().items.get().kind, ChangeItem.KIND_UPDATE_MANUFACTURER_PART)
        self.assertFalse(Change.objects.filter(status=Change.STATUS_OPEN).exists())

    def test_a_single_member_organization_saves_directly(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.editor_client.post(self.edit_url, self.new_values)
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.manufacturer_part_number, 'NEW-MPN')

    def test_a_non_primary_manufacturer_part_is_not_protected(self):
        other = self.assembly.manufacturer_parts().exclude(pk=self.primary.pk).first()
        self.editor_client.post(reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': other.id}),
                                {'manufacturer': 'Acme', 'manufacturer_part_number': 'OTHER-MPN'})
        other.refresh_from_db()
        self.assertEqual(other.manufacturer_part_number, 'OTHER-MPN')


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
