import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from mcp.server.mcpserver.exceptions import ToolError
from starlette.datastructures import Headers

from bom.models import APIToken

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

    def __init__(self, token):
        self.token = token
        self.user = token.user
        self.organization = token.organization

    @classmethod
    def from_context(cls, ctx):
        request = ctx.request_context.request
        token = getattr(getattr(request, 'state', None), 'api_token', None)
        if token is None:
            raise ToolError('Not authenticated.')
        return cls(token)

    def require_scope(self, scope):
        if not self.token.has_scope(scope):
            raise ToolError(f"This token does not have the {scope!r} scope.")

    def require_permission(self, permission, obj=None):
        if not self.user.has_perm(permission, obj if obj is not None else self.organization):
            raise ToolError(f"{self.user.username} does not have permission {permission!r} in {self.organization.name}.")

    def require_write(self, permission, obj=None):
        self.require_scope(APIToken.SCOPE_WRITE)
        self.require_permission(permission, obj)
        denial = _access_check()(self.organization, self.user, self.token.scope_list())
        if denial:
            raise ToolError(denial)


def _access_check():
    """Host-provided gate, e.g. a subscription check; django-bom itself never gates MCP access."""
    return settings.BOM_CONFIG.get('mcp_access_check') or (lambda organization, user, scopes: None)
