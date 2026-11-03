import base64
import hashlib
import json
import re
import secrets
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from mcp.server.auth.settings import AuthSettings
from starlette.testclient import TestClient
from oauth2_provider.models import AccessToken, Application, RefreshToken

from bom import constants
from bom.mcp.asgi import build_application
from bom.mcp.auth import ConnectionTokenVerifier
from bom.mcp.server import mcp
from bom.models import APIToken
from bom.test_mcp import MCPClient, MCPTestCase, deny_everything
from bom.views.views import editor_install_links

ISSUER_URL = 'http://testserver'
RESOURCE_URL = 'http://testserver/mcp'
METADATA_PATH = '/.well-known/oauth-protected-resource/mcp'
REDIRECT_URI = 'http://localhost:8765/callback'

OAUTH_SETTINGS = AuthSettings(
    issuer_url=ISSUER_URL, resource_server_url=RESOURCE_URL, required_scopes=['read'], validate_token_resource=True)


class OAuthMCPTestCase(MCPTestCase):
    def run(self, result=None):
        with mock.patch.object(mcp, '_token_verifier', ConnectionTokenVerifier(RESOURCE_URL)), \
                mock.patch.object(mcp.settings, 'auth', OAUTH_SETTINGS):
            return super().run(result)

    def make_application(self, name='Claude Code'):
        return Application.objects.create(
            name=name, client_id=f'client-{secrets.token_hex(4)}', client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE, redirect_uris=REDIRECT_URI)

    def make_toolkit_token(self, application, scope='read', resource=(RESOURCE_URL,), expires_in=3600, user=None):
        plaintext = secrets.token_urlsafe(24)
        AccessToken.objects.create(
            user=user or self.user, application=application, token=plaintext, scope=scope, resource=list(resource),
            expires=timezone.now() + timedelta(seconds=expires_in))
        return plaintext


class OAuthDiscoveryTest(OAuthMCPTestCase):
    def test_unauthenticated_request_points_at_resource_metadata(self):
        response = self.mcp(None).rpc('tools/list')
        self.assertEqual(response.status_code, 401)
        self.assertIn(f'resource_metadata="{ISSUER_URL}{METADATA_PATH}"', response.headers['www-authenticate'])

    def test_protected_resource_metadata(self):
        response = self.http.get(METADATA_PATH)
        self.assertEqual(response.status_code, 200)
        metadata = response.json()
        self.assertEqual(metadata['resource'], RESOURCE_URL)
        self.assertEqual([url.rstrip('/') for url in metadata['authorization_servers']], [ISSUER_URL])
        self.assertEqual(metadata['scopes_supported'], ['read'])

    def test_pasted_token_still_works(self):
        result, error = self.mcp(self.write_plaintext).call('get_organization')
        self.assertIsNone(error)
        self.assertEqual(result['token_level'], 'propose')
        self.assertEqual(APIToken.objects.count(), 3)

    def test_revoked_pasted_token_is_rejected(self):
        self.read_token.revoke()
        self.assertEqual(self.mcp(self.read_plaintext).rpc('tools/list').status_code, 401)


class OAuthWithoutIssuerTest(MCPTestCase):
    def test_pasted_tokens_only_and_no_metadata(self):
        with mock.patch.object(mcp.settings, 'auth', None), mock.patch.object(mcp, '_token_verifier', None):
            with TestClient(build_application()) as http:
                self.assertEqual(http.get(METADATA_PATH).status_code, 404)
                response = MCPClient(http, None).rpc('tools/list')
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('resource_metadata', response.headers['www-authenticate'])
                self.assertEqual(MCPClient(http, self.read_plaintext).rpc('tools/list').status_code, 200)


class OAuthTokenTest(OAuthMCPTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.make_application()

    def test_toolkit_token_creates_one_connection_and_reuses_it(self):
        plaintext = self.make_toolkit_token(self.application, scope='read write')
        client = self.mcp(plaintext)
        result, error = client.call('get_organization')
        self.assertIsNone(error)
        self.assertEqual(result['token_level'], 'propose')
        client.call('get_organization')

        connection = APIToken.objects.get(oauth_client_id=self.application.client_id)
        self.assertEqual((connection.user, connection.organization), (self.user, self.organization))
        self.assertEqual(connection.name, 'Claude Code')
        self.assertEqual(connection.scope_list(), ['read', 'write'])
        self.assertIsNotNone(connection.last_used_at)
        self.assertEqual(APIToken.objects.filter(oauth_client_id=self.application.client_id).count(), 1)

    def test_connection_cannot_be_pasted(self):
        self.mcp(self.make_toolkit_token(self.application)).rpc('tools/list')
        connection = APIToken.objects.get(oauth_client_id=self.application.client_id)
        self.assertEqual(connection.prefix, '')
        self.assertEqual(self.mcp(f'ibom_{connection.hashed_key}').rpc('tools/list').status_code, 401)

    def test_later_grant_with_different_scopes_updates_the_connection(self):
        self.mcp(self.make_toolkit_token(self.application, scope='read')).rpc('tools/list')
        self.mcp(self.make_toolkit_token(self.application, scope='read write apply')).rpc('tools/list')
        connection = APIToken.objects.get(oauth_client_id=self.application.client_id)
        self.assertEqual(connection.scope_list(), ['read', 'write', 'apply'])

    def test_rejects_wrong_missing_audience_and_expired_tokens(self):
        for token in (
            self.make_toolkit_token(self.application, resource=['http://testserver/other']),
            self.make_toolkit_token(self.application, resource=[]),
            self.make_toolkit_token(self.application, expires_in=-60),
            'not-a-token',
        ):
            self.assertEqual(self.mcp(token).rpc('tools/list').status_code, 401)
        self.assertFalse(APIToken.objects.filter(oauth_client_id=self.application.client_id).exists())

    def test_token_without_read_scope_is_forbidden(self):
        response = self.mcp(self.make_toolkit_token(self.application, scope='write')).rpc('tools/list')
        self.assertEqual(response.status_code, 403)

    def test_revoking_a_connection_deletes_the_clients_tokens(self):
        plaintext = self.make_toolkit_token(self.application)
        other_application = self.make_application('Cursor')
        other_plaintext = self.make_toolkit_token(other_application)
        RefreshToken.objects.create(
            user=self.user, application=self.application, token='refresh', access_token=AccessToken.objects.get(
                application=self.application))
        self.assertEqual(self.mcp(plaintext).rpc('tools/list').status_code, 200)

        APIToken.objects.get(oauth_client_id=self.application.client_id).revoke()

        self.assertEqual(self.mcp(plaintext).rpc('tools/list').status_code, 401)
        self.assertFalse(AccessToken.objects.filter(application=self.application).exists())
        self.assertFalse(RefreshToken.objects.filter(application=self.application).exists())
        self.assertEqual(self.mcp(other_plaintext).rpc('tools/list').status_code, 200)

    def test_token_issued_before_revocation_is_rejected_and_a_new_grant_reconnects(self):
        plaintext = self.make_toolkit_token(self.application)
        self.mcp(plaintext).rpc('tools/list')
        connection = APIToken.objects.get(oauth_client_id=self.application.client_id)
        APIToken.objects.filter(pk=connection.pk).update(revoked_at=timezone.now() + timedelta(seconds=5))
        self.assertEqual(self.mcp(plaintext).rpc('tools/list').status_code, 401)

        APIToken.objects.filter(pk=connection.pk).update(revoked_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.mcp(plaintext).rpc('tools/list').status_code, 200)
        self.assertEqual(APIToken.objects.filter(oauth_client_id=self.application.client_id).count(), 2)

    def test_access_check_still_gates_writes(self):
        plaintext = self.make_toolkit_token(self.application, scope='read write')
        with override_settings(BOM_CONFIG={**settings.BOM_CONFIG, 'mcp_access_check': deny_everything}):
            _, error = self.mcp(plaintext).call('create_part', description='A thing')
        self.assertIn('Upgrade to Pro.', error)

    def test_inactive_user_is_rejected(self):
        plaintext = self.make_toolkit_token(self.application)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.mcp(plaintext).rpc('tools/list').status_code, 401)


def radio_scopes(response):
    return re.findall(r'name="scope" type="radio" value="([^"]*)"', response.content.decode())


def checked_scope(response):
    return re.search(r'value="([^"]*)"\s+checked', response.content.decode()).group(1)


@override_settings(ROOT_URLCONF='bom.test_oauth_urls')
class ConsentPageTest(OAuthMCPTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.make_application()
        self.code_verifier = secrets.token_urlsafe(48)
        self.client.force_login(self.user)

    def authorize_params(self, scope='read write apply'):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.code_verifier.encode()).digest()).rstrip(b'=')
        return {
            'response_type': 'code', 'client_id': self.application.client_id, 'redirect_uri': REDIRECT_URI,
            'scope': scope, 'state': 'xyz', 'code_challenge': challenge.decode(), 'code_challenge_method': 'S256',
            'resource': RESOURCE_URL,
        }

    def consent_page(self, scope='read write apply'):
        return self.client.get('/o/authorize/', self.authorize_params(scope))

    def member(self, role):
        member = User.objects.create_user(f'member-{role}', f'{role}@example.com', 'ghostpassword')
        profile = member.bom_profile(organization=self.organization)
        profile.role = role
        profile.save()
        return member

    def test_admin_sees_every_level_and_the_requested_one_is_selected(self):
        response = self.consent_page()
        self.assertContains(response, 'Claude Code')
        self.assertContains(response, self.organization.name)
        self.assertEqual(radio_scopes(response), ['read', 'read write', 'read write apply'])
        self.assertEqual(checked_scope(response), 'read write apply')

    def test_default_is_the_narrower_of_the_request_and_the_ceiling(self):
        self.assertEqual(checked_scope(self.consent_page('read write')), 'read write')
        self.assertEqual(checked_scope(self.consent_page('read')), 'read')

    def test_viewer_can_only_grant_read(self):
        self.client.force_login(self.member(constants.ROLE_TYPE_VIEWER))
        response = self.consent_page()
        self.assertEqual(radio_scopes(response), ['read'])
        self.assertEqual(checked_scope(response), 'read')

    def test_apply_is_hidden_when_the_host_disables_it(self):
        config = {**settings.BOM_CONFIG, 'mcp_apply_enabled': False}
        with override_settings(BOM_CONFIG=config):
            response = self.consent_page()
        self.assertEqual(radio_scopes(response), ['read', 'read write'])
        self.assertEqual(checked_scope(response), 'read write')

    def test_writes_are_hidden_when_the_access_check_denies_them(self):
        config = {**settings.BOM_CONFIG, 'mcp_access_check': deny_everything}
        with override_settings(BOM_CONFIG=config):
            response = self.consent_page()
        self.assertEqual(radio_scopes(response), ['read'])

    def test_chosen_scope_is_what_the_client_is_granted(self):
        form = {**self.authorize_params(), 'scope': 'read write', 'allow': 'Authorize'}
        response = self.client.post('/o/authorize/', form)
        self.assertEqual(response.status_code, 302)
        redirect = urlparse(response['Location'])
        self.assertEqual(f'{redirect.scheme}://{redirect.netloc}{redirect.path}', REDIRECT_URI)
        code = parse_qs(redirect.query)['code'][0]

        token_response = self.client.post('/o/token/', {
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT_URI,
            'client_id': self.application.client_id, 'code_verifier': self.code_verifier, 'resource': RESOURCE_URL})
        self.assertEqual(token_response.status_code, 200, token_response.content)
        granted = token_response.json()
        self.assertEqual(granted['scope'], 'read write')

        result, error = self.mcp(granted['access_token']).call('get_organization')
        self.assertIsNone(error)
        self.assertEqual(result['token_level'], 'propose')

    def test_cancel_denies_access(self):
        response = self.client.post('/o/authorize/', {**self.authorize_params(), 'scope': 'read'})
        self.assertIn('error=access_denied', response['Location'])

    def test_user_without_an_organization_cannot_authorize(self):
        self.profile.organization = None
        self.profile.save()
        response = self.consent_page()
        self.assertNotContains(response, 'name="allow"')
        self.assertContains(response, 'organization')


@override_settings(ROOT_URLCONF='bom.test_oauth_urls')
class ConnectionManagementTest(OAuthMCPTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.make_application('Cursor')
        self.mcp(self.make_toolkit_token(self.application, scope='read write')).rpc('tools/list')
        self.connection = APIToken.objects.get(oauth_client_id=self.application.client_id)
        self.client.force_login(self.user)

    def test_settings_lists_connected_apps_apart_from_personal_tokens(self):
        personal_token, _ = APIToken.issue(self.connection.user, self.connection.organization, 'CI script')

        response = self.client.get('/settings/api/')

        self.assertContains(response, 'Connected apps')
        self.assertEqual(list(response.context['connected_apps']), [self.connection])
        self.assertIn(personal_token, response.context['personal_api_tokens'])
        self.assertNotIn(self.connection, response.context['personal_api_tokens'])

    def test_revoking_in_settings_revokes_the_clients_tokens(self):
        plaintext = self.make_toolkit_token(self.application)
        response = self.client.post('/settings/api/', {'submit-revoke-token': '', 'token_id': self.connection.id})
        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.revoked_at)
        self.assertFalse(AccessToken.objects.filter(application=self.application).exists())
        self.assertEqual(self.mcp(plaintext).rpc('tools/list').status_code, 401)


@override_settings(ROOT_URLCONF='bom.test_oauth_urls')
class ConnectQuickStartTest(OAuthMCPTestCase):
    OAUTH_CONFIG = {'mcp_oauth_issuer_url': ISSUER_URL, 'mcp_resource_url': RESOURCE_URL}

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def settings_page(self, **config):
        with override_settings(BOM_CONFIG={**settings.BOM_CONFIG, **config}):
            return self.client.get('/settings/api/')

    def test_shows_the_quick_start_until_an_assistant_connects(self):
        response = self.settings_page(**self.OAUTH_CONFIG)
        self.assertContains(response, 'Add custom connector')
        self.assertContains(response, f'claude mcp add --transport http indabom {RESOURCE_URL}')
        self.assertContains(response, 'Add to Cursor')
        self.assertNotContains(response, 'No connected apps yet.')

        self.mcp(self.make_toolkit_token(self.make_application('Cursor'))).rpc('tools/list')
        response = self.settings_page(**self.OAUTH_CONFIG)
        self.assertContains(response, 'Connect another assistant')

    def test_lists_connections_only_without_oauth(self):
        response = self.settings_page()
        self.assertContains(response, 'No connected apps yet.')
        self.assertNotContains(response, 'Add custom connector')

    def test_editor_install_links_carry_the_server_address(self):
        links = editor_install_links(RESOURCE_URL)
        cursor_config = parse_qs(urlparse(links['cursor']).query)['config'][0]
        self.assertEqual(json.loads(base64.b64decode(cursor_config)), {'url': RESOURCE_URL})
        vscode_config = unquote(links['vscode'].split('?', 1)[1])
        self.assertEqual(json.loads(vscode_config), {'name': 'indabom', 'type': 'http', 'url': RESOURCE_URL})


class HelpAIPageTest(OAuthMCPTestCase):
    def test_explains_sign_in_when_oauth_is_configured(self):
        config = {**settings.BOM_CONFIG, 'mcp_oauth_issuer_url': ISSUER_URL, 'mcp_resource_url': RESOURCE_URL}
        with override_settings(BOM_CONFIG=config):
            response = self.client.get('/help/ai/')
        self.assertContains(response, f'claude mcp add --transport http indabom {RESOURCE_URL}')
        self.assertContains(response, 'Authenticate')
        self.assertContains(response, 'Add custom connector')
        self.assertContains(response, 'Or paste a token')

    def test_explains_tokens_only_without_oauth(self):
        config = {**settings.BOM_CONFIG, 'site_url': 'https://bom.example.com/'}
        with override_settings(BOM_CONFIG=config):
            response = self.client.get('/help/ai/')
        self.assertContains(response, 'https://bom.example.com/mcp')
        self.assertContains(response, 'Authorization: Bearer')
        self.assertNotContains(response, 'Add custom connector')
        self.assertNotContains(response, 'Authenticate')

    def test_help_links_to_the_page(self):
        self.assertContains(self.client.get('/help/'), '/help/ai/')
