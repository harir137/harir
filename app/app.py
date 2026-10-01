import reflex as rx
import importlib
import inspect
from contextlib import asynccontextmanager
import starlette.exceptions as starlette_exceptions
import starlette.types as starlette_types
import starlette._utils as starlette_utils

# Refresh stale Starlette modules left loaded after dependency changes.
for module, symbol in (
    (starlette_exceptions, "StarletteDeprecationWarning"),
    (starlette_types, "ExceptionHandler"),
    (starlette_utils, "get_route_path"),
):
    if not hasattr(module, symbol):
        importlib.reload(module)

import starlette.middleware as starlette_middleware

# Repair a stale two-field Middleware iterator before FastAPI builds the panel stack.
if len(tuple(starlette_middleware.Middleware(object))) == 2:

    def compatible_middleware_iter(self):
        yield self.cls
        yield getattr(self, "args", ())
        yield getattr(self, "kwargs", getattr(self, "options", {}))

    starlette_middleware.Middleware.__iter__ = compatible_middleware_iter

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from urllib.parse import parse_qsl, urlencode

import main as panel_module
from app.luffy_view import luffy_page
from app.live_logs import install_live_logs
from app.panel_domain import install_domain_ui


def repair_outbound_tunnel() -> None:
    original = panel_module.websocket_tunnel
    source = inspect.getsource(original)
    first_message = "        first_msg = await asyncio.wait_for(websocket.receive(), timeout=15.0)\n"
    guarded_first_message = (
        "        try:\n"
        "            first_msg = await asyncio.wait_for(websocket.receive(), timeout=15.0)\n"
        "        except TimeoutError:\n"
        "            await websocket.close(code=1008, reason='initial message timeout')\n"
        "            return\n"
    )
    outbound = (
        "        reader, writer = await asyncio.wait_for(\n"
        "            asyncio.open_connection(address, port), timeout=10.0\n"
        "        )\n"
    )
    guarded = (
        "        try:\n"
        "            reader, writer = await asyncio.wait_for(\n"
        "                asyncio.open_connection(address, port), timeout=10.0\n"
        "            )\n"
        "        except (TimeoutError, OSError) as exc:\n"
        "            message = (\n"
        "                'Outbound TCP connection timed out'\n"
        "                if isinstance(exc, TimeoutError)\n"
        "                else 'Outbound TCP connection failed'\n"
        "            )\n"
        "            stats['total_errors'] += 1\n"
        "            error_logs.append(\n"
        "                {'error': message, 'time': datetime.now(timezone.utc).isoformat()}\n"
        "            )\n"
        "            logger.warning('%s', message)\n"
        "            await websocket.close(\n"
        "                code=1013, reason='destination temporarily unavailable'\n"
        "            )\n"
        "            return\n"
    )
    if source.count(first_message) != 1:
        raise RuntimeError("Initial tunnel message block not found")
    if source.count(outbound) != 1:
        raise RuntimeError("Outbound tunnel connection block not found")
    source = source.replace(first_message, guarded_first_message, 1)
    source = source.replace(outbound, guarded, 1)
    source = source[source.index("async def websocket_tunnel(") :]
    namespace: dict[str, object] = {}
    exec(
        compile(source, inspect.getsourcefile(original) or "main.py", "exec"),
        panel_module.__dict__,
        namespace,
    )
    patched = namespace["websocket_tunnel"]
    panel_module.websocket_tunnel = patched
    for route in panel_module.app.router.routes:
        if getattr(route, "path", None) == "/ws/{uuid}":
            route.endpoint = patched
            route.dependant.call = patched
            return
    raise RuntimeError("WebSocket tunnel route not found")


install_domain_ui()
repair_outbound_tunnel()
install_live_logs()
panel_app = panel_module.app


# The original panel reads its session cookie from JavaScript for live logs, but
# that cookie is HttpOnly. Let the browser send the cookie with the socket instead.
panel_module.PANEL_HTML = panel_module.PANEL_HTML.replace(
    "  const token = document.cookie.split('; ').find(row => row.startsWith('ren_session='))?.split('=')[1];\n  if(!token) return;\n  logsWS = new WebSocket(`${protocol}//${location.host}/ws/live-logs?token=${token}`);",
    "  if(!isAuthenticated) return;\n  logsWS = new WebSocket(`${protocol}//${location.host}/ws/live-logs`);",
)


PANEL_PATHS = frozenset({"/login", "/dashboard", "/panel", "/health", "/stats"})
PANEL_PREFIXES = ("/api/", "/ws/", "/sub/")


def integrate_panel(reflex_api: FastAPI) -> ASGIApp:
    reflex_lifespan = reflex_api.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with reflex_lifespan(app):
            async with panel_app.router.lifespan_context(panel_app):
                yield

    reflex_api.router.lifespan_context = lifespan

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] in ("http", "websocket") and (
            path in PANEL_PATHS or path.startswith(PANEL_PREFIXES)
        ):
            if scope["type"] == "websocket" and path == "/ws/live-logs":
                # The panel's WS handler expects a token query parameter. Supply it
                # from the HttpOnly session cookie without revealing it to scripts.
                cookies = dict(
                    item.strip().split("=", 1)
                    for header, value in scope.get("headers", [])
                    if header.lower() == b"cookie"
                    for item in value.decode("latin-1").split(";")
                    if "=" in item
                )
                if "ren_session" in cookies:
                    params = dict(
                        parse_qsl(
                            scope.get("query_string", b"").decode("latin-1")
                        )
                    )
                    params["token"] = cookies["ren_session"]
                    scope = {
                        **scope,
                        "query_string": urlencode(params).encode("latin-1"),
                    }
            await panel_app(scope, receive, send)
        else:
            await reflex_api(scope, receive, send)

    return dispatch


app = rx.App(
    theme=rx.theme(appearance="light"),
    api_transformer=integrate_panel,
)


def index() -> rx.Component:
    return luffy_page()


app.add_page(index, route="/", title="Luffy Panel")
app.add_page(luffy_page, route="/luffy", title="Luffy Panel")
