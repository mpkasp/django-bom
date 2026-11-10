import importlib
import json
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from starlette.testclient import TestClient

from bom import constants
from bom.helpers import create_a_fake_organization, create_some_fake_parts
from bom.mcp.asgi import build_application
from bom.models import (APIToken, Change, ChangeItem, ChangeSettings, Part, PartRevision,
                        PartRevisionPropertyDefinition, Seller)
from bom.services import boms, change_orders, changes, parts
from bom.services.errors import ServiceError


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


RELEASE_SCOPES = [APIToken.SCOPE_READ, APIToken.SCOPE_WRITE, APIToken.SCOPE_RELEASE]


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
        self.release_token, self.release_plaintext = APIToken.issue(self.user, self.organization, 'release',
                                                                    scopes=RELEASE_SCOPES)
        self.app = build_application()

    def member(self, username, role=constants.ROLE_TYPE_ADMIN, scopes=RELEASE_SCOPES):
        """Another person in the organization, with a token of their own: ``(user, token, plaintext)``."""
        member = User.objects.create_user(username, f'{username}@example.com', 'ghostpassword')
        profile = member.bom_profile(organization=self.organization)
        profile.role = role
        profile.save()
        return (member, *APIToken.issue(member, self.organization, username, scopes=scopes))

    def new_part(self, description='Subject'):
        PartRevisionPropertyDefinition.objects.update(required=False)
        return parts.create_part(self.organization, description,
                                 part_class_code=str(self.parts[0].number_class.code))

    def released_part(self, description='Subject'):
        return changes.release_revision(self.new_part(description), self.user)

    def history(self, part):
        return [entry['change'] for entry in changes.part_history(part)]

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

    @override_settings(ALLOWED_HOSTS=['mcp.example.com'])
    def test_allows_configured_hosts_with_or_without_a_port(self):
        with TestClient(build_application()) as http:
            client = MCPClient(http, self.read_plaintext)
            for host, expected_status in [('mcp.example.com', 200), ('mcp.example.com:443', 200), ('evil.example.com', 421)]:
                http.headers['Host'] = host
                self.assertEqual(client.rpc('tools/list').status_code, expected_status, host)

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
        client = self.mcp(self.write_plaintext)
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


class MCPAccessLevelTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.draft = self.new_part()
        self.part_number = self.draft.part.full_part_number()

    def tools(self):
        return {tool['name']: tool for tool in self.mcp(self.read_plaintext).rpc('tools/list').json()['result']['tools']}

    def test_the_staged_edit_tools_are_gone_and_the_change_order_tools_are_listed(self):
        tools = self.tools()

        self.assertFalse(set(tools) & {'start_change', 'submit_change', 'apply_change', 'withdraw_change',
                                       'remove_change_item', 'get_change', 'list_changes'})
        self.assertLessEqual(
            {'create_change_order', 'add_to_change_order', 'remove_from_change_order', 'submit_change_order',
             'withdraw_change_order', 'get_change_order', 'list_change_orders', 'release_revision',
             'approve_change_order', 'reject_change_order'}, set(tools))
        for tool in tools.values():
            properties = tool['inputSchema'].get('properties', {})
            self.assertFalse({'change_id', 'ref'} & set(properties), tool['name'])

    def test_annotations_mark_reads_writes_and_the_destructive_tool(self):
        tools = self.tools()

        for name in ('get_change_order', 'list_change_orders', 'get_part'):
            self.assertTrue(tools[name]['annotations']['readOnlyHint'], name)
        for name in ('release_revision', 'approve_change_order', 'reject_change_order', 'create_change_order',
                     'add_subpart'):
            self.assertFalse(tools[name]['annotations']['readOnlyHint'], name)
            self.assertFalse(tools[name]['annotations']['destructiveHint'], name)
        self.assertTrue(tools['remove_subpart']['annotations']['destructiveHint'])

    def test_a_read_token_reads_and_is_refused_every_write(self):
        client = self.mcp(self.read_plaintext)
        organization, _ = client.call('get_organization')
        self.assertEqual(organization['token_level'], 'read')
        self.assertIsNone(client.call('list_change_orders')[1])

        writes = {
            'create_part': {'description': 'x'},
            'update_part_revision': {'part_number': self.part_number, 'description': 'x'},
            'revise_part': {'part_number': self.part_number},
            'add_subpart': {'assembly_part_number': self.part_number, 'subpart_part_number': self.part_number},
            'update_subpart': {'assembly_part_number': self.part_number, 'subpart_part_number': self.part_number},
            'remove_subpart': {'assembly_part_number': self.part_number, 'subpart_part_number': self.part_number},
            'upload_bom': {'assembly_part_number': self.part_number, 'csv_text': 'part_number\n'},
            'add_manufacturer_part': {'part_number': self.part_number, 'manufacturer': 'Acme',
                                      'manufacturer_part_number': 'X'},
            'add_seller_part': {'manufacturer_part_id': 1, 'seller': 'Digi-Key', 'unit_cost': '1'},
            'create_change_order': {'title': 'x', 'rationale': 'y'},
            'add_to_change_order': {'number': 1, 'part_number': self.part_number},
            'remove_from_change_order': {'number': 1, 'part_number': self.part_number},
            'submit_change_order': {'number': 1},
            'withdraw_change_order': {'number': 1},
            'release_revision': {'part_number': self.part_number},
            'approve_change_order': {'number': 1},
            'reject_change_order': {'number': 1, 'reason': 'no'},
        }
        for tool, arguments in writes.items():
            result, error = client.call(tool, **arguments)
            self.assertIsNone(result, tool)
            self.assertIn("'write' scope", error, tool)
        self.assertFalse(Change.objects.exists())

    def test_an_edit_token_edits_and_is_refused_releasing_and_approving(self):
        client = self.mcp(self.write_plaintext)
        organization, _ = client.call('get_organization')
        self.assertEqual(organization['token_level'], 'edit')

        _, error = client.call('update_part_revision', part_number=self.part_number, description='Renamed')
        self.assertIsNone(error, error)
        order, error = client.call('create_change_order', title='Rename', rationale='Because',
                                   revisions=[{'part_number': self.part_number}])
        self.assertIsNone(error, error)
        client.call('submit_change_order', number=order['number'])

        for tool, arguments in {
            'release_revision': {'part_number': self.part_number},
            'approve_change_order': {'number': order['number']},
            'reject_change_order': {'number': order['number'], 'reason': 'no'},
        }.items():
            result, error = client.call(tool, **arguments)
            self.assertIsNone(result, tool)
            self.assertIn("'release' scope", error, tool)
        detail, _ = client.call('get_change_order', number=order['number'])
        self.assertEqual(detail['status'], 'open')
        self.assertFalse(detail['approval']['can_approve'])
        self.assertIn('release level', detail['approval']['message'])

    def test_a_release_token_releases(self):
        client = self.mcp(self.release_plaintext)
        organization, _ = client.call('get_organization')
        self.assertEqual(organization['token_level'], 'release')

        result, error = client.call('release_revision', part_number=self.part_number, note='First build')

        self.assertIsNone(error, error)
        self.assertEqual(result['configuration'], 'Released')
        entry = self.history(self.draft.part)[0]
        self.assertEqual((entry.items.get().kind, entry.api_token, entry.rationale),
                         (ChangeItem.KIND_RELEASE_REVISION, self.release_token, 'First build'))

    def test_the_token_never_widens_the_persons_role(self):
        self.profile.role = constants.ROLE_TYPE_VIEWER
        self.profile.save()
        client = self.mcp(self.release_plaintext)

        _, error = client.call('update_part_revision', part_number=self.part_number, description='Renamed')
        self.assertIn('permission', error)
        _, error = client.call('create_change_order', title='x', rationale='y')
        self.assertIn('permission', error)
        _, error = client.call('release_revision', part_number=self.part_number)
        self.assertIn('cannot release revisions', error)
        self.assertEqual(PartRevision.objects.get(pk=self.draft.pk).configuration, constants.CONFIGURATION_TYPE_DRAFT)

    def test_host_access_check_can_deny_releasing(self):
        config = dict(settings.BOM_CONFIG_DEFAULT, mcp_access_check='bom.test_mcp.deny_everything')
        with override_settings(BOM_CONFIG=config):
            _, error = self.mcp(self.release_plaintext).call('release_revision', part_number=self.part_number)
        self.assertIn('Upgrade to Pro.', error)

    def test_the_migration_strips_the_apply_scope_and_grants_no_release(self):
        APIToken.objects.filter(pk=self.release_token.pk).update(scopes='read,write,apply')
        APIToken.objects.filter(pk=self.write_token.pk).update(scopes='read,apply')
        migration = importlib.import_module('bom.migrations.0072_apitoken_release_scope')

        migration.strip_apply_scope(apps, None)

        self.assertEqual(APIToken.objects.get(pk=self.release_token.pk).scope_list(), ['read', 'write'])
        self.assertEqual(APIToken.objects.get(pk=self.write_token.pk).scope_list(), ['read'])
        self.assertEqual(APIToken.objects.get(pk=self.read_token.pk).scope_list(), ['read'])
        organization, _ = self.mcp(self.release_plaintext).call('get_organization')
        self.assertEqual(organization['token_level'], 'edit')


class MCPDirectEditTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.revision = self.new_part()
        self.part = self.revision.part
        self.part_number = self.part.full_part_number()
        self.resistor = self.parts[0].full_part_number()

    def property_values(self):
        # The revision form stores an unchecked boolean property as 'False', so 'custom' appears after any save.
        return {prop.property_definition.code: prop.value_raw for prop in self.revision.properties.all()
                if prop.property_definition.code != 'custom'}

    def lines(self, part_revision=None):
        part_revision = part_revision or self.revision
        return {line.part_revision.part.full_part_number(): (line.count, line.reference)
                for line in part_revision.assembly.subparts.select_related('part_revision__part')}

    def release(self):
        return changes.release_revision(self.revision, self.user)

    def test_every_edit_is_saved_at_once_and_recorded_in_history_with_the_token(self):
        client = self.mcp(self.write_plaintext)

        created, error = client.call('create_part', description='Cap',
                                     part_class_code=str(self.parts[0].number_class.code))
        self.assertIsNone(error, error)
        cap = Part.from_part_number(created['part_number'], self.organization)
        calls = [
            ('update_part_revision', {'part_number': self.part_number, 'description': 'Renamed'}),
            ('add_subpart', {'assembly_part_number': self.part_number, 'subpart_part_number': self.resistor,
                             'count': 2, 'reference': 'R1, R2'}),
            ('update_subpart', {'assembly_part_number': self.part_number, 'subpart_part_number': self.resistor,
                                'count': 1, 'reference': 'R1'}),
            ('remove_subpart', {'assembly_part_number': self.part_number, 'subpart_part_number': self.resistor}),
            ('add_manufacturer_part', {'part_number': self.part_number, 'manufacturer': 'Acme',
                                       'manufacturer_part_number': 'SUB-1'}),
        ]
        for tool, arguments in calls:
            result, error = client.call(tool, **arguments)
            self.assertIsNone(error, f"{tool}: {error}")
        _, error = client.call('add_seller_part', manufacturer_part_id=result['id'], seller='Digi-Key',
                               unit_cost='1.25')
        self.assertIsNone(error, error)

        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Renamed')
        self.assertEqual(self.lines(), {})
        self.assertEqual(self.part.manufacturer_parts().get().seller_parts().get().unit_cost.amount, Decimal('1.25'))
        recorded = self.history(self.part)
        self.assertEqual(
            [entry.items.get().kind for entry in reversed(recorded)],
            ['update_part_revision', 'add_subpart', 'update_subpart', 'remove_subpart', 'add_manufacturer_part',
             'add_seller_part'])
        for entry in recorded + self.history(cap):
            self.assertTrue(entry.is_direct_edit)
            self.assertIsNone(entry.number)
            self.assertEqual((entry.author, entry.api_token, entry.status),
                             (self.user, self.write_token, Change.STATUS_APPLIED))
        self.assertEqual(self.history(cap)[0].items.get().kind, 'create_part')

    def test_history_records_what_the_web_app_records(self):
        client = self.mcp(self.write_plaintext)
        client.call('add_subpart', assembly_part_number=self.part_number, subpart_part_number=self.resistor,
                    count=2, reference='R1, R2')
        client.call('update_subpart', assembly_part_number=self.part_number, subpart_part_number=self.resistor,
                    count=3, reference='R1, R2, R3')
        client.call('update_part_revision', part_number=self.part_number, description='Renamed')

        renamed, updated, added = [entry.items.get() for entry in self.history(self.part)]
        label = f"{self.part_number} rev {self.revision.revision}"
        self.assertEqual(added.summary, f"Add {self.resistor} x2.0 R1, R2 to {label}")
        self.assertEqual(added.payload, {'subpart_part_number': self.resistor, 'count': 2.0, 'reference': 'R1, R2',
                                         'do_not_load': False})
        self.assertEqual(added.result['part_revision_id'], self.revision.id)
        self.assertEqual(updated.summary, f"Update {self.resistor} in {label}")
        self.assertEqual((updated.before['count'], updated.payload['count']), (2.0, 3.0))
        self.assertEqual(renamed.summary, f"Update description and properties of {label}")
        self.assertEqual((renamed.before['description'], renamed.payload), ('Subject', {'fields': ['description']}))
        self.assertEqual(renamed.part_revision, self.revision)

    def test_update_part_revision_keeps_omitted_values_and_clears_empty_ones(self):
        client = self.mcp(self.write_plaintext)

        result, error = client.call('update_part_revision', part_number=self.part_number,
                                    properties={'sheen': 'matte', 'voltage': '3.3'})
        self.assertIsNone(error, error)
        self.assertIn({'code': 'sheen', 'name': 'Sheen', 'value': 'matte', 'unit': None}, result['properties'])
        _, error = client.call('update_part_revision', part_number=self.part_number, properties={'sheen': ''})
        self.assertIsNone(error, error)

        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')
        self.assertEqual(self.property_values(), {'voltage': '3.3'})

    def test_update_part_revision_validates_through_the_form(self):
        client = self.mcp(self.write_plaintext)

        _, error = client.call('update_part_revision', part_number=self.part_number, properties={'nonsense': 'x'})
        self.assertIn('Unknown properties', error)
        _, error = client.call('update_part_revision', part_number=self.part_number, description='Subject')
        self.assertIn('already has those values', error)
        _, error = client.call('update_part_revision', part_number=self.part_number, description='x' * 300)
        self.assertIn('description', error)
        self.assertEqual(self.history(self.part), [])

    def test_a_released_revision_is_refused_with_the_hint_to_revise(self):
        boms.add_subpart(self.organization, self.revision, self.resistor, count=1)
        self.release()
        client = self.mcp(self.release_plaintext)
        csv_text = f"part_number,quantity\n{self.resistor},4\n"

        for tool, arguments in {
            'update_part_revision': {'part_number': self.part_number, 'description': 'Renamed'},
            'add_subpart': {'assembly_part_number': self.part_number, 'subpart_part_number': self.resistor},
            'update_subpart': {'assembly_part_number': self.part_number, 'subpart_part_number': self.resistor,
                               'count': 5},
            'remove_subpart': {'assembly_part_number': self.part_number, 'subpart_part_number': self.resistor},
            'upload_bom': {'assembly_part_number': self.part_number, 'csv_text': csv_text},
        }.items():
            result, error = client.call(tool, **arguments)
            self.assertIsNone(result, tool)
            self.assertIn(f"{self.part_number} rev 1 is Released and cannot be edited", error, tool)
            self.assertIn('revise_part', error, tool)
        _, error = client.call('add_manufacturer_part', part_number=self.part_number, manufacturer='Acme',
                               manufacturer_part_number='SUB-1')
        self.assertIn('Revise this part', error)

        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')
        self.assertEqual(self.lines(), {self.resistor: (1, '')})
        self.assertEqual(self.part.revisions().count(), 1)

    def test_an_old_revision_points_at_the_draft_that_replaces_it(self):
        changes.revise_revision(self.release(), self.user)

        _, error = self.mcp(self.write_plaintext).call(
            'update_part_revision', part_number=self.part_number, revision='1', description='Renamed')

        self.assertIn("Edit the part's Draft, rev 2, instead", error)

    def test_a_revision_under_review_names_the_change_order_that_locks_it(self):
        draft = changes.revise_revision(self.release(), self.user)
        order = change_orders.create_change_order(self.organization, self.user, 'Rename', 'Because', [draft])
        change_orders.submit(order, self.user)

        _, error = self.mcp(self.write_plaintext).call(
            'update_part_revision', part_number=self.part_number, description='Renamed')

        self.assertIn(f"change order #{order.number}", error)
        self.assertIn('locked', error)

    def test_revise_part_creates_the_draft_now_with_the_assembly_and_records_it(self):
        boms.add_subpart(self.organization, self.revision, self.resistor, count=2, reference='R1, R2')
        self.release()
        client = self.mcp(self.write_plaintext)

        result, error = client.call('revise_part', part_number=self.part_number)

        self.assertIsNone(error, error)
        self.assertEqual((result['revision'], result['configuration'], result['description']),
                         ('2', 'Draft', 'Subject'))
        draft = self.part.latest()
        self.assertEqual(self.lines(draft), {self.resistor: (2, 'R1, R2')})
        entry = self.history(self.part)[0]
        self.assertEqual((entry.items.get().kind, entry.api_token), ('create_part_revision', self.write_token))

        _, error = client.call('add_subpart', assembly_part_number=self.part_number,
                               subpart_part_number=self.parts[1].full_part_number())
        self.assertIsNone(error, error)
        self.assertEqual(len(self.lines(draft)), 2)
        self.assertEqual(len(self.lines()), 1)

    def test_revise_part_refuses_a_newer_draft_and_a_part_that_was_never_released(self):
        client = self.mcp(self.write_plaintext)
        _, error = client.call('revise_part', part_number=self.part_number)
        self.assertIn('no Released or In Review revision', error)

        self.release()
        client.call('revise_part', part_number=self.part_number)
        result, error = client.call('revise_part', part_number=self.part_number)
        self.assertIsNone(result)
        self.assertIn('already has a newer Draft, rev 2', error)
        self.assertEqual(self.part.revisions().count(), 2)

    def test_another_organization_cannot_reach_the_part(self):
        other_user = User.objects.create_user('other', 'other@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(other_user)
        other_profile = other_user.bom_profile(organization=other_organization)
        other_profile.role = constants.ROLE_TYPE_ADMIN
        other_profile.save()
        _, other_plaintext = APIToken.issue(other_user, other_organization, 'release', scopes=RELEASE_SCOPES)
        client = self.mcp(other_plaintext)

        for tool, arguments in {
            'update_part_revision': {'part_number': self.part_number, 'description': 'Hijacked'},
            'revise_part': {'part_number': self.part_number},
            'release_revision': {'part_number': self.part_number},
            'create_change_order': {'title': 'x', 'rationale': 'y', 'revisions': [{'part_number': self.part_number}]},
        }.items():
            _, error = client.call(tool, **arguments)
            self.assertIn('No part', error, tool)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.description, 'Subject')


class MCPUploadBomTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.resistor, self.capacitor = self.parts[0], self.parts[1]
        self.assembly_revision = self.new_part('Board')
        self.assembly_number = self.assembly_revision.part.full_part_number()

    def csv_text(self, *rows, header='part_number,quantity,reference'):
        return '\n'.join([header] + [','.join(str(cell) for cell in row) for row in rows]) + '\n'

    def lines(self):
        return {line.part_revision.part.full_part_number(): (line.count, line.reference)
                for line in self.assembly_revision.assembly.subparts.select_related('part_revision__part')}

    def upload(self, csv_text, mode='add', plaintext=None):
        return self.mcp(plaintext or self.write_plaintext).call(
            'upload_bom', assembly_part_number=self.assembly_number, csv_text=csv_text, mode=mode)

    def load_initial_lines(self):
        _, error = self.upload(self.csv_text(
            (self.resistor.full_part_number(), 2, '"R1, R2"'), (self.capacitor.full_part_number(), 1, '')))
        self.assertIsNone(error, error)

    def test_saves_every_line_and_reports_what_changed(self):
        result, error = self.upload(self.csv_text(
            (self.resistor.full_part_number(), 2, '"R1, R2"'), (self.capacitor.full_part_number(), 1, '')))

        self.assertIsNone(error, error)
        self.assertEqual((result['part_number'], result['revision']), (self.assembly_number, '1'))
        self.assertEqual([(line['kind'], line['subpart_part_number'], line['count']) for line in result['changed']],
                         [('add_subpart', self.resistor.full_part_number(), 2),
                          ('add_subpart', self.capacitor.full_part_number(), 1)])
        self.assertEqual(result['warnings'], [])
        self.assertEqual(self.lines(), {self.resistor.full_part_number(): (2, 'R1, R2'),
                                        self.capacitor.full_part_number(): (1, '')})
        recorded = self.history(self.assembly_revision.part)
        self.assertEqual([entry.items.get().kind for entry in recorded], ['add_subpart', 'add_subpart'])
        self.assertEqual({entry.api_token for entry in recorded}, {self.write_token})

    def test_add_mode_adds_to_existing_lines_and_replace_mode_makes_them_match(self):
        self.load_initial_lines()
        csv_text = self.csv_text((self.resistor.full_part_number(), 3, '"R3, R4, R5"'))

        added, error = self.upload(csv_text, mode='add')
        self.assertIsNone(error, error)
        self.assertEqual([(line['kind'], line['count']) for line in added['changed']], [('update_subpart', 5)])
        self.assertEqual(self.lines()[self.resistor.full_part_number()], (5, 'R1, R2, R3, R4, R5'))

        replaced, error = self.upload(csv_text, mode='replace')
        self.assertIsNone(error, error)
        self.assertEqual(sorted(line['kind'] for line in replaced['changed']), ['remove_subpart', 'update_subpart'])
        self.assertEqual(self.lines(), {self.resistor.full_part_number(): (3, 'R3, R4, R5')})

    def test_a_csv_with_errors_changes_nothing_and_lists_the_rows(self):
        result, error = self.upload(self.csv_text(
            (self.resistor.full_part_number(), 'many', ''), (self.capacitor.full_part_number(), 'lots', '')))

        self.assertIsNone(result)
        self.assertIn('2 errors; nothing was changed', error)
        self.assertIn('Row 2', error)
        self.assertIn('Row 3', error)
        _, error = self.upload('description\nBoard\n')
        self.assertIn('nothing was changed', error)
        self.assertEqual(self.lines(), {})

    def test_a_csv_that_needs_new_parts_is_refused_and_creates_nothing(self):
        part_count = Part.objects.filter(organization=self.organization).count()

        result, error = self.upload(self.csv_text((self.resistor.full_part_number(), 1, ''), ('200-9999-00', 1, '')))

        self.assertIsNone(result)
        self.assertIn('200-9999-00', error)
        self.assertIn('create_part', error)
        self.assertEqual(Part.objects.filter(organization=self.organization).count(), part_count)
        self.assertEqual(self.lines(), {})

    def test_a_csv_that_changes_nothing_and_unknown_modes_are_refused(self):
        self.load_initial_lines()
        same_lines = self.csv_text((self.resistor.full_part_number(), 2, '"R1, R2"'),
                                   (self.capacitor.full_part_number(), 1, ''))

        _, error = self.upload(same_lines, mode='replace')
        self.assertIn('makes no changes', error)
        _, error = self.upload(same_lines, mode='merge')
        self.assertIn('Valid modes: add, replace', error)

    def test_a_line_that_fails_undoes_the_lines_saved_before_it(self):
        self.load_initial_lines()
        before = self.lines()
        history_before = len(self.history(self.assembly_revision.part))

        with mock.patch('bom.services.edits.remove_subpart', side_effect=ServiceError('That line is stuck.')):
            result, error = self.upload(self.csv_text((self.resistor.full_part_number(), 3, '"R3, R4, R5"')),
                                        mode='replace')

        self.assertIsNone(result)
        self.assertIn('That line is stuck.', error)
        self.assertEqual(self.lines(), before)
        self.assertEqual(len(self.history(self.assembly_revision.part)), history_before)


class MCPChangeOrderTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.released = self.released_part('Bracket')
        self.part = self.released.part
        self.part_number = self.part.full_part_number()
        self.client_as_author = self.mcp(self.release_plaintext)

    def configuration(self, part_revision):
        return PartRevision.objects.get(pk=part_revision.pk).configuration

    def submitted_order(self, description='Bracket, longer'):
        """Revise the Released part, edit the Draft, and submit it in a change order, all through the tools."""
        client = self.client_as_author
        for tool, arguments in (
            ('revise_part', {'part_number': self.part_number}),
            ('update_part_revision', {'part_number': self.part_number, 'description': description}),
            ('create_change_order', {'title': 'Lengthen the bracket', 'rationale': 'It did not reach',
                                     'revisions': [{'part_number': self.part_number}]}),
        ):
            result, error = client.call(tool, **arguments)
            self.assertIsNone(error, f"{tool}: {error}")
        submitted, error = client.call('submit_change_order', number=result['number'])
        self.assertIsNone(error, error)
        return submitted

    def test_revise_edit_submit_and_a_different_person_approves(self):
        _, reviewer_token, reviewer_plaintext = self.member('reviewer')

        submitted = self.submitted_order()

        draft = self.part.latest()
        self.assertEqual((submitted['status'], submitted['author']), ('open', self.user.username))
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_IN_REVIEW)
        order = Change.objects.get(organization=self.organization, number=submitted['number'])
        self.assertEqual((order.api_token, order.rationale), (self.release_token, 'It did not reach'))

        detail, error = self.mcp(reviewer_plaintext).call('get_change_order', number=submitted['number'])
        self.assertIsNone(error, error)
        self.assertIn(f"/changes/{order.number}/", detail['url'])
        revision, = detail['revisions']
        self.assertEqual((revision['part_number'], revision['revision'], revision['state'], revision['base_revision']),
                         (self.part_number, '2', 'In Review', '1'))
        self.assertEqual(revision['diff'], [{
            'label': 'Revision', 'summary': 'Rev 1 to rev 2', 'before': {'description': 'Bracket'},
            'after': {'description': 'Bracket, longer'}}])
        self.assertIsInstance(revision['unit_cost_after'], str)
        self.assertIsInstance(revision['unit_cost_before'], str)
        self.assertEqual(revision['released_assemblies_using_base'], [])
        self.assertTrue(detail['approval']['can_approve'])

        approved, error = self.mcp(reviewer_plaintext).call('approve_change_order', number=submitted['number'])

        self.assertIsNone(error, error)
        self.assertEqual((approved['status'], approved['resolved_by']), ('applied', 'reviewer'))
        self.assertEqual(self.configuration(draft), constants.CONFIGURATION_TYPE_RELEASED)
        order.refresh_from_db()
        self.assertEqual(order.resolved_api_token, reviewer_token)
        self.assertIn(order, self.history(self.part))

    def test_an_agent_cannot_approve_what_its_person_wrote_or_edited(self):
        self.member('reviewer')
        submitted = self.submitted_order()

        detail, _ = self.client_as_author.call('get_change_order', number=submitted['number'])
        self.assertFalse(detail['approval']['can_approve'])
        self.assertIn('did not write or edit', detail['approval']['message'])
        self.assertIn('only available in IndaBOM', detail['approval']['message'])
        result, error = self.client_as_author.call('approve_change_order', number=submitted['number'])

        self.assertIsNone(result)
        self.assertIn('did not write or edit', error)
        self.assertEqual(self.configuration(self.part.latest()), constants.CONFIGURATION_TYPE_IN_REVIEW)

    def test_an_agent_cannot_approve_a_change_order_its_person_only_edited(self):
        author, _, author_plaintext = self.member('author')
        draft = changes.revise_revision(self.released, author)
        _, error = self.client_as_author.call('update_part_revision', part_number=self.part_number,
                                              description='Bracket, wider')
        self.assertIsNone(error, error)
        order = change_orders.create_change_order(self.organization, author, 'Widen', 'Because', [draft])
        change_orders.submit(order, author)

        _, error = self.client_as_author.call('approve_change_order', number=order.number)

        self.assertIn('did not write or edit', error)

    def test_an_agent_approves_its_persons_change_order_when_they_are_the_only_member(self):
        submitted = self.submitted_order()

        detail, _ = self.client_as_author.call('get_change_order', number=submitted['number'])
        self.assertTrue(detail['approval']['can_approve'])
        approved, error = self.client_as_author.call('approve_change_order', number=submitted['number'])

        self.assertIsNone(error, error)
        self.assertEqual(approved['status'], 'applied')
        self.assertEqual(self.configuration(self.part.latest()), constants.CONFIGURATION_TYPE_RELEASED)

    def test_a_rejected_change_order_is_fixed_and_submitted_again(self):
        _, _, reviewer_plaintext = self.member('reviewer')
        submitted = self.submitted_order('Brakcet')
        number = submitted['number']

        _, error = self.mcp(reviewer_plaintext).call('reject_change_order', number=number, reason=' ')
        self.assertIn('reason', error)
        rejected, error = self.mcp(reviewer_plaintext).call('reject_change_order', number=number, reason='Typo')
        self.assertIsNone(error, error)
        self.assertEqual((rejected['status'], rejected['rejection_reason']), ('rejected', 'Typo'))
        self.assertEqual(self.configuration(self.part.latest()), constants.CONFIGURATION_TYPE_DRAFT)

        _, error = self.client_as_author.call('update_part_revision', part_number=self.part_number,
                                              description='Bracket, longer')
        self.assertIsNone(error, error)
        resubmitted, error = self.client_as_author.call('submit_change_order', number=number)
        self.assertIsNone(error, error)
        self.assertEqual((resubmitted['number'], resubmitted['status'], resubmitted['rejection_reason']),
                         (number, 'open', ''))

    def test_revisions_are_added_and_removed_while_it_is_a_draft_and_only_its_author_submits_or_withdraws(self):
        _, _, other_plaintext = self.member('other')
        client = self.client_as_author
        plate = self.released_part('Plate').part.full_part_number()
        client.call('revise_part', part_number=self.part_number)
        order, error = client.call('create_change_order', title='Rework', rationale='Because')
        self.assertIsNone(error, error)
        number = order['number']
        self.assertEqual((order['status'], order['revisions']), ('draft', []))

        _, error = client.call('submit_change_order', number=number)
        self.assertIn('no revisions to release', error)
        _, error = client.call('add_to_change_order', number=number, part_number=plate)
        self.assertIn('has no Draft revision. Call revise_part', error)
        _, error = client.call('add_to_change_order', number=number, part_number=self.part_number, revision='1')
        self.assertIn('only a Draft revision', error)
        added, error = client.call('add_to_change_order', number=number, part_number=self.part_number)
        self.assertIsNone(error, error)
        self.assertEqual([(revision['part_number'], revision['revision']) for revision in added['revisions']],
                         [(self.part_number, '2')])
        _, error = client.call('create_change_order', title='Second', rationale='Because',
                               revisions=[{'part_number': self.part_number}])
        self.assertIn(f"already in change order #{number}", error)
        self.assertFalse(Change.objects.filter(title='Second').exists())

        _, error = client.call('remove_from_change_order', number=number, part_number=plate)
        self.assertIn('is not in change order', error)
        removed, error = client.call('remove_from_change_order', number=number, part_number=self.part_number)
        self.assertIsNone(error, error)
        self.assertEqual(removed['revisions'], [])
        client.call('add_to_change_order', number=number, part_number=self.part_number)

        for tool in ('submit_change_order', 'withdraw_change_order'):
            _, error = self.mcp(other_plaintext).call(tool, number=number)
            self.assertIn('Only the author', error, tool)
        client.call('submit_change_order', number=number)
        _, error = client.call('remove_from_change_order', number=number, part_number=self.part_number)
        self.assertIn('while it is a draft or after it was sent back', error)
        withdrawn, error = client.call('withdraw_change_order', number=number)
        self.assertIsNone(error, error)
        self.assertEqual(withdrawn['status'], 'withdrawn')
        self.assertEqual(self.configuration(self.part.latest()), constants.CONFIGURATION_TYPE_DRAFT)

    def test_list_change_orders_filters_by_status_and_leaves_out_saved_edits(self):
        submitted = self.submitted_order()
        self.client_as_author.call('create_change_order', title='Later', rationale='Because')
        client = self.mcp(self.read_plaintext)

        open_orders, error = client.call('list_change_orders')
        self.assertIsNone(error, error)
        self.assertEqual([(order['number'], order['title'], order['status']) for order in open_orders],
                         [(submitted['number'], 'Lengthen the bracket', 'open')])
        self.assertEqual(open_orders[0]['revisions'], [{'part_number': self.part_number, 'revision': '2'}])
        everything, _ = client.call('list_change_orders', status='all')
        self.assertEqual([order['title'] for order in everything], ['Later', 'Lengthen the bracket'])

    def test_the_diff_and_impact_cover_bom_lines_and_released_assemblies(self):
        resistor = self.parts[0].full_part_number()
        assembly = self.new_part('Board')
        boms.add_subpart(self.organization, assembly, self.part_number, count=1)
        changes.release_revision(assembly, self.user)
        client = self.client_as_author
        client.call('revise_part', part_number=self.part_number)
        client.call('add_subpart', assembly_part_number=self.part_number, subpart_part_number=resistor, count=2)
        order, error = client.call('create_change_order', title='Add a resistor', rationale='Because',
                                   revisions=[{'part_number': self.part_number}])

        self.assertIsNone(error, error)
        revision, = order['revisions']
        self.assertEqual([(entry['label'], entry['summary']) for entry in revision['diff']],
                         [('Add line', resistor)])
        self.assertEqual(revision['released_assemblies_using_base'],
                         [{'part_number': assembly.part.full_part_number(), 'revision': '1'}])
        self.assertNotEqual(revision['unit_cost_before'], revision['unit_cost_after'])

    def test_change_orders_can_be_turned_off_by_the_host(self):
        config = dict(settings.BOM_CONFIG_DEFAULT, change_orders_enabled=False)
        with override_settings(BOM_CONFIG=config):
            organization, _ = self.client_as_author.call('get_organization')
            _, error = self.client_as_author.call('create_change_order', title='x', rationale='y')

        self.assertFalse(organization['change_orders_enabled'])
        self.assertIn('Change orders are not enabled', error)

    def test_another_organization_cannot_reach_the_change_order(self):
        submitted = self.submitted_order()
        other_user = User.objects.create_user('other', 'other@example.com', 'ghostpassword')
        other_organization = create_a_fake_organization(other_user)
        other_profile = other_user.bom_profile(organization=other_organization)
        other_profile.role = constants.ROLE_TYPE_ADMIN
        other_profile.save()
        _, other_plaintext = APIToken.issue(other_user, other_organization, 'release', scopes=RELEASE_SCOPES)

        for tool, arguments in {'get_change_order': {}, 'approve_change_order': {}, 'withdraw_change_order': {},
                                'reject_change_order': {'reason': 'no'}}.items():
            _, error = self.mcp(other_plaintext).call(tool, number=submitted['number'], **arguments)
            self.assertIn('No change order', error, tool)
        self.assertEqual(Change.objects.get(number=submitted['number'], organization=self.organization).status,
                         Change.STATUS_OPEN)


class MCPReleaseRevisionTest(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.draft = self.new_part()
        self.part_number = self.draft.part.full_part_number()

    def configuration(self):
        return PartRevision.objects.get(pk=self.draft.pk).configuration

    def test_required_approval_refuses_a_direct_release_and_points_at_a_change_order(self):
        self.member('reviewer')
        ChangeSettings.objects.create(organization=self.organization, require_release_approval=True)
        client = self.mcp(self.release_plaintext)

        organization, _ = client.call('get_organization')
        result, error = client.call('release_revision', part_number=self.part_number)

        self.assertTrue(organization['release_approval_required'])
        self.assertIsNone(result)
        self.assertIn('requires approval to release', error)
        self.assertIn('create_change_order', error)
        self.assertEqual(self.configuration(), constants.CONFIGURATION_TYPE_DRAFT)

    def test_a_revision_in_a_change_order_is_released_only_by_approving_it(self):
        self.member('reviewer')
        order = change_orders.create_change_order(self.organization, self.user, 'First release', 'Because',
                                                  [self.draft])
        change_orders.submit(order, self.user)

        _, error = self.mcp(self.release_plaintext).call('release_revision', part_number=self.part_number)

        self.assertIn(f"is in change order #{order.number}", error)
        self.assertEqual(self.configuration(), constants.CONFIGURATION_TYPE_IN_REVIEW)

    def test_a_released_revision_cannot_be_released_again(self):
        client = self.mcp(self.release_plaintext)
        client.call('release_revision', part_number=self.part_number)

        _, error = client.call('release_revision', part_number=self.part_number)

        self.assertIn('only a Draft or In Review revision can be released', error)
