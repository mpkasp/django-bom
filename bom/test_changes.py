from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_a_fake_organization, create_some_fake_parts, create_user_and_organization
from bom.models import Change, ChangeItem, ChangeSettings, ManufacturerPart, Part, PartRevision, SellerPart
from bom.services import boms, change_display, changes, parts
from bom.services.errors import ServiceError
from bom.test_mcp import MCPTestCase


def disable_review(organization):
    return False


def make_assembly(organization, parts_list, description='Assembly'):
    part_class = parts_list[0].number_class
    return parts.create_part(organization, description, part_class_code=str(part_class.code),
                             properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'})


class ChangesMCPTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.part_class_code = str(self.parts[0].number_class.code)
        self.assembly = make_assembly(self.organization, self.parts)

    def stage_cap(self, client, change_id, **extra):
        return client.call(
            'create_part', description='Cap', part_class_code=self.part_class_code,
            properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'},
            change_id=change_id, ref='cap', **extra)

    def test_staged_change_applies_parts_subparts_and_sourcing(self):
        client = self.mcp(self.apply_plaintext)
        started, error = client.call('start_change', title='Add cap', rationale='Needed for rev B')
        self.assertIsNone(error, error)
        change_id = started['change_id']

        _, error = self.stage_cap(client, change_id)
        self.assertIsNone(error, error)
        _, error = client.call('add_subpart', assembly_part_number=self.assembly.part.full_part_number(),
                               subpart_part_number='$ref:cap', count=2, reference='C1, C2', change_id=change_id)
        self.assertIsNone(error, error)
        staged, error = client.call('add_manufacturer_part', part_number='$ref:cap', manufacturer='Acme',
                                    manufacturer_part_number='CAP-1', change_id=change_id, ref='mp')
        self.assertIsNone(error, error)
        self.assertEqual(staged['items_in_change'], 3)
        _, error = client.call('add_seller_part', manufacturer_part_id='$ref:mp', seller='Digi-Key',
                               unit_cost='0.5', change_id=change_id)
        self.assertIsNone(error, error)
        self.assertFalse(PartRevision.objects.filter(description='Cap').exists())

        result, error = client.call('apply_change', change_id=change_id)
        self.assertIsNone(error, error)
        self.assertEqual(result['status'], 'applied')
        cap = PartRevision.objects.get(description='Cap').part
        self.assertEqual(self.assembly.assembly.subparts.get().part_revision.part, cap)
        self.assertEqual(SellerPart.objects.get(manufacturer_part__part=cap).seller.name, 'Digi-Key')
        self.assertEqual(result['items'][0]['result']['part_number'], cap.full_part_number())

    def test_propose_only_token_submits_a_change_request(self):
        client = self.mcp(self.write_plaintext)
        result, error = client.call('create_part', description='Cap', part_class_code=self.part_class_code,
                                    properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'})
        self.assertIsNone(error, error)
        self.assertIn(f"/changes/{result['number']}/", result['url'])
        self.assertIn('change request', result['message'])
        self.assertFalse(PartRevision.objects.filter(description='Cap').exists())
        self.assertEqual(Change.objects.get(id=result['change_id']).status, Change.STATUS_OPEN)

        _, error = client.call('apply_change', change_id=result['change_id'])
        self.assertIn("'apply' scope", error)

        organization, _ = client.call('get_organization')
        self.assertEqual(organization['token_level'], 'propose')
        self.assertTrue(organization['review_enabled'])

        _, error = self.mcp(self.apply_plaintext).call('apply_change', change_id=result['change_id'])
        self.assertIsNone(error, error)
        self.assertTrue(PartRevision.objects.filter(description='Cap').exists())

    def test_apply_scope_applies_directly_without_a_change_id(self):
        client = self.mcp(self.apply_plaintext)
        result, error = client.call('create_part', description='Cap', part_class_code=self.part_class_code,
                                    properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'})
        self.assertIsNone(error, error)
        self.assertEqual(result['configuration'], 'Draft')
        change = Change.objects.get()
        self.assertEqual((change.status, change.items.count(), change.api_token), ('applied', 1, self.apply_token))
        organization, _ = client.call('get_organization')
        self.assertEqual(organization['token_level'], 'apply')

    def test_host_can_turn_off_mcp_apply(self):
        config = dict(settings.BOM_CONFIG_DEFAULT, mcp_apply_enabled=False)
        with override_settings(BOM_CONFIG=config):
            client = self.mcp(self.apply_plaintext)
            organization, _ = client.call('get_organization')
            self.assertEqual(organization['token_level'], 'propose')
            _, error = client.call('apply_change', change_id=1)
            self.assertIn('not enabled', error)

    def test_eco_applies_to_a_new_draft_revision_and_leaves_the_released_one_untouched(self):
        PartRevision.objects.filter(pk=self.assembly.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        client = self.mcp(self.apply_plaintext)
        assembly_number = self.assembly.part.full_part_number()
        started, _ = client.call('start_change', title='Revise assembly', rationale='ECO')
        _, error = client.call('add_subpart', assembly_part_number=assembly_number,
                               subpart_part_number=self.parts[0].full_part_number(), change_id=started['change_id'])
        self.assertIsNone(error, error)
        detail, _ = client.call('get_change', change_id=started['change_id'])
        self.assertTrue(detail['is_eco'])
        self.assertTrue(detail['is_protected'])

        detail, error = client.call('apply_change', change_id=started['change_id'])
        self.assertIsNone(error, error)
        self.assertTrue(detail['is_eco'])
        new_revision = PartRevision.objects.get(part=self.assembly.part, revision='2')
        self.assertEqual(detail['items'][0]['result']['new_revision_id'], new_revision.id)
        self.assertEqual(new_revision.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.assertEqual(new_revision.assembly.subparts.get().part_revision.part, self.parts[0])
        self.assertEqual(new_revision.description, self.assembly.description)
        self.assertEqual(new_revision.properties.count(), self.assembly.properties.count())
        self.assertFalse(self.assembly.assembly.subparts.exists())
        self.assertEqual(changes.created_revisions(Change.objects.get(id=started['change_id'])), [new_revision])

    def test_get_change_reports_impact_and_list_changes_flags_ecos(self):
        top = make_assembly(self.organization, self.parts, description='Top')
        boms.add_subpart(self.organization, top, self.assembly.part.full_part_number(), count=1)
        PartRevision.objects.filter(pk=top.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        client = self.mcp(self.apply_plaintext)
        started, _ = client.call('start_change', title='Edit child', rationale='Why')
        client.call('add_subpart', assembly_part_number=self.assembly.part.full_part_number(),
                    subpart_part_number=self.parts[0].full_part_number(), change_id=started['change_id'])

        detail, error = client.call('get_change', change_id=started['change_id'])
        self.assertIsNone(error, error)
        self.assertEqual([(entry['part_number'], entry['needs_new_revision']) for entry in detail['impact']['assemblies']],
                         [(top.part.full_part_number(), True)])
        self.assertIn('delta', detail['impact']['cost'])
        self.assertEqual((detail['is_eco'], detail['is_stale'], detail['is_protected']), (False, False, False))

        listed, _ = client.call('list_changes', status='draft')
        self.assertFalse(listed[0]['is_eco'])

    def test_eco_disabled_refuses_immutable_targets(self):
        PartRevision.objects.filter(pk=self.assembly.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        config = dict(settings.BOM_CONFIG_DEFAULT, eco_enabled=False)
        with override_settings(BOM_CONFIG=config):
            client = self.mcp(self.apply_plaintext)
            _, error = client.call('add_subpart', assembly_part_number=self.assembly.part.full_part_number(),
                                   subpart_part_number=self.parts[0].full_part_number())
            self.assertIn('Engineering Change Order', error)
            self.assertEqual(Change.objects.get().status, Change.STATUS_WITHDRAWN)
            self.assertFalse(PartRevision.objects.filter(part=self.assembly.part, revision='2').exists())

    def test_update_and_remove_subpart_tools_edit_a_line(self):
        client = self.mcp(self.apply_plaintext)
        assembly_number = self.assembly.part.full_part_number()
        part_number = self.parts[0].full_part_number()
        client.call('add_subpart', assembly_part_number=assembly_number, subpart_part_number=part_number, count=2)
        report, error = client.call('update_subpart', assembly_part_number=assembly_number,
                                    subpart_part_number=part_number, count=5)
        self.assertIsNone(error, error)
        self.assertEqual(self.assembly.assembly.subparts.get().count, 5)
        _, error = client.call('remove_subpart', assembly_part_number=assembly_number,
                               subpart_part_number=part_number)
        self.assertIsNone(error, error)
        self.assertFalse(self.assembly.assembly.subparts.exists())
        _, error = client.call('remove_subpart', assembly_part_number=assembly_number,
                               subpart_part_number=part_number)
        self.assertIn('no line', error)

    def test_an_agent_corrects_its_change_by_removing_edits_and_staging_again(self):
        client = self.mcp(self.apply_plaintext)
        started, _ = client.call('start_change', title='Two parts', rationale='Testing')
        change_id = started['change_id']
        self.stage_cap(client, change_id)
        client.call('create_part', description='Resistor', part_class_code=self.part_class_code,
                    properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'},
                    change_id=change_id)
        client.call('add_subpart', assembly_part_number=self.assembly.part.full_part_number(),
                    subpart_part_number='$ref:cap', change_id=change_id)

        _, error = client.call('remove_change_item', change_id=change_id, position=1)
        self.assertIn('Item 3 uses what item 1 creates; remove that first', error)
        client.call('remove_change_item', change_id=change_id, position=3)
        detail, error = client.call('remove_change_item', change_id=change_id, position=1)
        self.assertIsNone(error, error)
        self.assertEqual([item['position'] for item in detail['items']], [2])
        self.assertEqual(detail['edits_after_submission'], [])

        _, error = client.call('apply_change', change_id=change_id)
        self.assertIsNone(error, error)
        self.assertEqual(sorted(PartRevision.objects.filter(description__in=['Cap', 'Resistor'])
                                .values_list('description', flat=True)), ['Resistor'])

    def test_other_authors_changes_cannot_be_staged_into_and_listing_filters_by_status(self):
        other = User.objects.create_user('other', 'other@example.com', 'ghostpassword')
        other.bom_profile(organization=self.organization)
        foreign = changes.start_change(self.organization, other, 'Not yours')
        client = self.mcp(self.apply_plaintext)
        _, error = self.stage_cap(client, foreign.id)
        self.assertIn('only its author', error)

        client.call('start_change', title='Mine')
        listed, _ = client.call('list_changes', status='draft')
        self.assertEqual({entry['title'] for entry in listed}, {'Not yours', 'Mine'})
        listed, _ = client.call('list_changes')
        self.assertEqual(listed, [])


class ChangesServiceTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.assembly = make_assembly(self.organization, self.parts)
        self.assembly_number = self.assembly.part.full_part_number()
        self.primary_part = self.parts[0]

    def add_member(self, role):
        member = User.objects.create_user(f'member-{role}', f'{role}@example.com', 'ghostpassword')
        profile = member.bom_profile(organization=self.organization)
        profile.role = role
        profile.save()
        return member

    def stage_protected_change(self, author):
        ChangeSettings.objects.create(organization=self.organization, protect_primary_manufacturer_part=True)
        change = changes.start_change(self.organization, author, 'Swap MPN', 'Second source')
        changes.stage_item(change, ChangeItem.KIND_ADD_MANUFACTURER_PART, {
            'part_number': self.primary_part.full_part_number(), 'manufacturer': 'Acme',
            'manufacturer_part_number': 'ALT-1'})
        return change

    def test_numbers_increment_per_organization(self):
        first = changes.start_change(self.organization, self.user, 'One')
        second = changes.start_change(self.organization, self.user, 'Two')
        self.assertEqual((first.number, second.number), (1, 2))

    def test_stage_item_validates_without_saving(self):
        change = changes.start_change(self.organization, self.user, 'Bad subpart')
        with self.assertRaises(ServiceError) as raised:
            changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
                'assembly_part_number': self.assembly_number, 'subpart_part_number': 'nope', 'count': 1})
        self.assertIn('subpart_part_number', raised.exception.field_errors)
        with self.assertRaises(ServiceError):
            changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
                'assembly_part_number': self.assembly_number, 'subpart_part_number': '$ref:missing'})
        self.assertEqual(change.items.count(), 0)

        item = changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': self.assembly_number,
            'subpart_part_number': self.primary_part.full_part_number(), 'count': 1})
        self.assertEqual(item.part_revision, self.assembly)
        self.assertFalse(self.assembly.assembly and self.assembly.assembly.subparts.exists())

    def test_single_member_organization_applies_its_own_protected_change(self):
        change = self.stage_protected_change(self.user)
        self.assertTrue(change.is_protected)
        self.assertFalse(change.is_eco)
        self.assertTrue(changes.approval_status(change, self.user)['can_apply'])
        changes.apply_change(change, self.user)
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_APPLIED)
        self.assertIsNone(change.protection_overridden_by)
        self.assertTrue(ManufacturerPart.objects.filter(part=self.primary_part, manufacturer_part_number='ALT-1').exists())

    def test_two_member_organization_blocks_self_apply_of_protected_change(self):
        editor = self.add_member(constants.ROLE_TYPE_EDITOR)
        change = self.stage_protected_change(editor)
        changes.submit_change(change, editor)
        with self.assertRaisesMessage(ServiceError, 'someone other than its author'):
            changes.apply_change(change, editor)
        with self.assertRaises(ServiceError):
            changes.apply_change(change, editor, override_protection=True)

        changes.apply_change(change, self.user)
        change.refresh_from_db()
        self.assertEqual((change.status, change.resolved_by), (Change.STATUS_APPLIED, self.user))

    def test_admin_can_override_protection_on_their_own_change(self):
        self.add_member(constants.ROLE_TYPE_EDITOR)
        change = self.stage_protected_change(self.user)
        with self.assertRaisesMessage(ServiceError, 'someone other'):
            changes.apply_change(change, self.user)

        changes.apply_change(change, self.user, override_protection=True)
        change.refresh_from_db()
        self.assertEqual((change.status, change.protection_overridden_by), (Change.STATUS_APPLIED, self.user))

    def test_unprotected_change_can_be_applied_by_its_author(self):
        editor = self.add_member(constants.ROLE_TYPE_EDITOR)
        change = changes.start_change(self.organization, editor, 'Add part')
        changes.stage_item(change, ChangeItem.KIND_CREATE_PART, {
            'description': 'Cap', 'part_class_code': str(self.primary_part.number_class.code),
            'properties': {'sheen': 'matte', 'voltage': '3.3'}, 'property_units': {'voltage': 'V'}})
        changes.submit_change(change, editor)
        changes.apply_change(change, editor)
        self.assertTrue(PartRevision.objects.filter(description='Cap').exists())

    def test_viewer_cannot_apply(self):
        viewer = self.add_member(constants.ROLE_TYPE_VIEWER)
        change = changes.start_change(self.organization, viewer, 'Add part')
        changes.stage_item(change, ChangeItem.KIND_CREATE_PART, {
            'description': 'Cap', 'part_class_code': str(self.primary_part.number_class.code),
            'properties': {'sheen': 'matte', 'voltage': '3.3'}, 'property_units': {'voltage': 'V'}})
        with self.assertRaisesMessage(ServiceError, 'permission'):
            changes.apply_change(change, viewer)

    def test_change_goes_stale_when_the_base_is_modified(self):
        change = changes.start_change(self.organization, self.user, 'Add subpart')
        changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': self.assembly_number,
            'subpart_part_number': self.primary_part.full_part_number(), 'count': 1})
        self.assertFalse(change.is_stale)

        boms.add_subpart(self.organization, self.assembly, self.parts[1].full_part_number(), count=1)
        self.assertTrue(change.is_stale)
        with self.assertRaisesMessage(ServiceError, 'stale'):
            changes.apply_change(change, self.user)

    def test_failed_apply_rolls_back_every_item(self):
        change = changes.start_change(self.organization, self.user, 'Two parts with the same number')
        payload = {'description': 'Cap', 'part_class_code': str(self.primary_part.number_class.code),
                   'number_item': '7777', 'number_variation': '01',
                   'properties': {'sheen': 'matte', 'voltage': '3.3'}, 'property_units': {'voltage': 'V'}}
        changes.stage_item(change, ChangeItem.KIND_CREATE_PART, payload)
        changes.stage_item(change, ChangeItem.KIND_CREATE_PART, payload)
        with self.assertRaisesMessage(ServiceError, 'Item 2'):
            changes.apply_change(change, self.user)
        self.assertFalse(Part.objects.filter(number_item='7777').exists())
        change.refresh_from_db()
        self.assertEqual(change.status, Change.STATUS_DRAFT)

    def test_reject_and_withdraw(self):
        editor = self.add_member(constants.ROLE_TYPE_EDITOR)
        change = self.stage_protected_change(editor)
        changes.submit_change(change, editor)
        with self.assertRaises(ServiceError):
            changes.withdraw_change(change, self.user)
        with self.assertRaises(ServiceError):
            changes.reject_change(change, self.user, '')
        changes.reject_change(change, self.user, 'Not now')
        self.assertEqual(change.status, Change.STATUS_REJECTED)
        self.assertEqual(change.items.get().reject_reason, 'Not now')

        other = changes.start_change(self.organization, editor, 'Changed my mind')
        changes.withdraw_change(other, editor)
        self.assertEqual(other.status, Change.STATUS_WITHDRAWN)

    def make_priced_cap(self, unit_cost='2.00'):
        cap = make_assembly(self.organization, self.parts, description='Priced cap')
        manufacturer_part = parts.add_manufacturer_part(self.organization, cap.part, 'Acme', 'CAP-1')
        parts.add_seller_part(self.organization, manufacturer_part, 'Digi-Key', unit_cost)
        return cap

    def stage_add(self, change, subpart, assembly=None, **fields):
        return changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': (assembly or self.assembly).part.full_part_number(),
            'subpart_part_number': subpart.part.full_part_number(), 'count': 1, **fields})

    def test_impact_lists_parent_assemblies_and_marks_released_ones(self):
        top = make_assembly(self.organization, self.parts, description='Top')
        boms.add_subpart(self.organization, top, self.assembly_number, count=1)
        PartRevision.objects.filter(pk=top.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        change = changes.start_change(self.organization, self.user, 'Edit child')
        self.stage_add(change, self.parts[0].latest())

        assemblies = changes.impact(change)['assemblies']
        self.assertEqual([(entry['part_number'], entry['configuration'], entry['needs_new_revision'])
                          for entry in assemblies], [(top.part.full_part_number(), 'Released', True)])

        PartRevision.objects.filter(pk=top.pk).update(configuration=constants.CONFIGURATION_TYPE_DRAFT)
        self.assertFalse(changes.impact(change)['assemblies'][0]['needs_new_revision'])

    def test_cost_delta_is_a_rolled_back_dry_run_and_can_protect_the_change(self):
        cap = self.make_priced_cap()
        change = changes.start_change(self.organization, self.user, 'Add priced cap')
        self.stage_add(change, cap, count=3)

        cost = changes.impact(change)['cost']
        self.assertEqual(cost['quantity'], 100)
        self.assertEqual(cost['delta'], Decimal('6'))
        self.assertEqual(cost['targets'][0]['before'], Decimal('0'))
        self.assertFalse(self.assembly.assembly.subparts.exists())
        self.assertFalse(change.is_protected)

        change_settings = ChangeSettings.objects.create(organization=self.organization, protect_cost_delta=Decimal('7'))
        self.assertFalse(change.is_protected)
        change_settings.protect_cost_delta = Decimal('6')
        change_settings.save()
        self.assertTrue(change.is_protected)
        self.assertFalse(self.assembly.assembly.subparts.exists())

    def test_rebase_rejects_conflicting_items_and_clears_staleness(self):
        cap = self.make_priced_cap()
        boms.add_subpart(self.organization, self.assembly, cap.part.full_part_number(), count=2)
        change = changes.start_change(self.organization, self.user, 'Edit lines')
        changes.stage_item(change, ChangeItem.KIND_UPDATE_SUBPART, {
            'assembly_part_number': self.assembly_number, 'subpart_part_number': cap.part.full_part_number(),
            'count': 4})
        keep = self.stage_add(change, self.parts[0].latest())
        self.assertFalse(change.is_stale)

        boms.remove_subpart(self.organization, self.assembly, cap.part.full_part_number())
        self.assertTrue(change.is_stale)
        conflicts = changes.rebase_change(change)
        self.assertEqual(len(conflicts), 1)
        self.assertIn('conflict:', conflicts[0]['reason'])
        update_item = change.items.get(kind=ChangeItem.KIND_UPDATE_SUBPART)
        self.assertEqual(update_item.status, ChangeItem.STATUS_REJECTED)
        keep.refresh_from_db()
        self.assertEqual(keep.status, ChangeItem.STATUS_ACCEPTED)
        self.assertFalse(change.is_stale)
        changes.apply_change(change, self.user)
        self.assertEqual(self.assembly.assembly.subparts.get().part_revision.part, self.parts[0])

    def test_apply_refuses_items_that_no_longer_validate(self):
        change = changes.start_change(self.organization, self.user, 'Add then obsolete')
        item = self.stage_add(change, self.parts[0].latest())
        PartRevision.objects.filter(part=self.parts[0]).update(configuration=constants.CONFIGURATION_TYPE_OBSOLETE)
        change.base_digest = changes.compute_digest(change)
        change.save()
        problems = changes.validate_change(change)
        self.assertEqual([(problem['item'], problem['level']) for problem in problems], [(item, 'error')])
        with self.assertRaisesMessage(ServiceError, 'no longer validates'):
            changes.apply_change(change, self.user)

    def test_eco_on_released_assembly_can_remove_a_line(self):
        boms.add_subpart(self.organization, self.assembly, self.parts[0].full_part_number(), count=2)
        PartRevision.objects.filter(pk=self.assembly.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        change = changes.start_change(self.organization, self.user, 'Drop part')
        changes.stage_item(change, ChangeItem.KIND_REMOVE_SUBPART, {
            'assembly_part_number': self.assembly_number, 'subpart_part_number': self.parts[0].full_part_number()})
        changes.apply_change(change, self.user)
        self.assertEqual(self.assembly.assembly.subparts.count(), 1)
        new_revision = PartRevision.objects.get(part=self.assembly.part, revision='2')
        self.assertFalse(new_revision.assembly.subparts.exists())

    def test_revert_undoes_added_and_merged_lines_and_deletes_created_sourcing(self):
        boms.add_subpart(self.organization, self.assembly, self.parts[0].full_part_number(), count=2)
        change = changes.start_change(self.organization, self.user, 'Add lines')
        self.stage_add(change, self.parts[0].latest(), reference='')
        self.stage_add(change, self.parts[1].latest())
        changes.stage_item(change, ChangeItem.KIND_ADD_MANUFACTURER_PART, {
            'part_number': self.parts[1].full_part_number(), 'manufacturer': 'Acme', 'manufacturer_part_number': 'NEW-1'},
            ref='mpn')
        changes.stage_item(change, ChangeItem.KIND_ADD_SELLER_PART, {
            'manufacturer_part_id': '$ref:mpn', 'seller': 'Digi-Key', 'unit_cost': '1.0'})
        changes.apply_change(change, self.user)
        self.assertEqual(self.assembly.assembly.subparts.get(part_revision__part=self.parts[0]).count, 3)
        self.assertTrue(ManufacturerPart.objects.filter(manufacturer_part_number='NEW-1').exists())

        counter = changes.revert_change(change, self.user)
        self.assertEqual(counter.status, Change.STATUS_APPLIED)
        lines = self.assembly.assembly.subparts.all()
        self.assertEqual([(line.part_revision.part, line.count) for line in lines], [(self.parts[0], 2)])
        self.assertFalse(ManufacturerPart.objects.filter(manufacturer_part_number='NEW-1').exists())
        self.assertFalse(SellerPart.objects.filter(manufacturer_part__manufacturer_part_number='NEW-1').exists())
        with self.assertRaisesMessage(ServiceError, 'already been reverted'):
            changes.revert_change(change, self.user)

    def test_revert_lists_created_parts_in_the_rationale(self):
        change = changes.start_change(self.organization, self.user, 'Cap and line')
        changes.stage_item(change, ChangeItem.KIND_CREATE_PART, {
            'description': 'Cap', 'part_class_code': str(self.primary_part.number_class.code),
            'properties': {'sheen': 'matte', 'voltage': '3.3'}, 'property_units': {'voltage': 'V'}}, ref='cap')
        changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': self.assembly_number, 'subpart_part_number': '$ref:cap', 'count': 1})
        changes.apply_change(change, self.user)

        counter = changes.revert_change(change, self.user)
        self.assertFalse(self.assembly.assembly.subparts.exists())
        self.assertIn('was created and is left in place', counter.rationale)
        self.assertTrue(PartRevision.objects.filter(description='Cap').exists())

    def stage_leaf_eco(self):
        """A Released leaf used by a Released and a Draft parent, edited by an applied ECO."""
        leaf = make_assembly(self.organization, self.parts, description='Leaf')
        leaf_number = leaf.part.full_part_number()
        released_parent = make_assembly(self.organization, self.parts, description='Released parent')
        draft_parent = make_assembly(self.organization, self.parts, description='Draft parent')
        for parent in (released_parent, draft_parent):
            boms.add_subpart(self.organization, parent, leaf_number, count=2, reference='L1, L2')
        PartRevision.objects.filter(pk__in=(leaf.pk, released_parent.pk)).update(
            configuration=constants.CONFIGURATION_TYPE_RELEASED)
        change = changes.start_change(self.organization, self.user, 'Rework leaf')
        changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': leaf_number, 'subpart_part_number': self.parts[0].full_part_number()})
        changes.apply_change(change, self.user)
        return leaf, released_parent, draft_parent, change

    def test_eco_apply_moves_draft_parents_and_leaves_released_parents_on_the_old_revision(self):
        leaf, released_parent, draft_parent, change = self.stage_leaf_eco()
        new_leaf = PartRevision.objects.get(part=leaf.part, revision='2')

        self.assertEqual(draft_parent.assembly.subparts.get().part_revision, new_leaf)
        self.assertEqual(released_parent.assembly.subparts.get().part_revision, leaf)
        result = change.items.get().result
        self.assertEqual(result['previous_revision_id'], leaf.id)
        self.assertEqual(result['draft_parents_updated'], [f'{draft_parent.part.full_part_number()} rev 1'])
        self.assertEqual([candidate['parent'] for candidate in changes.cascade_candidates(change)], [released_parent])

    def test_cascade_creates_draft_revisions_of_selected_released_parents_pointing_at_the_new_revision(self):
        leaf, released_parent, _, change = self.stage_leaf_eco()
        new_leaf = PartRevision.objects.get(part=leaf.part, revision='2')

        created = changes.cascade_revisions(change, self.user, [released_parent.id])

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].part, released_parent.part)
        self.assertEqual(created[0].configuration, constants.CONFIGURATION_TYPE_DRAFT)
        line = created[0].assembly.subparts.get()
        self.assertEqual((line.part_revision, line.count, line.reference), (new_leaf, 2, 'L1, L2'))
        self.assertEqual(released_parent.assembly.subparts.get().part_revision, leaf)
        change.refresh_from_db()
        self.assertEqual(change.cascade, [{'source_revision_id': released_parent.id, 'new_revision_id': created[0].id}])
        self.assertEqual(changes.cascaded_revisions(change), created)
        self.assertEqual(changes.cascade_candidates(change), [])
        with self.assertRaises(ServiceError):
            changes.cascade_revisions(change, self.user, [released_parent.id])

    def test_cascade_requires_a_selection_and_edit_permission(self):
        _, released_parent, _, change = self.stage_leaf_eco()
        with self.assertRaises(ServiceError):
            changes.cascade_revisions(change, self.user, [])
        with self.assertRaises(ServiceError):
            changes.cascade_revisions(change, self.add_member(constants.ROLE_TYPE_VIEWER), [released_parent.id])

    def test_part_history_lists_only_applied_changes(self):
        leaf, _, _, applied = self.stage_leaf_eco()
        open_change = changes.start_change(self.organization, self.user, 'Open one')
        changes.stage_item(open_change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': leaf.part.full_part_number(), 'subpart_part_number': self.parts[1].full_part_number()})
        changes.submit_change(open_change, self.user)
        changes.start_change(self.organization, self.user, 'Untouched')

        history = changes.part_history(leaf.part)
        self.assertEqual([entry['change'] for entry in history], [applied])
        self.assertEqual(history[0]['diff_revision_ids'], (leaf.id, leaf.part.latest().id))
        self.assertEqual(changes.part_history(self.parts[3]), [])

    def test_organization_scoping(self):
        other_user = User.objects.create_user('stranger', 'stranger@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(other_user)
        change = changes.start_change(self.organization, self.user, 'Mine')
        with self.assertRaises(ServiceError):
            changes.change_for(other_organization, change.id)


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class ChangesViewTest(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.parts = create_some_fake_parts(self.organization)
        self.client = Client()
        self.client.login(username='kasper', password='ghostpassword')
        self.change = changes.start_change(self.organization, self.user, 'Add a cap', 'Because')
        changes.stage_item(self.change, ChangeItem.KIND_CREATE_PART, {
            'description': 'Cap', 'part_class_code': str(self.parts[0].number_class.code),
            'properties': {'sheen': 'matte', 'voltage': '3.3'}, 'property_units': {'voltage': 'V'}})

    def new_cap(self):
        return parts.create_part(self.organization, 'Other cap', part_class_code=str(self.parts[0].number_class.code),
                                 properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'}).part

    def review_url(self, name='change-review'):
        return reverse(f'bom:{name}', kwargs={'number': self.change.number})

    def test_pages_require_login_and_the_host_flag(self):
        self.assertEqual(Client().get(reverse('bom:change-list')).status_code, 302)
        config = dict(settings.BOM_CONFIG_DEFAULT, changes_review_enabled='bom.test_changes.disable_review')
        with override_settings(BOM_CONFIG=config):
            self.assertEqual(self.client.get(reverse('bom:change-list')).status_code, 404)
            self.assertEqual(self.client.get(self.review_url()).status_code, 404)

    def test_list_filters_and_nav_badge(self):
        changes.submit_change(self.change, self.user)
        response = self.client.get(reverse('bom:change-list'))
        self.assertContains(response, 'Add a cap')
        self.assertContains(response, 'new badge')
        self.assertNotContains(self.client.get(reverse('bom:change-list') + '?filter=released'), 'Add a cap')

    def two_edit_request(self):
        cap = self.new_cap()
        PartRevision.objects.filter(pk=cap.latest().pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        change = changes.start_change(self.organization, self.user, 'Two edits')
        rename = changes.stage_item(change, ChangeItem.KIND_UPDATE_PART_REVISION, {
            'part_number': cap.full_part_number(), 'description': 'Renamed cap'})
        add_line = changes.stage_item(change, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': self.parts[2].full_part_number(), 'subpart_part_number': cap.full_part_number(),
            'count': 2, 'reference': 'C1, C2'})
        changes.submit_change(change, self.user)
        return change, rename, add_line

    def test_a_reviewer_who_edits_a_protected_request_cannot_approve_it(self):
        change, rename, add_line = self.two_edit_request()
        reviewer = User.objects.create_user('reviewer', 'reviewer@example.com', 'ghostpassword')
        profile = reviewer.bom_profile(organization=self.organization)
        profile.role = constants.ROLE_TYPE_EDITOR
        profile.save()
        self.assertTrue(changes.approval_status(change, reviewer)['can_apply'])

        changes.edit_item(change, reviewer, add_line, {'count': '1', 'reference': 'C1'})

        approval = changes.approval_status(change, reviewer)
        self.assertFalse(approval['can_apply'])
        self.assertIn('you edited it', approval['message'])
        with self.assertRaises(ServiceError):
            changes.apply_change(change, reviewer)

    def test_a_stale_request_is_rebased_before_it_is_edited(self):
        change, rename, add_line = self.two_edit_request()
        boms.add_subpart(self.organization, self.parts[2].latest(), self.parts[0].full_part_number(), count=1)

        with self.assertRaisesMessage(ServiceError, 'Rebase it'):
            changes.remove_item(change, self.user, rename)

    def test_part_page_history_tab_lists_applied_changes_newest_first_and_skips_open_ones(self):
        assembly = self.parts[2].latest()
        applied = changes.start_change(self.organization, self.user, 'Applied one')
        changes.stage_item(applied, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': assembly.part.full_part_number(),
            'subpart_part_number': self.parts[1].full_part_number(), 'count': 1})
        changes.apply_change(applied, self.user)
        changes.record_direct_edit(self.organization, self.user, assembly.part, ChangeItem.KIND_UPDATE_PART,
                                   'Direct edit two', part_revision=assembly)
        opened = changes.start_change(self.organization, self.user, 'Open one')
        changes.stage_item(opened, ChangeItem.KIND_ADD_SUBPART, {
            'assembly_part_number': assembly.part.full_part_number(),
            'subpart_part_number': self.parts[0].full_part_number(), 'count': 1})
        changes.submit_change(opened, self.user)

        response = self.client.get(reverse('bom:part-info', kwargs={'part_id': assembly.part_id}))
        content = response.content.decode()
        self.assertIn('id="history-tab"', content)
        tab = content[content.index('<div id="history"'):]
        self.assertLess(tab.index('Direct edit two'), tab.index('Applied one'))
        self.assertNotIn('Open one', tab)

        unrelated = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.parts[3].id}))
        self.assertContains(unrelated, 'No history yet.')

    def test_part_page_shows_history_even_when_review_is_off(self):
        with override_settings(BOM_CONFIG=dict(settings.BOM_CONFIG_DEFAULT, changes_review_enabled=disable_review)):
            response = self.client.get(reverse('bom:part-info', kwargs={'part_id': self.parts[2].id}))
        self.assertContains(response, 'id="history-tab"')

    def test_revision_diff_lists_added_removed_and_changed_lines_and_properties(self):
        old = make_assembly(self.organization, self.parts, description='Assembly')
        boms.add_subpart(self.organization, old, self.parts[0].full_part_number(), count=1, reference='A1')
        boms.add_subpart(self.organization, old, self.parts[1].full_part_number(), count=2)
        new = parts.create_next_revision(old)
        boms.update_subpart(self.organization, new, self.parts[0].full_part_number(), count=5, reference='A1, A2, A3, A4, A5')
        boms.remove_subpart(self.organization, new, self.parts[1].full_part_number())
        boms.add_subpart(self.organization, new, self.parts[2].full_part_number(), count=1)
        new.description = 'Assembly v2'
        new.save()
        sheen = new.properties.get(property_definition__code='sheen')
        sheen.value_raw = 'gloss'
        sheen.save()

        response = self.client.get(reverse('bom:part-diff', kwargs={
            'part_id': old.part_id, 'revision_a_id': old.id, 'revision_b_id': new.id}))

        self.assertEqual(response.status_code, 200)
        labels = {(item.get_kind_display, item.summary): item for item in response.context['items']}
        self.assertEqual(labels[('Update line', self.parts[0].full_part_number())].payload,
                         {'count': 5, 'reference': 'A1, A2, A3, A4, A5'})
        self.assertEqual(labels[('Update line', self.parts[0].full_part_number())].before,
                         {'count': 1, 'reference': 'A1'})
        self.assertIn(('Remove line', self.parts[1].full_part_number()), labels)
        self.assertIn(('Add line', self.parts[2].full_part_number()), labels)
        property_item = next(item for item in response.context['items'] if item.get_kind_display == 'Update property')
        self.assertEqual((property_item.before, property_item.payload), ({'value': 'matte'}, {'value': 'gloss'}))
        self.assertEqual(next(item for item in response.context['items'] if item.get_kind_display == 'Revision').payload,
                         {'description': 'Assembly v2'})

    def test_revision_diff_is_scoped_to_the_part_and_organization(self):
        first, second = self.parts[0].latest(), self.parts[1].latest()
        url = reverse('bom:part-diff', kwargs={
            'part_id': first.part_id, 'revision_a_id': first.id, 'revision_b_id': second.id})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_part_page_offers_compare_with_when_the_part_has_several_revisions(self):
        assembly = make_assembly(self.organization, self.parts)
        self.assertNotContains(self.client.get(reverse('bom:part-info', kwargs={'part_id': assembly.part_id})),
                               'Compare with')
        newer = parts.create_next_revision(assembly)
        response = self.client.get(reverse('bom:part-info', kwargs={'part_id': assembly.part_id}))
        self.assertContains(response, 'Compare with')
        self.assertContains(response, reverse('bom:part-diff', kwargs={
            'part_id': assembly.part_id, 'revision_a_id': assembly.id, 'revision_b_id': newer.id}))

    def test_other_organizations_changes_are_not_found(self):
        stranger = User.objects.create_user('stranger', 'stranger@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(stranger)
        profile = stranger.bom_profile(organization=other_organization)
        profile.role = constants.ROLE_TYPE_ADMIN
        profile.save()
        client = Client()
        client.login(username='stranger', password='ghostpassword')
        self.assertEqual(client.get(self.review_url()).status_code, 404)

    def test_confirmed_csv_import_records_an_applied_change(self):
        part_class_code = self.parts[0].number_class.code
        csv_text = f'part_class,description,revision,property_sheen,property_voltage\n{part_class_code},Imported cap,1,matte,3.3\n'
        upload = SimpleUploadedFile('parts.csv', csv_text.encode(), content_type='text/csv')
        preview = self.client.post(reverse('bom:import'), {'type': 'parts', 'file': upload})
        self.assertTrue(preview.context['token'], preview.context['errors'])
        self.client.post(reverse('bom:import-confirm'), {'token': preview.context['token']})

        change = Change.objects.get(organization=self.organization, status=Change.STATUS_APPLIED)
        item = change.items.get()
        self.assertEqual(item.kind, ChangeItem.KIND_CSV_IMPORT)
        self.assertIn('Imported parts', item.summary)
        self.assertTrue(item.payload['changes_digest'])
        with self.assertRaises(ServiceError):
            changes.apply_change(change, self.user)


class ChangeDisplayTest(TestCase):
    def test_quantities_drop_trailing_zeros(self):
        self.assertEqual([change_display.quantity(value) for value in (1.0, '2.50', 3)], ['1', '2.5', '3'])

    def test_money_shows_cents_unless_the_price_has_fractions_of_a_cent(self):
        self.assertEqual(change_display.money(Decimal('3.33000'), 'USD'), '$3.33')
        self.assertEqual(change_display.money(Decimal('0.2'), 'USD'), '$0.20')
        self.assertEqual(change_display.money(Decimal('0.0123'), 'USD'), '$0.0123')
        self.assertEqual(change_display.money(Decimal('-1.5'), 'EUR'), '-1.50 EUR')
        self.assertIsNone(change_display.money(None, 'USD'))
