import importlib
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.models import APIToken, Change, ChangeItem, ManufacturerPart, PartRevision, PartRevisionPropertyDefinition
from bom.services import boms, changes, parts
from bom.services.errors import ServiceError


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class EditingTestCase(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        PartRevisionPropertyDefinition.objects.update(required=False)
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        self.part_class_code = str(self.parts[0].number_class.code)

    def new_part(self, description='Leaf'):
        return parts.create_part(self.organization, description, part_class_code=self.part_class_code)

    def history(self):
        return Change.objects.filter(organization=self.organization, is_direct_edit=True)


class DirectEditHistoryTest(EditingTestCase):
    def assertRecordedOnce(self, action, kind):
        before = self.history().count()
        action()
        self.assertEqual(self.history().count(), before + 1)
        change = self.history().order_by('-id').first()
        self.assertIsNone(change.number)
        self.assertEqual(change.status, Change.STATUS_APPLIED)
        self.assertEqual(change.author, self.user)
        self.assertEqual(change.items.get().kind, kind)

    def test_each_direct_save_records_exactly_one_history_change_without_a_number(self):
        assembly = self.parts[1].latest()
        leaf = self.new_part().part
        line = assembly.assembly.subparts.first()
        kwargs = {'part_id': assembly.part_id, 'part_revision_id': assembly.id}

        self.assertRecordedOnce(lambda: self.client.post(reverse('bom:create-part'), {
            'number_class': self.part_class_code, 'description': 'Fresh cap', 'revision': 'A'}),
            ChangeItem.KIND_CREATE_PART)
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:part-add-subpart', kwargs=kwargs),
            {'subpart_part_number': leaf.full_part_number(), 'count': 1, 'reference': 'U8'}),
            ChangeItem.KIND_ADD_SUBPART)
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:part-edit-subpart', kwargs=dict(kwargs, subpart_id=line.id)),
            {'part_revision': line.part_revision.id, 'count': 1, 'reference': 'R1'}), ChangeItem.KIND_UPDATE_SUBPART)
        self.assertRecordedOnce(lambda: self.client.get(
            reverse('bom:part-remove-subpart', kwargs=dict(kwargs, subpart_id=line.id))), ChangeItem.KIND_REMOVE_SUBPART)
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:part-revision-edit', kwargs=kwargs), {'revision': assembly.revision, 'configuration': 'D',
                                                               'description': 'Renamed'}),
            ChangeItem.KIND_UPDATE_PART_REVISION)
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:part-add-manufacturer-part', kwargs={'part_id': assembly.part_id}),
            {'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-1'}), ChangeItem.KIND_ADD_MANUFACTURER_PART)
        manufacturer_part = ManufacturerPart.objects.get(manufacturer_part_number='ACME-1')
        seller_data = {'seller': 'Digi-Key', 'unit_cost_0': '0.50', 'unit_cost_1': 'USD', 'nre_cost_0': '0',
                       'nre_cost_1': 'USD', 'minimum_order_quantity': 1, 'minimum_pack_quantity': 1}
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:manufacturer-part-add-sellerpart', kwargs={'manufacturer_part_id': manufacturer_part.id}),
            seller_data), ChangeItem.KIND_ADD_SELLER_PART)
        seller_part = manufacturer_part.sellerpart_set.get()
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:seller-part-edit', kwargs={'seller_part_id': seller_part.id}),
            dict(seller_data, unit_cost_0='0.75')), ChangeItem.KIND_UPDATE_SELLER_PART)
        self.assertRecordedOnce(lambda: self.client.get(
            reverse('bom:seller-part-delete', kwargs={'seller_part_id': seller_part.id})),
            ChangeItem.KIND_DELETE_SELLER_PART)
        self.assertRecordedOnce(lambda: self.client.get(
            reverse('bom:manufacturer-part-delete', kwargs={'manufacturer_part_id': manufacturer_part.id})),
            ChangeItem.KIND_DELETE_MANUFACTURER_PART)
        self.assertRecordedOnce(lambda: self.client.post(
            reverse('bom:part-revision-new', kwargs={'part_id': leaf.id}),
            {'revision': '2', 'description': 'Next', 'copy_assembly': 'on', 'configuration': 'D'}),
            ChangeItem.KIND_CREATE_PART_REVISION)

    def test_numbered_change_requests_keep_a_clean_sequence(self):
        first = changes.start_change(self.organization, self.user, 'First request')
        self.client.post(reverse('bom:create-part'), {
            'number_class': self.part_class_code, 'description': 'Fresh cap', 'revision': 'A'})
        second = changes.start_change(self.organization, self.user, 'Second request')

        self.assertEqual((first.number, second.number), (1, 2))
        self.assertEqual(self.history().count(), 1)
        self.assertEqual(self.history().get().number, None)

    def test_a_recording_failure_is_logged_and_does_not_undo_the_save(self):
        with mock.patch('bom.services.changes.ChangeItem.objects.create', side_effect=RuntimeError('boom')):
            with self.assertLogs('bom.services.changes', level='ERROR'):
                self.client.post(reverse('bom:create-part'), {
                    'number_class': self.part_class_code, 'description': 'Fresh cap', 'revision': 'A'})
        self.assertTrue(PartRevision.objects.filter(description='Fresh cap').exists())
        self.assertFalse(self.history().exists())

    def test_direct_edits_stay_out_of_agent_facing_lookups(self):
        self.client.post(reverse('bom:create-part'), {
            'number_class': self.part_class_code, 'description': 'Fresh cap', 'revision': 'A'})
        direct_edit = self.history().get()
        with self.assertRaises(changes.ServiceError):
            changes.change_for(self.organization, direct_edit.id)
        self.assertFalse(changes.is_eco(direct_edit))
        with self.assertRaises(changes.ServiceError):
            changes.revert_change(direct_edit, self.user)


class WithdrawStagedDraftsMigrationTest(EditingTestCase):
    def test_human_drafts_are_withdrawn_and_agent_drafts_stay(self):
        human_draft = changes.start_change(self.organization, self.user, 'Staged by a person')
        agent_draft = changes.start_change(
            self.organization, self.user, 'Staged by an agent',
            api_token=APIToken.issue(self.user, self.organization, 'agent')[0])
        open_request = changes.start_change(self.organization, self.user, 'Already submitted')
        Change.objects.filter(pk=open_request.pk).update(status=Change.STATUS_OPEN)

        importlib.import_module('bom.migrations.0067_change_history_and_withdraw_staged_drafts') \
            .withdraw_staged_drafts(apps, None)

        statuses = {change.title: Change.objects.get(pk=change.pk).status
                    for change in (human_draft, agent_draft, open_request)}
        self.assertEqual(statuses, {'Staged by a person': Change.STATUS_WITHDRAWN,
                                    'Staged by an agent': Change.STATUS_DRAFT,
                                    'Already submitted': Change.STATUS_OPEN})


class ReviseTest(EditingTestCase):
    def released_revision(self):
        revision = self.new_part('Widget')
        boms.add_subpart(self.organization, revision, self.parts[0].full_part_number(), count=1, reference='U1')
        PartRevision.objects.filter(pk=revision.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        revision.refresh_from_db()
        return revision

    def revise(self, revision):
        return self.client.post(reverse('bom:part-revision-revise', kwargs={
            'part_id': revision.part_id, 'part_revision_id': revision.id}))

    def test_revise_creates_the_next_draft_with_the_bom_and_records_history(self):
        released = self.released_revision()

        response = self.revise(released)

        draft = released.part.latest()
        self.assertEqual((draft.revision, draft.configuration), ('2', constants.CONFIGURATION_TYPE_DRAFT))
        self.assertEqual(draft.assembly.subparts.get().part_revision, self.parts[0].latest())
        self.assertRedirects(response, reverse('bom:part-manage-bom', kwargs={
            'part_id': released.part_id, 'part_revision_id': draft.id}))
        entry = self.history().get()
        self.assertIsNone(entry.number)
        self.assertEqual(entry.items.get().kind, ChangeItem.KIND_CREATE_PART_REVISION)
        self.assertEqual(changes.part_history(released.part)[0]['diff_revision_ids'], (released.id, draft.id))

    def test_revise_refuses_when_a_newer_draft_exists_and_links_to_it(self):
        released = self.released_revision()
        self.revise(released)
        draft = released.part.latest()

        response = self.revise(released)

        self.assertEqual(released.part.revisions().count(), 2)
        self.assertEqual(self.history().count(), 1)
        page = self.client.get(response.url)
        self.assertContains(page, 'already has a newer Draft')
        self.assertContains(page, reverse('bom:part-info-history', kwargs={
            'part_id': released.part_id, 'part_revision_id': draft.id}))

    def test_revise_refuses_a_draft_and_a_viewer(self):
        draft = self.new_part('Still a draft')
        with self.assertRaises(ServiceError):
            changes.revise_revision(draft, self.user)

        released = self.released_revision()
        viewer = User.objects.create_user('viewer', 'viewer@example.com', 'ghostpassword')
        profile = viewer.bom_profile(organization=self.organization)
        profile.role = constants.ROLE_TYPE_VIEWER
        profile.save()
        with self.assertRaises(ServiceError):
            changes.revise_revision(released, viewer)
        self.assertEqual(released.part.revisions().count(), 1)

    def test_the_part_page_offers_revise_on_a_released_revision_only(self):
        released = self.released_revision()
        revise_url = reverse('bom:part-revision-revise', kwargs={
            'part_id': released.part_id, 'part_revision_id': released.id})
        self.assertContains(self.client.get(reverse('bom:part-info', kwargs={'part_id': released.part_id})), revise_url)

        self.revise(released)
        draft_page = self.client.get(reverse('bom:part-info', kwargs={'part_id': released.part_id}))
        self.assertNotContains(draft_page, 'Revise</button>')


class ReleaseTest(EditingTestCase):
    def setUp(self):
        super().setUp()
        self.component = self.new_part('Component')
        self.primary = parts.add_manufacturer_part(self.organization, self.component.part, 'Acme', 'ACME-1')
        self.alternate = parts.add_manufacturer_part(self.organization, self.component.part, 'Globex', 'GLX-1')
        self.draft_parent = self.assembly_using(self.component, 'Draft parent')
        self.released_parent = self.assembly_using(self.component, 'Released parent')
        PartRevision.objects.filter(pk__in=(self.component.pk, self.released_parent.pk)).update(
            configuration=constants.CONFIGURATION_TYPE_RELEASED)
        self.next_component = parts.create_next_revision(self.component)
        boms.add_subpart(self.organization, self.next_component, self.parts[0].full_part_number(), count=1,
                         reference='U1')

    def assembly_using(self, component, description):
        assembly = self.new_part(description)
        boms.add_subpart(self.organization, assembly, component.part.full_part_number(), count=1, reference='U1')
        return assembly

    def member(self, role):
        member = User.objects.create_user(f'member-{role}', f'{role}@example.com', 'ghostpassword')
        profile = member.bom_profile(organization=self.organization)
        profile.role = role
        profile.save()
        return member

    def line_target(self, assembly):
        assembly.refresh_from_db()
        return assembly.assembly.subparts.get().part_revision

    def test_release_freezes_the_revision_and_moves_draft_parents(self):
        changes.release_revision(self.next_component, self.user, note='Ready for build')

        self.next_component.refresh_from_db()
        self.assertEqual(self.next_component.configuration, constants.CONFIGURATION_TYPE_RELEASED)
        self.assertEqual(self.line_target(self.draft_parent), self.next_component)
        self.assertEqual(self.line_target(self.released_parent), self.component)
        self.assertEqual(self.component.part.revisions().count(), 2)

    def test_release_records_history_with_the_manufacturer_parts_snapshot(self):
        changes.release_revision(self.next_component, self.user, note='Ready for build')

        entry = self.history().get()
        self.assertIsNone(entry.number)
        self.assertEqual(entry.rationale, 'Ready for build')
        item = entry.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_RELEASE_REVISION)
        self.assertEqual(item.result['previous_revision_id'], self.component.id)
        self.assertEqual(item.result['manufacturer_parts'], [
            {'id': self.primary.id, 'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-1',
             'is_primary': True},
            {'id': self.alternate.id, 'manufacturer': 'Globex', 'manufacturer_part_number': 'GLX-1',
             'is_primary': False}])
        self.assertEqual(changes.part_history(self.component.part)[0]['diff_revision_ids'],
                         (self.component.id, self.next_component.id))

    def test_release_cascades_only_the_selected_released_parents(self):
        other_released = self.assembly_using(self.component, 'Other released parent')
        PartRevision.objects.filter(pk=other_released.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)

        changes.release_revision(self.next_component, self.user, cascade_parent_ids=[self.released_parent.id])

        new_parent = self.released_parent.part.latest()
        self.assertEqual((new_parent.revision, new_parent.configuration), ('2', constants.CONFIGURATION_TYPE_DRAFT))
        self.assertEqual(new_parent.assembly.subparts.get().part_revision, self.next_component)
        self.assertEqual(other_released.part.revisions().count(), 1)
        self.assertEqual(self.line_target(self.released_parent), self.component)
        self.assertEqual(self.history().count(), 2)

    def test_a_failed_cascade_rolls_the_whole_release_back(self):
        with mock.patch('bom.services.changes.parts.create_next_revision', side_effect=ServiceError('boom')):
            with self.assertRaises(ServiceError):
                changes.release_revision(self.next_component, self.user, cascade_parent_ids=[self.released_parent.id])

        self.next_component.refresh_from_db()
        self.assertEqual(self.next_component.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.assertEqual(self.line_target(self.draft_parent), self.component)
        self.assertEqual(self.released_parent.part.revisions().count(), 1)
        self.assertFalse(self.history().exists())

    def test_cascade_refuses_assemblies_that_do_not_use_the_previous_revision(self):
        with self.assertRaises(ServiceError):
            changes.release_revision(self.next_component, self.user, cascade_parent_ids=[self.draft_parent.id])
        self.next_component.refresh_from_db()
        self.assertEqual(self.next_component.configuration, constants.CONFIGURATION_TYPE_DRAFT)

    def test_a_viewer_cannot_release(self):
        with self.assertRaises(ServiceError):
            changes.release_revision(self.next_component, self.member(constants.ROLE_TYPE_VIEWER))
        self.next_component.refresh_from_db()
        self.assertEqual(self.next_component.configuration, constants.CONFIGURATION_TYPE_DRAFT)

        self.client.logout()
        self.client.login(username='member-V', password='ghostpassword')
        self.client.post(reverse('bom:part-revision-release', kwargs={
            'part_id': self.component.part_id, 'part_revision_id': self.next_component.id}))
        self.next_component.refresh_from_db()
        self.assertEqual(self.next_component.configuration, constants.CONFIGURATION_TYPE_DRAFT)

    def test_releasing_an_already_released_revision_errors_cleanly(self):
        with self.assertRaisesMessage(ServiceError, 'only a Draft or In Review revision can be released'):
            changes.release_revision(self.component, self.user)
        self.assertFalse(self.history().exists())

        url = reverse('bom:part-revision-release', kwargs={
            'part_id': self.component.part_id, 'part_revision_id': self.component.id})
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_the_dialog_previews_the_diff_cost_and_where_used_and_releases_with_a_cascade(self):
        url = reverse('bom:part-revision-release', kwargs={
            'part_id': self.component.part_id, 'part_revision_id': self.next_component.id})

        page = self.client.get(url)

        self.assertContains(page, 'Compared with rev 1')
        self.assertContains(page, self.parts[0].full_part_number())
        self.assertContains(page, 'Unit cost at 100 units')
        self.assertContains(page, f'{self.draft_parent.part.full_part_number()} rev 1')
        self.assertContains(page, f'name="cascade_parent_id" value="{self.released_parent.id}"')
        self.assertNotContains(page, f'name="cascade_parent_id" value="{self.draft_parent.id}"')

        response = self.client.post(url, {'note': 'Go', 'cascade_parent_id': [self.released_parent.id]})

        self.assertRedirects(response, reverse('bom:part-info-history', kwargs={
            'part_id': self.component.part_id, 'part_revision_id': self.next_component.id}))
        self.next_component.refresh_from_db()
        self.assertEqual(self.next_component.configuration, constants.CONFIGURATION_TYPE_RELEASED)
        self.assertEqual(self.released_parent.part.revisions().count(), 2)
        self.assertEqual(self.history().get(items__kind=ChangeItem.KIND_RELEASE_REVISION).rationale, 'Go')
