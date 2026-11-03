import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from starlette.testclient import TestClient

from bom import constants
from bom.helpers import create_a_fake_organization, create_some_fake_parts
from bom.mcp.asgi import build_application
from bom.services import changes, parts
from bom.models import APIToken, Change, Part, PartRevision, PartRevisionPropertyDefinition, Seller

def deny_everything(organization, user, scopes):
    return 'Upgrade to Pro.'


MCP_HEADERS = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}


class MCPClient:
    """Drives the MCP ASGI app over JSON-RPC the way a stateless HTTP client does."""

    def __init__(self, client, token=None):
        self.client = client
        self.token = token
        self.next_id = 0

    def rpc(self, method, params=None):
        self.next_id += 1
        headers = dict(MCP_HEADERS)
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return self.client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params or {}})

    def call(self, tool, **arguments):
        """``(structured result, error text)``: exactly one of them is set."""
        response = self.rpc('tools/call', {'name': tool, 'arguments': arguments})
        assert response.status_code == 200, response.text
        result = response.json()['result']
        if result.get('isError'):
            return None, result['content'][0]['text']
        if 'structuredContent' in result:
            structured = result['structuredContent']
        else:
            structured = json.loads(result['content'][0]['text'])
        if set(structured) == {'result'}:  # the SDK wraps list and scalar returns
            structured = structured['result']
        return structured, None


# TransactionTestCase: tool calls run on worker threads with their own DB connections, so rows must be committed.
@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class MCPTestCase(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('kasper', 'kasper@example.com', 'ghostpassword')
        self.organization = create_a_fake_organization(self.user)
        self.profile = self.user.bom_profile(organization=self.organization)
        self.profile.role = constants.ROLE_TYPE_ADMIN
        self.profile.save()
        self.parts = create_some_fake_parts(self.organization)
        self.read_token, self.read_plaintext = APIToken.issue(self.user, self.organization, 'read')
        self.write_token, self.write_plaintext = APIToken.issue(
            self.user, self.organization, 'write', scopes=[APIToken.SCOPE_READ, APIToken.SCOPE_WRITE])
        self.apply_token, self.apply_plaintext = APIToken.issue(
            self.user, self.organization, 'apply',
            scopes=[APIToken.SCOPE_READ, APIToken.SCOPE_WRITE, APIToken.SCOPE_APPLY])
        self.app = build_application()

    def mcp(self, plaintext):
        return MCPClient(self.http, plaintext)

    def run(self, result=None):
        with TestClient(build_application()) as http:
            self.http = http
            return super().run(result)


class MCPServerTest(MCPTestCase):
    def test_rejects_missing_unknown_expired_and_revoked_tokens(self):
        self.assertEqual(self.mcp(None).rpc('tools/list').status_code, 401)
        response = self.mcp('ibom_not-a-token').rpc('tools/list')
        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer', response.headers['www-authenticate'])

        self.read_token.revoke()
        self.assertEqual(self.mcp(self.read_plaintext).rpc('tools/list').status_code, 401)

    def test_lists_tools_and_records_last_use(self):
        response = self.mcp(self.read_plaintext).rpc('tools/list')
        self.assertEqual(response.status_code, 200)
        names = {tool['name'] for tool in response.json()['result']['tools']}
        self.assertIn('get_bom', names)
        self.assertIn('create_part', names)
        self.read_token.refresh_from_db()
        self.assertIsNotNone(self.read_token.last_used_at)

    def test_get_organization(self):
        result, error = self.mcp(self.read_plaintext).call('get_organization')
        self.assertIsNone(error)
        self.assertEqual(result['name'], self.organization.name)
        archibald = next(part_class for part_class in result['part_classes'] if part_class['code'] == '200')
        self.assertIn('voltage', {definition['code'] for definition in archibald['property_definitions']})

    def test_search_get_part_bom_and_where_used(self):
        pt1, pt2, pt3, pt4 = self.parts
        client = self.mcp(self.read_plaintext)

        result, _ = client.call('search_parts', query='STM32F401CEU6')
        self.assertEqual([part['part_number'] for part in result], [pt1.full_part_number()])

        result, _ = client.call('get_part', part_number=pt1.full_part_number())
        self.assertEqual(result['manufacturer_part_number'], 'STM32F401CEU6')
        self.assertTrue(result['manufacturer_parts'][0]['seller_parts'])

        result, _ = client.call('get_bom', part_number=pt3.full_part_number(), quantity=10, flat=True)
        self.assertEqual(result['quantity'], 10)
        self.assertTrue({pt1.full_part_number(), pt2.full_part_number()} <= {line['part_number'] for line in result['items']})

        result, _ = client.call('price_bom', part_number=pt3.full_part_number(), quantity=10)
        self.assertIn('out_of_pocket_cost', result)

        result, _ = client.call('where_used', part_number=pt1.full_part_number())
        self.assertIn(pt3.full_part_number(), {line['part_number'] for line in result})

    def test_cannot_reach_another_organizations_parts(self):
        other_user = User.objects.create_user('other', 'other@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(other_user)
        other_user.bom_profile(organization=other_organization)
        _, other_plaintext = APIToken.issue(other_user, other_organization, 'read')
        pt1 = self.parts[0]

        result, error = self.mcp(other_plaintext).call('get_part', part_number=pt1.full_part_number())
        self.assertIsNone(result)
        self.assertIn('No part', error)
        result, _ = self.mcp(other_plaintext).call('search_parts', query='STM32F401CEU6')
        self.assertEqual(result, [])

    def test_read_scope_cannot_write(self):
        result, error = self.mcp(self.read_plaintext).call('create_part', description='x', part_class_code='500')
        self.assertIsNone(result)
        self.assertIn("'write' scope", error)
        self.assertFalse(PartRevision.objects.filter(description='x').exists())

    def test_viewer_role_cannot_write_even_with_write_scope(self):
        self.profile.role = constants.ROLE_TYPE_VIEWER
        self.profile.save()
        _, error = self.mcp(self.write_plaintext).call('create_part', description='x', part_class_code='500')
        self.assertIn('permission', error)

    def test_host_access_check_can_deny_writes(self):
        config = dict(settings.BOM_CONFIG_DEFAULT, mcp_access_check='bom.test_mcp.deny_everything')
        with override_settings(BOM_CONFIG=config):
            _, error = self.mcp(self.write_plaintext).call('create_part', description='x', part_class_code='500')
        self.assertIn('Upgrade to Pro.', error)

    def test_create_part_add_subpart_and_sourcing(self):
        client = self.mcp(self.apply_plaintext)
        part_class = self.parts[0].number_class

        result, error = client.call(
            'create_part', description='Battery powered cap', part_class_code=str(part_class.code),
            properties={'sheen': 'matte', 'voltage': '3.3'}, property_units={'voltage': 'V'},
            manufacturer='Acme', manufacturer_part_number='CAP-001')
        self.assertIsNone(error, error)
        cap = Part.from_part_number(result['part_number'], self.organization)
        self.assertIn({'code': 'voltage', 'name': 'Voltage', 'value': '3.3', 'unit': 'V'}, result['properties'])
        self.assertEqual(cap.primary_manufacturer_part.manufacturer_part_number, 'CAP-001')
        self.assertEqual(result['configuration'], 'Draft')

        result, error = client.call(
            'add_subpart', assembly_part_number=cap.full_part_number(),
            subpart_part_number=self.parts[0].full_part_number(), count=2, reference='U1, U2')
        self.assertIsNone(error, error)
        subpart_line = next(line for line in result['items'] if line['part_number'] == self.parts[0].full_part_number())
        self.assertEqual(subpart_line['references'], 'U1, U2')
        self.assertEqual(subpart_line['quantity'], 2)

        _, error = client.call('add_subpart', assembly_part_number=cap.full_part_number(),
                               subpart_part_number=cap.full_part_number())
        self.assertIn('recursion', error.lower())

        result, error = client.call('add_manufacturer_part', part_number=cap.full_part_number(),
                                    manufacturer='Acme', manufacturer_part_number='CAP-001-ALT')
        self.assertIsNone(error, error)
        result, error = client.call('add_seller_part', manufacturer_part_id=result['id'], seller='Digi-Key',
                                    unit_cost='1.25', minimum_order_quantity=10)
        self.assertIsNone(error, error)
        self.assertEqual(Decimal(result['unit_cost']), Decimal('1.25'))
        self.assertTrue(Seller.objects.filter(organization=self.organization, name='Digi-Key').exists())

    def test_form_errors_reach_the_model(self):
        _, error = self.mcp(self.write_plaintext).call('create_part', description='x', part_class_code='999')
        self.assertIn('number_class', error)


class MCPExportBomCsvTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.assembly = self.parts[2]
        self.assembly_revision = self.assembly.latest()

    def view_csv(self, route, **query):
        self.client.force_login(self.user)
        url = reverse(route, kwargs={'part_revision_id': self.assembly_revision.id})
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_layouts_match_the_ui_export(self):
        client = self.mcp(self.read_plaintext)
        expected = {
            'indented': self.view_csv('bom:part-revision-export-bom'),
            'single_level': self.view_csv('bom:part-revision-export-bom', levels=1),
            'flat': self.view_csv('bom:part-revision-export-bom-flat'),
        }

        for layout, expected_csv in expected.items():
            result, error = client.call(
                'export_bom_csv', part_number=self.assembly.full_part_number(), layout=layout)
            self.assertIsNone(error, error)
            self.assertEqual(result, expected_csv, layout)
        self.assertNotEqual(expected['indented'], expected['flat'])

    def test_a_named_revision_and_an_unknown_layout(self):
        client = self.mcp(self.read_plaintext)

        result, error = client.call('export_bom_csv', part_number=self.assembly.full_part_number(),
                                    revision=self.assembly_revision.revision)
        self.assertIsNone(error, error)
        self.assertIn(self.parts[0].full_part_number(), result)

        _, error = client.call('export_bom_csv', part_number=self.assembly.full_part_number(), layout='tree')
        self.assertIn('Valid layouts: indented, single_level, flat', error)
        _, error = client.call('export_bom_csv', part_number=self.assembly.full_part_number(), revision='ZZ')
        self.assertIn('no revision', error)

    def test_another_organization_cannot_export(self):
        other_user = User.objects.create_user('other', 'other@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(other_user)
        other_user.bom_profile(organization=other_organization)
        _, other_plaintext = APIToken.issue(other_user, other_organization, 'read')

        result, error = self.mcp(other_plaintext).call('export_bom_csv', part_number=self.assembly.full_part_number())

        self.assertIsNone(result)
        self.assertIn('No part', error)


class MCPRevisionToolsTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        PartRevisionPropertyDefinition.objects.update(required=False)
        self.revision = parts.create_part(
            self.organization, 'Subject', part_class_code=str(self.parts[0].number_class.code))
        self.part_number = self.revision.part.full_part_number()

    def start_change(self, plaintext):
        result, error = self.mcp(plaintext).call('start_change', title='Edit subject', rationale='Because')
        self.assertIsNone(error, error)
        return result['change_id']

    def property_values(self):
        # The revision form stores an unchecked boolean property as 'False', so 'custom' appears after any save.
        return {prop.property_definition.code: prop.value_raw for prop in self.revision.properties.all()
                if prop.property_definition.code != 'custom'}

    def release(self):
        PartRevision.objects.filter(pk=self.revision.pk).update(configuration=constants.CONFIGURATION_TYPE_RELEASED)

    def test_update_part_revision_stages_then_applies(self):
        client = self.mcp(self.apply_plaintext)
        change_id = self.start_change(self.apply_plaintext)

        staged, error = client.call(
            'update_part_revision', part_number=self.part_number, change_id=change_id,
            description='Renamed', properties={'sheen': 'matte', 'voltage': '3.3'})

        self.assertIsNone(error, error)
        self.assertEqual(staged['staged']['kind'], 'update_part_revision')
        self.assertIn('Update description and properties', staged['staged']['summary'])
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')

        applied, error = client.call('apply_change', change_id=change_id)

        self.assertIsNone(error, error)
        self.assertEqual(applied['status'], 'applied')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Renamed')
        self.assertEqual(self.property_values(), {'sheen': 'matte', 'voltage': '3.3'})
        self.assertIn('Renamed', self.revision.displayable_synopsis)
        self.assertIn('matte', self.revision.displayable_synopsis)

    def test_update_part_revision_keeps_omitted_values_and_clears_empty_ones(self):
        client = self.mcp(self.apply_plaintext)
        change_id = self.start_change(self.apply_plaintext)
        client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                    properties={'sheen': 'matte', 'voltage': '3.3'})
        client.call('apply_change', change_id=change_id)

        change_id = self.start_change(self.apply_plaintext)
        _, error = client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                               properties={'sheen': ''})
        self.assertIsNone(error, error)
        client.call('apply_change', change_id=change_id)

        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')
        self.assertEqual(self.property_values(),
                         {'voltage': '3.3'})

    def test_update_part_revision_validates_through_the_form(self):
        change_id = self.start_change(self.write_plaintext)
        client = self.mcp(self.write_plaintext)

        _, error = client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                               properties={'nonsense': 'x'})
        self.assertIn('Unknown properties', error)
        _, error = client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                               description='Subject')
        self.assertIn('already has those values', error)
        _, error = client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                               description='x' * 300)
        self.assertIn('description', error)

    def test_update_part_revision_on_an_immutable_revision_says_to_revise_first(self):
        self.release()
        change_id = self.start_change(self.write_plaintext)

        result, error = self.mcp(self.write_plaintext).call(
            'update_part_revision', part_number=self.part_number, change_id=change_id, description='Nope')

        self.assertIsNone(result)
        self.assertIn('revise_part', error)
        self.assertIn('Released', error)
        self.assertFalse(Change.objects.get(pk=change_id).items.exists())

    def test_a_propose_token_stages_and_a_person_applies(self):
        client = self.mcp(self.write_plaintext)
        change_id = self.start_change(self.write_plaintext)
        client.call('update_part_revision', part_number=self.part_number, change_id=change_id, description='Proposed')

        _, error = client.call('apply_change', change_id=change_id)
        self.assertIn("'apply' scope", error)
        submitted, error = client.call('submit_change', change_id=change_id)
        self.assertIsNone(error, error)
        self.assertEqual(submitted['status'], 'open')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')

        changes.apply_change(Change.objects.get(pk=change_id), self.user)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Proposed')

    def test_an_applied_update_can_be_reverted(self):
        client = self.mcp(self.apply_plaintext)
        change_id = self.start_change(self.apply_plaintext)
        client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                    description='Changed', properties={'sheen': 'matte'})
        client.call('apply_change', change_id=change_id)

        changes.revert_change(Change.objects.get(pk=change_id), self.user)

        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')
        self.assertEqual(self.property_values(), {})

    def test_revise_part_stages_the_next_draft_and_applies(self):
        self.release()
        client = self.mcp(self.apply_plaintext)
        change_id = self.start_change(self.apply_plaintext)

        staged, error = client.call('revise_part', part_number=self.part_number, change_id=change_id)

        self.assertIsNone(error, error)
        self.assertEqual(staged['staged']['kind'], 'create_part_revision')
        self.assertIn('Revise', staged['staged']['summary'])
        self.assertEqual(self.revision.part.revisions().count(), 1)

        change, error = client.call('apply_change', change_id=change_id)

        self.assertIsNone(error, error)
        self.assertFalse(change['is_eco'])
        new_revision = self.revision.part.latest()
        self.assertEqual(new_revision.revision, '2')
        self.assertEqual(new_revision.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.assertEqual(new_revision.description, 'Subject')
        self.assertEqual(change['items'][0]['result']['part_revision_id'], new_revision.id)
        self.assertEqual(Change.objects.get(pk=change_id).items.get().part_id, self.revision.part_id)

    def test_revise_part_copies_the_assembly_like_the_ui(self):
        assembly = self.parts[2]
        PartRevision.objects.filter(pk=assembly.latest().pk).update(
            configuration=constants.CONFIGURATION_TYPE_RELEASED)
        client = self.mcp(self.apply_plaintext)
        change_id = self.start_change(self.apply_plaintext)

        client.call('revise_part', part_number=assembly.full_part_number(), change_id=change_id)
        client.call('apply_change', change_id=change_id)

        revised, released = assembly.latest(), assembly.revisions().order_by('id').first()
        self.assertNotEqual(revised.id, released.id)
        self.assertEqual(sorted(revised.assembly.subparts.values_list('part_revision_id', 'count')),
                         sorted(assembly.revisions().get(pk=assembly.latest().pk).assembly.subparts
                                .values_list('part_revision_id', 'count')))
        self.assertTrue(revised.assembly.subparts.exists())

    def test_revise_part_refuses_when_a_newer_draft_exists(self):
        self.release()
        parts.create_next_revision(self.revision)
        change_id = self.start_change(self.write_plaintext)

        result, error = self.mcp(self.write_plaintext).call('revise_part', part_number=self.part_number,
                                                            change_id=change_id)

        self.assertIsNone(result)
        self.assertIn('already has a newer Draft, rev 2', error)

    def test_revise_part_refuses_a_part_with_only_a_draft(self):
        change_id = self.start_change(self.write_plaintext)

        _, error = self.mcp(self.write_plaintext).call('revise_part', part_number=self.part_number,
                                                       change_id=change_id)

        self.assertIn('no Released or In Review revision', error)

    def test_a_revise_that_was_overtaken_fails_when_applied(self):
        self.release()
        change_id = self.start_change(self.apply_plaintext)
        client = self.mcp(self.apply_plaintext)
        client.call('revise_part', part_number=self.part_number, change_id=change_id)
        parts.create_next_revision(self.revision)

        _, error = client.call('apply_change', change_id=change_id)

        self.assertIsNotNone(error)
        self.assertEqual(self.revision.part.revisions().count(), 2)

    def test_read_tokens_cannot_use_the_write_tools(self):
        change_id = self.start_change(self.write_plaintext)
        client = self.mcp(self.read_plaintext)

        for tool, arguments in (
                ('update_part_revision', {'part_number': self.part_number, 'change_id': change_id, 'description': 'x'}),
                ('revise_part', {'part_number': self.part_number, 'change_id': change_id})):
            result, error = client.call(tool, **arguments)
            self.assertIsNone(result)
            self.assertIn("'write' scope", error, tool)
        self.assertFalse(Change.objects.get(pk=change_id).items.exists())

    def test_viewers_cannot_stage_revisions(self):
        change_id = self.start_change(self.write_plaintext)
        self.profile.role = constants.ROLE_TYPE_VIEWER
        self.profile.save()

        _, error = self.mcp(self.write_plaintext).call(
            'update_part_revision', part_number=self.part_number, change_id=change_id, description='x')

        self.assertIn('permission', error)

    def test_another_organization_cannot_reach_the_part_or_the_change(self):
        self.release()
        other_user = User.objects.create_user('other', 'other@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(other_user)
        other_profile = other_user.bom_profile(organization=other_organization)
        other_profile.role = constants.ROLE_TYPE_ADMIN
        other_profile.save()
        _, other_plaintext = APIToken.issue(other_user, other_organization, 'write',
                                            scopes=[APIToken.SCOPE_READ, APIToken.SCOPE_WRITE])
        other_client = self.mcp(other_plaintext)
        change_id = self.start_change(self.write_plaintext)

        _, error = other_client.call('update_part_revision', part_number=self.part_number, change_id=change_id,
                                     description='Hijacked')
        self.assertIn('No part', error)

        own_change = other_client.call('start_change', title='Mine')[0]['change_id']
        _, error = other_client.call('revise_part', part_number=self.part_number, change_id=own_change)
        self.assertIn('No part', error)

        _, error = self.mcp(self.write_plaintext).call('revise_part', part_number=self.part_number,
                                                       change_id=own_change)
        self.assertIn('No change', error)


class MCPReleaseTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        PartRevisionPropertyDefinition.objects.update(required=False)
        self.revision = parts.create_part(
            self.organization, 'Subject', part_class_code=str(self.parts[0].number_class.code))
        self.request = changes.request_release_approval(self.revision, self.user)

    def test_there_is_no_release_tool(self):
        names = {tool['name'] for tool in self.mcp(self.apply_plaintext).rpc('tools/list').json()['result']['tools']}

        self.assertFalse({name for name in names if 'release' in name or 'approve' in name})

    def test_an_apply_token_cannot_approve_a_release_request(self):
        result, error = self.mcp(self.apply_plaintext).call('apply_change', change_id=self.request.id)

        self.assertIn('agent token cannot approve or release', error)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.configuration, constants.CONFIGURATION_TYPE_DRAFT)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, Change.STATUS_OPEN)

    def test_an_apply_token_cannot_approve_a_request_a_person_started(self):
        change = changes.start_change(self.organization, self.user, 'Human edit')
        changes.stage_item(change, 'add_subpart', {
            'assembly_part_number': self.revision.part.full_part_number(),
            'subpart_part_number': self.parts[0].full_part_number(), 'count': 1})
        changes.submit_change(change, self.user)

        result, error = self.mcp(self.apply_plaintext).call('apply_change', change_id=change.id)

        self.assertIn('agent token cannot approve or release', error)

    def test_an_apply_token_still_applies_its_own_draft_edit(self):
        result, error = self.mcp(self.apply_plaintext).call(
            'add_subpart', assembly_part_number=self.revision.part.full_part_number(),
            subpart_part_number=self.parts[0].full_part_number(), count=2)

        self.assertIsNone(error)
        self.assertEqual(self.revision.part.latest().configuration, constants.CONFIGURATION_TYPE_DRAFT)
