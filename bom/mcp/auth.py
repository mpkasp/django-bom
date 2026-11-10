import hashlib
import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections
from django.utils import timezone
from django.utils.module_loading import import_string
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from starlette.datastructures import Headers

from bom.models import APIToken

logger = logging.getLogger(__name__)


def oauth_auth_settings():
    """The SDK auth settings when the host runs an OAuth authorization server, else ``None`` (pasted tokens only)."""
    issuer_url = settings.BOM_CONFIG.get('mcp_oauth_issuer_url')
    if not issuer_url:
        return None
    resource_url = settings.BOM_CONFIG.get('mcp_resource_url')
    if not resource_url:
        raise ImproperlyConfigured("BOM_CONFIG['mcp_resource_url'] is required with 'mcp_oauth_issuer_url'.")
    return AuthSettings(
        issuer_url=issuer_url, resource_server_url=resource_url,
        required_scopes=[APIToken.SCOPE_READ], validate_token_resource=True)


class ConnectionTokenVerifier:
    """Accepts pasted ``ibom_`` tokens and, when django-oauth-toolkit is installed, its access tokens.

    Both resolve to an ``APIToken``, whose id travels in the SDK ``AccessToken`` claims for ``Actor``.
    """

    def __init__(self, resource_url):
        self.resource_url = str(resource_url)

    async def verify_token(self, token):
        return await sync_to_async(self._verify, thread_sensitive=False)(token)

    def _verify(self, token):
        close_old_connections()
        try:
            if token.startswith(APIToken.PLAINTEXT_PREFIX):
                return self._verify_pasted_token(token)
            if 'oauth2_provider' in settings.INSTALLED_APPS:
                return self._verify_oauth_token(token)
            return None
        finally:
            close_old_connections()

    def _verify_pasted_token(self, plaintext):
        api_token = APIToken.authenticate(plaintext)
        if api_token is None:
            return None
        return self._access_token(
            plaintext, api_token, client_id=f'api-token-{api_token.id}', scopes=api_token.scope_list(),
            expires_at=api_token.expires_at)

    def _verify_oauth_token(self, token):
        from oauth2_provider.models import get_access_token_model

        toolkit_token = get_access_token_model().objects.select_related('application', 'user').filter(
            token_checksum=hashlib.sha256(token.encode()).hexdigest()).first()
        # Unrestricted tokens (no resource) are refused: any token must be bound to this server.
        if (toolkit_token is None or toolkit_token.application is None or toolkit_token.user is None
                or not toolkit_token.user.is_active or toolkit_token.is_expired()
                or not toolkit_token.resource or not toolkit_token.allows_audience(self.resource_url)):
            return None
        organization = toolkit_token.user.bom_profile().organization
        if organization is None:
            return None
        scopes = [scope for scope in toolkit_token.scope.split() if scope in dict(APIToken.SCOPES)]
        client_id = toolkit_token.application.client_id
        api_token = APIToken.connect(
            toolkit_token.user, organization, client_id, toolkit_token.application.name or client_id, scopes,
            granted_at=toolkit_token.created)
        if api_token is None:
            return None
        APIToken.objects.filter(pk=api_token.pk).update(last_used_at=timezone.now())
        return self._access_token(
            token, api_token, client_id=client_id, scopes=scopes, expires_at=toolkit_token.expires)

    def _access_token(self, token, api_token, client_id, scopes, expires_at):
        return AccessToken(
            token=token, client_id=client_id, scopes=scopes, resource=self.resource_url,
            expires_at=int(expires_at.timestamp()) if expires_at else None,
            subject=str(api_token.user_id), claims={'api_token_id': api_token.id})


class BearerTokenMiddleware:
    """Pure ASGI middleware for hosts without an OAuth server: resolve the bearer token to an ``APIToken`` and
    stash it on the request state.

    Rejects the request with 401 when the token is missing, unknown, expired, or revoked, so tools never
    run unauthenticated.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        authorization = Headers(scope=scope).get('authorization', '')
        plaintext = authorization[len('Bearer '):] if authorization.startswith('Bearer ') else ''
        token = await sync_to_async(_authenticate, thread_sensitive=False)(plaintext)
        if token is None:
            return await _unauthorized(send)

        scope.setdefault('state', {})['api_token'] = token
        return await self.app(scope, receive, send)


def _authenticate(plaintext):
    close_old_connections()
    try:
        return APIToken.authenticate(plaintext)
    finally:
        close_old_connections()


async def _unauthorized(send):
    body = json.dumps({'error': 'invalid_token', 'error_description': 'A valid bearer token is required.'}).encode()
    await send({
        'type': 'http.response.start',
        'status': 401,
        'headers': [
            (b'content-type', b'application/json'),
            (b'www-authenticate', b'Bearer error="invalid_token"'),
            (b'content-length', str(len(body)).encode()),
        ],
    })
    await send({'type': 'http.response.body', 'body': body})


class Actor:
    """Who a tool call acts as: the token's user, their organization, and the token's scopes."""

    def __init__(self, token, agent_client=''):
        self.token = token
        self.user = token.user
        self.organization = token.organization
        self.agent_client = agent_client

    @classmethod
    def from_context(cls, ctx):
        token = _token_from_request(ctx.request_context.request) or _token_from_access_token(get_access_token())
        if token is None:
            raise ToolError('Not authenticated.')
        return cls(token, agent_client=_client_name(ctx))

    def token_level(self):
        return self.token.level()

    def require_scope(self, scope):
        if not self.token.has_scope(scope):
            raise ToolError(f"This connection does not have the {scope!r} scope. The user can grant it by reconnecting "
                            f"and choosing a higher access level.")

    def require_permission(self, permission, obj=None):
        if not self.user.has_perm(permission, obj if obj is not None else self.organization):
            raise ToolError(f"{self.user.username} does not have permission {permission!r} in {self.organization.name}.")

    def require_write(self, permission, obj=None):
        self.require_scope(APIToken.SCOPE_WRITE)
        self.require_permission(permission, obj)
        self._require_host_access()

    def require_release(self):
        """Releasing and approving need the release level. The services check the person's own permission and the
        approval rules, so the agent is bound exactly as its person is."""
        self.require_scope(APIToken.SCOPE_WRITE)
        self.require_scope(APIToken.SCOPE_RELEASE)
        self._require_host_access()

    def _require_host_access(self):
        denial = access_denial(self.organization, self.user, self.token.scope_list())
        if denial:
            raise ToolError(denial)


def _token_from_request(request):
    return getattr(getattr(request, 'state', None), 'api_token', None)


def _token_from_access_token(access_token):
    token_id = (access_token.claims or {}).get('api_token_id') if access_token else None
    if token_id is None:
        return None
    close_old_connections()
    return APIToken.objects.select_related('user', 'organization').filter(id=token_id).first()


def _client_name(ctx):
    """The MCP client's self-reported name, when the transport exposes it (stateless requests may not)."""
    client_params = getattr(ctx.session, 'client_params', None)
    client_info = getattr(client_params, 'client_info', None) or getattr(client_params, 'clientInfo', None)
    return getattr(client_info, 'name', '') or ''


def access_denial(organization, user, scopes):
    """The host's reason to refuse these scopes (e.g. a subscription check), or ``None``."""
    return _access_check()(organization, user, scopes)


def _access_check():
    """Host-provided gate, e.g. a subscription check, as a callable or its dotted path (settings load before
    models, so hosts usually give the path). django-bom itself never gates MCP access."""
    check = settings.BOM_CONFIG.get('mcp_access_check')
    if isinstance(check, str):
        check = import_string(check)
    return check or (lambda organization, user, scopes: None)
