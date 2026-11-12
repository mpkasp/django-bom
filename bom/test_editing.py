import importlib
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.models import (APIToken, Change, ChangeItem, ChangeSettings, ManufacturerPart, PartRevision,
                        PartRevisionPropertyDefinition)
from bom.services import boms, change_orders, changes, parts
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
            reverse('bom:part-edit-revision', kwargs=kwargs), {'revision': assembly.revision, 'configuration': 'D',
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


class WithdrawPendingStagedChangesMigrationTest(EditingTestCase):
    def test_pending_staged_changes_are_withdrawn_and_change_orders_stay(self):
        def staged(title, status):
            change = changes.start_change(self.organization, self.user, title)
            ChangeItem.objects.create(change=change, position=1, kind=ChangeItem.KIND_CREATE_PART, summary=title)
            change.status = status
            change.save(update_fields=['status'])
            return change

        pending = [staged('Staged draft', Change.STATUS_DRAFT), staged('Staged open', Change.STATUS_OPEN)]
        kept = [
            staged('Staged applied', Change.STATUS_APPLIED),
            changes.start_change(self.organization, self.user, 'Empty change order'),
            change_orders.create_change_order(self.organization, self.user, 'Change order', 'Because',
                                              [self.new_part()]),
            changes.record_direct_edit(self.organization, self.user, self.parts[0], ChangeItem.KIND_UPDATE_PART,
                                       'Direct edit'),
        ]
        statuses = [change.status for change in kept]

        importlib.import_module('bom.migrations.0073_remove_staged_changes') \
            .withdraw_pending_staged_changes(apps, None)

        for change in pending:
            change.refresh_from_db()
            self.assertEqual(change.status, Change.STATUS_WITHDRAWN)
            self.assertIsNotNone(change.resolved_at)
        self.assertEqual([Change.objects.get(pk=change.pk).status for change in kept], statuses)


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
        self.assertRedirects(response, reverse('bom:part-edit-revision', kwargs={
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

    def test_the_part_page_status_explains_the_draft_and_points_old_revisions_at_it(self):
        released = self.released_revision()
        self.revise(released)

        draft_page = self.client.get(reverse('bom:part-info', kwargs={'part_id': released.part_id}))
        self.assertContains(draft_page, 'Rev 1 stays in use until you release rev 2.')
        self.assertContains(draft_page, 'Compare with rev 1')
        self.assertNotContains(draft_page, 'New revision from this one')

        old_page = self.client.get(reverse('bom:part-info-history', kwargs={
            'part_id': released.part_id, 'part_revision_id': released.id}))
        self.assertContains(old_page, "You're viewing rev 1")
        self.assertContains(old_page, 'Open rev 2')
        self.assertNotContains(old_page, 'Revise</button>')


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


class RequestsNavTest(EditingTestCase):
    nav_link = 'title="IndaBOM | Change orders"'

    def propose(self, title):
        change = change_orders.create_change_order(self.organization, self.user, title, 'Because', [self.new_part()])
        return change_orders.submit(change, self.user)

    def test_the_nav_item_shows_for_an_editor_and_counts_open_change_orders(self):
        home = reverse('bom:home')
        self.client.post(reverse('bom:create-part'), {
            'number_class': self.part_class_code, 'description': 'Fresh cap', 'revision': 'A'})
        changes.start_change(self.organization, self.user, 'Still a draft')
        page = self.client.get(home)
        self.assertContains(page, self.nav_link)
        self.assertNotContains(page, 'new badge')

        self.propose('First request')
        self.propose('Second request')
        page = self.client.get(home)
        self.assertContains(page, self.nav_link)
        self.assertContains(page, 'data-badge-caption="">2<')

    def test_the_nav_item_is_hidden_from_viewers_and_without_the_host_flag(self):
        home = reverse('bom:home')
        with override_settings(BOM_CONFIG=dict(settings.BOM_CONFIG_DEFAULT, change_orders_enabled=False)):
            self.assertNotContains(self.client.get(home), self.nav_link)
        profile = self.user.bom_profile()
        profile.role = constants.ROLE_TYPE_VIEWER
        profile.save()
        self.assertNotContains(self.client.get(home), self.nav_link)

    def test_the_nav_item_shows_on_the_requests_page_even_at_zero(self):
        page = self.client.get(reverse('bom:change-list'))
        self.assertContains(page, self.nav_link)
        self.assertNotContains(page, 'new badge')
        self.assertContains(page, '<h4>Change orders</h4>')

    def test_the_history_filter_lists_direct_edits_and_the_other_filters_do_not(self):
        self.client.post(reverse('bom:create-part'), {
            'number_class': self.part_class_code, 'description': 'Fresh cap', 'revision': 'A'})
        self.propose('A proposal')
        applied = self.propose('An applied request')
        change_orders.approve(applied, self.user)
        url = reverse('bom:change-list')

        history = self.client.get(url + '?filter=history')
        self.assertContains(history, 'Create part')
        self.assertNotContains(history, 'A proposal')
        self.assertNotContains(self.client.get(url), 'Create part')
        self.assertContains(self.client.get(url), 'A proposal')
        released_page = self.client.get(url + '?filter=released')
        self.assertContains(released_page, 'An applied request')
        self.assertNotContains(released_page, 'Fresh cap')
        self.assertNotContains(self.client.get(url + '?filter=ecos'), 'Fresh cap')


class EditPageTest(EditingTestCase):
    def setUp(self):
        super().setUp()
        self.revision = self.new_part('Widget')
        self.part = self.revision.part
        self.primary = parts.add_manufacturer_part(self.organization, self.part, 'Acme', 'ACME-1')
        self.alternate = parts.add_manufacturer_part(self.organization, self.part, 'Globex', 'GLX-1')
        self.edit_url = self.url('part-edit-revision', self.revision)

    def url(self, name, revision):
        return reverse(f'bom:{name}', kwargs={'part_id': revision.part_id, 'part_revision_id': revision.id})

    def release(self, revision):
        PartRevision.objects.filter(pk=revision.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)

    def form_data(self, description):
        return {'revision': self.revision.revision, 'configuration': 'D', 'description': description}

    def test_the_edit_page_shows_the_revision_form_the_bom_and_the_manufacturer_parts(self):
        page = self.client.get(self.edit_url)

        self.assertContains(page, f'Edit {self.part.full_part_number()} rev {self.revision.revision}')
        self.assertContains(page, 'id="part-revision-form"')
        self.assertContains(page, 'Bill of materials')
        self.assertContains(page, 'id="manufacturer-parts"')
        self.assertContains(page, 'GLX-1')
        self.assertContains(page, reverse('bom:manufacturer-part-make-primary', kwargs={
            'manufacturer_part_id': self.alternate.id}))
        self.assertEqual(self.client.get(self.url('part-manage-bom', self.revision)).status_code, 200)

    def test_saving_the_revision_form_records_one_history_entry(self):
        response = self.client.post(self.edit_url, self.form_data('Renamed'))

        self.assertRedirects(response, self.edit_url)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Renamed')
        entry = self.history().get()
        self.assertEqual(entry.items.get().kind, ChangeItem.KIND_UPDATE_PART_REVISION)

    def test_a_released_revision_refuses_edits_and_offers_revise(self):
        self.release(self.revision)

        page = self.client.get(self.edit_url)

        self.assertContains(page, 'cannot be edited')
        self.assertContains(page, self.url('part-revision-revise', self.revision))
        self.assertNotContains(page, 'id="part-revision-form"')

    def test_posting_the_revision_form_after_release_shows_an_error_and_changes_nothing(self):
        self.release(self.revision)

        response = self.client.post(self.edit_url, self.form_data('Too late'), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot be edited')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Widget')
        self.assertEqual(self.history().count(), 0)

    def test_make_primary_sets_the_primary_records_history_and_returns_to_the_edit_page(self):
        response = self.client.post(
            reverse('bom:manufacturer-part-make-primary', kwargs={'manufacturer_part_id': self.alternate.id}),
            {'next': self.edit_url})

        self.assertRedirects(response, self.edit_url)
        self.part.refresh_from_db()
        self.assertEqual(self.part.primary_manufacturer_part, self.alternate)
        item = self.history().get().items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_UPDATE_PART)
        self.assertEqual(item.before, {'primary_manufacturer_part_id': self.primary.id})

    def test_make_primary_ignores_an_external_next_and_needs_post(self):
        url = reverse('bom:manufacturer-part-make-primary', kwargs={'manufacturer_part_id': self.alternate.id})
        self.assertEqual(self.client.get(url).status_code, 405)

        response = self.client.post(url, {'next': 'https://evil.example.com/'})

        self.assertNotIn('evil.example.com', response.url)

    def test_manufacturer_part_add_edit_and_delete_return_to_the_edit_page(self):
        next_query = {'next': self.edit_url}
        added = self.client.post(
            reverse('bom:part-add-manufacturer-part', kwargs={'part_id': self.part.id}) + f'?next={self.edit_url}',
            {'manufacturer': 'Initech', 'manufacturer_part_number': 'INI-1'})
        edited = self.client.post(
            reverse('bom:manufacturer-part-edit', kwargs={'manufacturer_part_id': self.alternate.id})
            + f'?next={self.edit_url}', {'manufacturer': 'Globex', 'manufacturer_part_number': 'GLX-2'})
        deleted = self.client.get(
            reverse('bom:manufacturer-part-delete', kwargs={'manufacturer_part_id': self.alternate.id}), next_query)

        for response in (added, edited, deleted):
            self.assertRedirects(response, self.edit_url)

    def test_part_revision_edit_redirects_to_the_edit_page(self):
        url = self.url('part-revision-edit', self.revision)
        self.assertRedirects(self.client.get(url), self.edit_url)
        self.assertRedirects(self.client.post(url, self.form_data('Ignored')), self.edit_url)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Widget')

    def test_the_part_identity_page_no_longer_has_the_primary_manufacturer_part_field(self):
        page = self.client.get(reverse('bom:part-edit', kwargs={'part_id': self.part.id}))

        self.assertNotContains(page, 'primary_manufacturer_part')

    def test_the_part_page_header_offers_edit_on_a_draft_and_revise_on_a_released_revision(self):
        part_url = reverse('bom:part-info', kwargs={'part_id': self.part.id})

        draft_page = self.client.get(part_url)
        self.assertContains(draft_page, f'href="{self.edit_url}"')
        self.assertContains(draft_page, 'Release&hellip;')
        self.assertNotContains(draft_page, 'Send to review')
        self.assertNotContains(draft_page, self.url('part-revision-revise', self.revision))
        self.assertNotContains(draft_page, 'Manage Revision')
        self.assertNotContains(draft_page, 'part-identity-edit')
        self.assertNotContains(draft_page, '[change]')

        self.release(self.revision)
        released_page = self.client.get(part_url)
        self.assertContains(released_page, self.url('part-revision-revise', self.revision))
        self.assertNotContains(released_page, self.edit_url)
        self.assertNotContains(released_page, 'Release&hellip;')
        self.assertNotContains(released_page, 'Manage Revision')


class ImmutableGuardTest(EditingTestCase):
    def setUp(self):
        super().setUp()
        self.revision = self.new_part('Widget')
        self.part = self.revision.part
        boms.add_subpart(self.organization, self.revision, self.parts[0].full_part_number(), count=1, reference='U1')
        self.primary = parts.add_manufacturer_part(self.organization, self.part, 'Acme', 'ACME-1')
        self.add_url = reverse('bom:part-add-manufacturer-part', kwargs={'part_id': self.part.id})
        PartRevision.objects.filter(pk=self.revision.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)

    def test_manufacturer_parts_can_be_added_to_the_draft_after_revising_and_not_before(self):
        mpn = {'manufacturer': 'Initech', 'manufacturer_part_number': 'INI-1'}
        with self.assertRaisesMessage(ServiceError, 'Revise this part to change its manufacturer parts.'):
            parts.add_manufacturer_part(self.organization, self.part, 'Initech', 'INI-0')

        self.client.post(reverse('bom:part-revision-revise', kwargs={
            'part_id': self.part.id, 'part_revision_id': self.revision.id}))
        self.client.post(self.add_url, mpn)

        self.assertTrue(ManufacturerPart.objects.filter(part=self.part, manufacturer_part_number='INI-1').exists())

    def test_a_crafted_subpart_post_against_a_released_revision_is_refused_without_an_error_page(self):
        subpart = self.revision.assembly.subparts.get()
        kwargs = {'part_id': self.part.id, 'part_revision_id': self.revision.id}
        edit_url = reverse('bom:part-edit-subpart', kwargs=dict(kwargs, subpart_id=subpart.id))
        remove_url = reverse('bom:part-remove-subpart', kwargs=dict(kwargs, subpart_id=subpart.id))

        attempts = [
            (edit_url, {'count': 5, 'reference': 'U1, U2, U3, U4, U5'}),
            (remove_url, {}),
            (reverse('bom:part-remove-all-subparts', kwargs=kwargs), {}),
            (reverse('bom:part-add-subpart', kwargs=kwargs),
             {'subpart_part_number': self.parts[1].full_part_number(), 'count': 1}),
        ]

        for url, data in attempts:
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            self.assertContains(self.client.get(response.url), 'cannot be edited')
        subpart.refresh_from_db()
        self.assertEqual(subpart.count, 1)
        self.assertEqual(self.history().count(), 0)

    def test_manufacturer_part_views_refuse_when_the_latest_revision_is_released(self):
        primary_kwargs = {'manufacturer_part_id': self.primary.id}
        attempts = [
            (self.add_url, {'manufacturer': 'Initech', 'manufacturer_part_number': 'INI-1'}),
            (reverse('bom:manufacturer-part-edit', kwargs=primary_kwargs),
             {'manufacturer': 'Acme', 'manufacturer_part_number': 'ACME-2'}),
            (reverse('bom:manufacturer-part-make-primary', kwargs=primary_kwargs), {}),
            (reverse('bom:manufacturer-part-delete', kwargs=primary_kwargs), {}),
        ]

        for url, data in attempts:
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            self.assertContains(self.client.get(response.url), 'Revise this part to change its manufacturer parts.')
        self.assertEqual(list(ManufacturerPart.objects.filter(part=self.part)), [self.primary])
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.manufacturer_part_number, 'ACME-1')


class ApprovalTestCase(EditingTestCase):
    def setUp(self):
        super().setUp()
        self.component = self.new_part('Component')
        PartRevision.objects.filter(pk=self.component.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        self.draft_parent = self.assembly_using(self.component, 'Draft parent')
        self.released_parent = self.assembly_using(self.component, 'Released parent')
        PartRevision.objects.filter(pk=self.released_parent.pk).update(
            configuration=constants.CONFIGURATION_TYPE_RELEASED)
        self.draft = parts.create_next_revision(self.component)

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

    def require_approval(self, required=True):
        ChangeSettings.objects.update_or_create(
            organization=self.organization, defaults={'require_release_approval': required})

    def principal(self, role):
        profile = self.user.bom_profile()
        profile.role = role
        profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        return self.user

    def fresh_draft(self, description='Subject'):
        return self.new_part(description)

    def configuration(self, revision):
        revision.refresh_from_db()
        return revision.configuration


class ReleaseApprovalTest(ApprovalTestCase):
    def test_release_matrix(self):
        released, draft = constants.CONFIGURATION_TYPE_RELEASED, constants.CONFIGURATION_TYPE_DRAFT
        for role, required, members, override, expected in [
            (constants.ROLE_TYPE_VIEWER, False, 2, False, draft),
            (constants.ROLE_TYPE_VIEWER, True, 2, True, draft),
            (constants.ROLE_TYPE_EDITOR, False, 2, False, released),
            (constants.ROLE_TYPE_ADMIN, False, 2, False, released),
            (constants.ROLE_TYPE_EDITOR, True, 1, False, released),
            (constants.ROLE_TYPE_ADMIN, True, 1, False, released),
            (constants.ROLE_TYPE_EDITOR, True, 2, False, draft),
            (constants.ROLE_TYPE_EDITOR, True, 2, True, draft),
            (constants.ROLE_TYPE_ADMIN, True, 2, False, draft),
            (constants.ROLE_TYPE_ADMIN, True, 2, True, released),
        ]:
            with self.subTest(role=role, required=required, members=members, override=override):
                User.objects.exclude(pk=self.user.pk).delete()
                if members == 2:
                    self.member(constants.ROLE_TYPE_EDITOR)
                self.require_approval(required)
                user = self.principal(role)
                revision = self.fresh_draft()
                try:
                    changes.release_revision(revision, user, override_approval=override)
                except ServiceError:
                    pass
                self.assertEqual(self.configuration(revision), expected)

    def test_an_admin_override_is_recorded_on_the_history_entry(self):
        self.member(constants.ROLE_TYPE_EDITOR)
        self.require_approval()
        revision = self.fresh_draft()

        changes.release_revision(revision, self.user, note='Urgent', override_approval=True)

        entry = self.history().get(items__kind=ChangeItem.KIND_RELEASE_REVISION)
        self.assertEqual(entry.protection_overridden_by, self.user)
        self.assertIn('Admin override', entry.title)
        self.assertTrue(entry.items.get().payload['approval_overridden'])

    def test_a_normal_release_records_no_override(self):
        revision = self.fresh_draft()

        changes.release_revision(revision, self.user)

        entry = self.history().get()
        self.assertIsNone(entry.protection_overridden_by)
        self.assertFalse(entry.items.get().payload['approval_overridden'])

    def test_the_dialog_offers_the_buttons_each_principal_may_use(self):
        self.member(constants.ROLE_TYPE_EDITOR)
        self.require_approval()
        url = reverse('bom:part-revision-release', kwargs={
            'part_id': self.draft.part_id, 'part_revision_id': self.draft.id})

        admin_page = self.client.get(url)
        self.assertContains(admin_page, 'Add to change order')
        self.assertContains(admin_page, 'Release as admin')
        self.assertNotContains(admin_page, 'value="release"')

        self.principal(constants.ROLE_TYPE_EDITOR)
        editor_page = self.client.get(url)
        self.assertContains(editor_page, 'Add to change order')
        self.assertNotContains(editor_page, 'Release as admin')
        self.assertNotContains(editor_page, 'value="release"')

        self.require_approval(False)
        self.assertContains(self.client.get(url), 'value="release"')

    def test_the_dialog_refuses_a_direct_release_when_approval_is_required(self):
        self.member(constants.ROLE_TYPE_EDITOR)
        self.require_approval()
        self.principal(constants.ROLE_TYPE_EDITOR)
        url = reverse('bom:part-revision-release', kwargs={
            'part_id': self.draft.part_id, 'part_revision_id': self.draft.id})

        response = self.client.post(url, {'action': 'release'})

        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response.url), 'requires approval to release')
        self.assertEqual(self.configuration(self.draft), constants.CONFIGURATION_TYPE_DRAFT)

    def test_add_to_change_order_starts_a_draft_change_order_and_leaves_the_revision_draft(self):
        self.member(constants.ROLE_TYPE_EDITOR)
        self.require_approval()
        url = reverse('bom:part-revision-release', kwargs={
            'part_id': self.draft.part_id, 'part_revision_id': self.draft.id})

        response = self.client.post(url, {'action': 'add_to_change_order', 'note': 'Please check'})

        change = Change.objects.get(organization=self.organization, is_direct_edit=False)
        self.assertRedirects(response, reverse('bom:change-review', kwargs={'number': change.number}))
        self.assertEqual((change.status, change.author, change.rationale),
                         (Change.STATUS_DRAFT, self.user, 'Please check'))
        self.assertEqual(change.title, f'Release {self.draft.part.full_part_number()} rev {self.draft.revision}')
        self.assertEqual(change.revisions.get().part_revision, self.draft)
        self.assertEqual(self.configuration(self.draft), constants.CONFIGURATION_TYPE_DRAFT)

    def test_approve_rolls_everything_back_when_a_release_fails(self):
        child = self.fresh_draft('Child')
        parent = self.assembly_using(child, 'Parent')
        other = self.member(constants.ROLE_TYPE_EDITOR)
        change = change_orders.create_change_order(self.organization, other, 'Both', part_revisions=[parent, child])
        change_orders.submit(change, other)
        real_release = changes._release_core
        calls = []

        def fail_on_second_release(revision_id, *args):
            calls.append(revision_id)
            if len(calls) == 2:
                raise ServiceError('forced failure')
            return real_release(revision_id, *args)

        with mock.patch.object(changes, '_release_core', side_effect=fail_on_second_release):
            with self.assertRaisesMessage(ServiceError, 'forced failure'):
                change_orders.approve(change, self.user)

        self.assertEqual(self.configuration(child), constants.CONFIGURATION_TYPE_IN_REVIEW)
        self.assertEqual(self.configuration(parent), constants.CONFIGURATION_TYPE_IN_REVIEW)
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_OPEN)
        self.assertFalse(change.items.exists())


class TelemetryTest(ApprovalTestCase):
    def assertEvent(self, logs, event):
        self.assertTrue(any(f'bom_event={event} organization={self.organization.id} user=' in line
                            for line in logs.output), logs.output)

    def test_each_editing_action_logs_one_structured_line(self):
        other = self.member(constants.ROLE_TYPE_EDITOR)
        draft = self.fresh_draft()
        released = self.fresh_draft('Released')
        PartRevision.objects.filter(pk=released.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        released.refresh_from_db()
        edit_url = reverse('bom:part-edit-revision', kwargs={'part_id': draft.part_id, 'part_revision_id': draft.id})

        with self.assertLogs('bom.services.changes', level='INFO') as logs:
            self.client.post(edit_url, {'revision': draft.revision, 'configuration': 'D', 'description': 'New'})
        self.assertEvent(logs, 'edit_save')
        self.assertIn(f'part={draft.part_id}', logs.output[0])

        with self.assertLogs('bom.services.changes', level='INFO') as logs:
            changes.revise_revision(released, self.user)
        self.assertEvent(logs, 'revise')

        with self.assertLogs('bom.services.changes', level='INFO') as logs:
            changes.release_revision(draft, self.user)
        self.assertEvent(logs, 'release')

        approval = change_orders.create_change_order(self.organization, other, 'Approve me', part_revisions=[self.draft])
        with self.assertLogs('bom.services.changes', level='INFO') as logs:
            change_orders.submit(approval, other)
        self.assertEvent(logs, 'request_approval')

        with self.assertLogs('bom.services.changes', level='INFO') as logs:
            change_orders.approve(approval, self.user)
        self.assertEvent(logs, 'approve')

        rejected = change_orders.create_change_order(self.organization, other, 'Reject me',
                                                     part_revisions=[self.fresh_draft('Another')])
        change_orders.submit(rejected, other)
        with self.assertLogs('bom.services.changes', level='INFO') as logs:
            change_orders.reject(rejected, self.user, 'No')
        self.assertEvent(logs, 'reject')
