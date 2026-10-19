import json

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from starlette.testclient import TestClient

from bom import constants
from bom.helpers import create_a_fake_organization, create_some_fake_parts
from bom.mcp.asgi import build_application
from bom.models import APIToken, Part, PartRevision, Seller

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
class MCPServerTest(TransactionTestCase):
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
        self.app = build_application()

    def mcp(self, plaintext):
        return MCPClient(self.http, plaintext)

    def run(self, result=None):
        with TestClient(build_application()) as http:
            self.http = http
            return super().run(result)

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
        self.assertEqual(result['unit_cost'], '1.25')
        self.assertTrue(Seller.objects.filter(organization=self.organization, name='Digi-Key').exists())

    def test_form_errors_reach_the_model(self):
        _, error = self.mcp(self.write_plaintext).call('create_part', description='x', part_class_code='999')
        self.assertIn('number_class', error)
