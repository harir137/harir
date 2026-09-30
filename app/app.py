import reflex as rx
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from urllib.parse import parse_qsl, urlencode

import main as panel_module
from app.luffy_view import luffy_page

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
    reflex_api.add_event_handler("startup", panel_app.router.startup)
    reflex_api.add_event_handler("shutdown", panel_app.router.shutdown)

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
app.add_page(luffy_page, route="/", title="Luffy Panel")
app.add_page(luffy_page, route="/luffy", title="Luffy Panel")
