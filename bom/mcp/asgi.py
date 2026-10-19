import contextlib

from django.conf import settings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from bom.mcp.auth import BearerTokenMiddleware
from bom.mcp.server import mcp


def build_mcp_app(path='/mcp'):
    """The authenticated MCP ASGI app. Register it in the host as ``Route(path, endpoint=build_mcp_app(path))``
    (a ``Mount`` would strip the prefix and skip the bare path).

    The host app's lifespan must enter ``mcp.session_manager.run()``; Starlette never runs a sub-app's
    lifespan. Use ``mcp_lifespan`` for that.
    """
    inner = mcp.streamable_http_app(
        streamable_http_path=path, stateless_http=True, json_response=True,
        transport_security=_transport_security())
    return BearerTokenMiddleware(inner)


@contextlib.asynccontextmanager
async def mcp_lifespan(app):
    async with mcp.session_manager.run():
        yield


def _transport_security():
    """Reuse Django's ALLOWED_HOSTS for the SDK's DNS-rebinding protection, which defaults to localhost only."""
    allowed_hosts = list(getattr(settings, 'ALLOWED_HOSTS', []))
    if '*' in allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [f"{host.lstrip('.')}:*" for host in allowed_hosts] + ['localhost:*', '127.0.0.1:*']
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts,
        allowed_origins=[f"https://{host}" for host in allowed_hosts] + ['http://localhost:*', 'http://127.0.0.1:*'])


def build_application(django_app=None):
    """Standalone ASGI entrypoint: the MCP server at /mcp beside the Django app (when given)."""
    routes = [Route('/mcp', endpoint=build_mcp_app('/mcp'), methods=['GET', 'POST', 'DELETE'])]
    if django_app is not None:
        routes.append(Mount('/', app=django_app))
    return Starlette(routes=routes, lifespan=mcp_lifespan)
