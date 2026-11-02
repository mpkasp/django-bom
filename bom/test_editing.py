import importlib
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.models import APIToken, Change, ChangeItem, ManufacturerPart, PartRevision, PartRevisionPropertyDefinition
from bom.services import changes, parts


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
