from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bom import constants
from bom.helpers import create_a_fake_organization, create_some_fake_parts, create_user_and_organization
from bom.models import Change, ChangeItem, ChangeSettings, ManufacturerPart, Part, PartRevision, SellerPart
from bom.services import boms, changes, parts
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

    def test_released_revision_is_an_eco_and_phase_one_refuses_to_apply_it(self):
        PartRevision.objects.filter(pk=self.assembly.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)
        client = self.mcp(self.apply_plaintext)
        assembly_number = self.assembly.part.full_part_number()

        _, error = client.call('add_subpart', assembly_part_number=assembly_number,
                               subpart_part_number=self.parts[0].full_part_number())
        self.assertIn('Engineering Change Order', error)
        self.assertEqual(Change.objects.get().status, Change.STATUS_WITHDRAWN)

        started, _ = client.call('start_change', title='Revise assembly', rationale='ECO')
        _, error = client.call('add_subpart', assembly_part_number=assembly_number,
                               subpart_part_number=self.parts[0].full_part_number(), change_id=started['change_id'])
        self.assertIsNone(error, error)
        detail, _ = client.call('get_change', change_id=started['change_id'])
        self.assertTrue(detail['is_eco'])
        self.assertTrue(detail['is_protected'])
        _, error = client.call('apply_change', change_id=started['change_id'])
        self.assertIn('Engineering Change Order', error)
        self.assertFalse(self.assembly.assembly and self.assembly.assembly.subparts.exists())

    def test_rejected_item_reason_is_visible_and_skipped_on_apply(self):
        client = self.mcp(self.apply_plaintext)
        started, _ = client.call('start_change', title='Two parts', rationale='Testing')
        change_id = started['change_id']
        self.stage_cap(client, change_id)
        client.call('create_part', description='Resistor', part_class_code=self.part_class_code,
                    properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'},
                    change_id=change_id)
        client.call('add_subpart', assembly_part_number=self.assembly.part.full_part_number(),
                    subpart_part_number='$ref:cap', change_id=change_id)

        change = Change.objects.get(id=change_id)
        cap_item = change.items.get(position=1)
        changes.set_item_status(change, cap_item, ChangeItem.STATUS_REJECTED, 'wrong footprint')
        detail, _ = client.call('get_change', change_id=change_id)
        self.assertEqual([item['status'] for item in detail['items']], ['rejected', 'accepted', 'rejected'])
        self.assertEqual(detail['items'][0]['reject_reason'], 'wrong footprint')
        self.assertIn('depends on rejected item 1', detail['items'][2]['reject_reason'])

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
        self.assertNotContains(self.client.get(reverse('bom:change-list') + '?filter=applied'), 'Add a cap')
        self.assertEqual(self.client.get(reverse('bom:change-list') + '?filter=ecos').status_code, 200)

    def test_review_page_renders_and_apply_post_applies(self):
        response = self.client.get(self.review_url())
        self.assertContains(response, 'Create part')
        self.assertContains(response, 'Any Editor or Admin can apply')

        response = self.client.post(self.review_url('change-apply'), follow=True)
        self.assertContains(response, 'applied')
        self.change.refresh_from_db()
        self.assertEqual(self.change.status, Change.STATUS_APPLIED)
        self.assertTrue(PartRevision.objects.filter(description='Cap').exists())

    def test_item_reject_with_reason_then_reject_change(self):
        item = self.change.items.get()
        self.client.post(reverse('bom:change-item-status', kwargs={'number': self.change.number, 'item_id': item.id}),
                         {'status': 'rejected', 'reason': 'no thanks'})
        item.refresh_from_db()
        self.assertEqual((item.status, item.reject_reason), ('rejected', 'no thanks'))
        self.assertContains(self.client.get(self.review_url()), 'no thanks')

        self.client.post(self.review_url('change-reject'), {'reason': 'Out of scope'})
        self.change.refresh_from_db()
        self.assertEqual(self.change.status, Change.STATUS_REJECTED)

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
