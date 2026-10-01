import asyncio
from contextlib import suppress

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

import main as panel


async def ws_live_logs(websocket: WebSocket, token: str | None = None) -> None:
    await websocket.accept()
    if not token or not await panel.is_valid_session(token):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    with suppress(WebSocketDisconnect):
        for item in list(panel.log_queue):
            await websocket.send_text(item)
        last_idx = len(panel.log_queue)
        while True:
            message = None
            with suppress(TimeoutError):
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=0.5
                )
            if (
                message is not None
                and message["type"] == "websocket.disconnect"
            ):
                break

            curr = list(panel.log_queue)
            if len(curr) > last_idx:
                for item in curr[last_idx:]:
                    await websocket.send_text(item)
                last_idx = len(curr)
            elif len(curr) < last_idx:
                last_idx = len(curr)


def install_live_logs() -> None:
    for route in panel.app.router.routes:
        if getattr(route, "path", None) == "/ws/live-logs":
            route.endpoint = ws_live_logs
            route.dependant.call = ws_live_logs
            return
    raise RuntimeError("Live logs WebSocket route not found")
