import contextlib
from urllib.parse import urlparse

from django.conf import settings
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from bom.mcp.auth import BearerTokenMiddleware
from bom.mcp.server import mcp


def build_mcp_app(path='/mcp'):
    """The authenticated MCP ASGI app. Register it in the host with ``mcp_routes(path)`` (a ``Mount`` would strip
    the prefix and skip the bare path).

    The host app's lifespan must enter ``mcp.session_manager.run()``; Starlette never runs a sub-app's
    lifespan. Use ``mcp_lifespan`` for that.
    """
    inner = mcp.streamable_http_app(
        streamable_http_path=path, stateless_http=True, json_response=True,
        transport_security=_transport_security())
    return inner if mcp.settings.auth else BearerTokenMiddleware(inner)


def mcp_routes(path='/mcp'):
    """The MCP endpoint, plus the RFC 9728 protected resource metadata route when OAuth is configured."""
    app = build_mcp_app(path)
    # No GET: a stateless server never pushes messages, so a client's SSE stream would idle until the proxy times it out.
    routes = [Route(path, endpoint=app, methods=['POST', 'DELETE'])]
    if mcp.settings.auth:
        metadata_path = urlparse(str(build_resource_metadata_url(mcp.settings.auth.resource_server_url))).path
        routes.append(Route(metadata_path, endpoint=app, methods=['GET', 'OPTIONS']))
    return routes


@contextlib.asynccontextmanager
async def mcp_lifespan(app):
    async with mcp.session_manager.run():
        yield


def _transport_security():
    """Reuse Django's ALLOWED_HOSTS for the SDK's DNS-rebinding protection, which defaults to localhost only."""
    allowed_hosts = list(getattr(settings, 'ALLOWED_HOSTS', []))
    if '*' in allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    # The SDK's "host:*" pattern only matches a Host header that carries a port, and proxies like Cloud Run send none.
    hosts = [pattern for host in allowed_hosts for pattern in (host.lstrip('.'), f"{host.lstrip('.')}:*")]
    hosts += ['localhost:*', '127.0.0.1:*']
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts,
        allowed_origins=[f"https://{host}" for host in allowed_hosts] + ['http://localhost:*', 'http://127.0.0.1:*'])


def build_application(django_app=None):
    """Standalone ASGI entrypoint: the MCP server at /mcp beside the Django app (when given)."""
    routes = mcp_routes('/mcp')
    if django_app is not None:
        routes.append(Mount('/', app=django_app))
    return Starlette(routes=routes, lifespan=mcp_lifespan)
