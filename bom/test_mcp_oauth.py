import base64
import hashlib
import json
import secrets
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

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
