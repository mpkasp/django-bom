import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from django.utils.module_loading import import_string
from mcp.server.mcpserver.exceptions import ToolError
from starlette.datastructures import Headers

from bom.models import APIToken
from bom.services.flags import mcp_apply_enabled

logger = logging.getLogger(__name__)


class BearerTokenMiddleware:
    """Pure ASGI middleware: resolve the bearer token to an ``APIToken`` and stash it on the request state.

    Rejects the request with 401 when the token is missing, unknown, expired, or revoked, so tools never
    run unauthenticated. OAuth access tokens (Phase B) plug in here alongside personal tokens.
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
        request = ctx.request_context.request
        token = getattr(getattr(request, 'state', None), 'api_token', None)
        if token is None:
            raise ToolError('Not authenticated.')
        return cls(token, agent_client=_client_name(ctx))

    def can_apply(self):
        return self.token.has_scope(APIToken.SCOPE_APPLY) and mcp_apply_enabled(self.organization)

    def token_level(self):
        if self.can_apply():
            return 'apply'
        return 'propose' if self.token.has_scope(APIToken.SCOPE_WRITE) else 'read'

    def require_scope(self, scope):
        if not self.token.has_scope(scope):
            raise ToolError(f"This token does not have the {scope!r} scope.")

    def require_permission(self, permission, obj=None):
        if not self.user.has_perm(permission, obj if obj is not None else self.organization):
            raise ToolError(f"{self.user.username} does not have permission {permission!r} in {self.organization.name}.")

    def require_write(self, permission, obj=None):
        self.require_scope(APIToken.SCOPE_WRITE)
        self.require_permission(permission, obj)
        self._require_host_access()

    def require_propose(self):
        self.require_scope(APIToken.SCOPE_WRITE)
        self._require_host_access()

    def require_apply(self):
        self.require_scope(APIToken.SCOPE_APPLY)
        if not mcp_apply_enabled(self.organization):
            raise ToolError(f"Applying changes through MCP is not enabled for {self.organization.name}.")
        self._require_host_access()

    def _require_host_access(self):
        denial = _access_check()(self.organization, self.user, self.token.scope_list())
        if denial:
            raise ToolError(denial)


def _client_name(ctx):
    """The MCP client's self-reported name, when the transport exposes it (stateless requests may not)."""
    client_params = getattr(ctx.session, 'client_params', None)
    client_info = getattr(client_params, 'client_info', None) or getattr(client_params, 'clientInfo', None)
    return getattr(client_info, 'name', '') or ''


def _access_check():
    """Host-provided gate, e.g. a subscription check, as a callable or its dotted path (settings load before
    models, so hosts usually give the path). django-bom itself never gates MCP access."""
    check = settings.BOM_CONFIG.get('mcp_access_check')
    if isinstance(check, str):
        check = import_string(check)
    return check or (lambda organization, user, scopes: None)
