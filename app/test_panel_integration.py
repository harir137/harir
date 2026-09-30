import reflex as rx
import secrets

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.app import integrate_panel
import main as panel_module
import logging


def test_real_panel_routes() -> None:
    api = FastAPI()

    @api.get("/ping/")
    def ping() -> str:
        return "pong"

    with TestClient(integrate_panel(api)) as client:
        page = client.get("/login")
        assert page.status_code == 200
        assert "Luffy Panel" in page.text
        assert "<html" in page.text
        assert client.get("/dashboard").status_code == 200
        assert client.get("/panel").status_code == 200

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/api/me").json() == {"authenticated": False}
        assert client.get("/api/links").status_code == 401
        assert (
            client.post(
                "/api/login", json={"password": "not-the-password"}
            ).status_code
            == 401
        )
        assert client.get("/ping/").json() == "pong"

        try:
            with client.websocket_connect("/ws/live-logs") as websocket:
                websocket.receive_text()
        except WebSocketDisconnect as exc:
            logging.exception("Unexpected error")
            assert exc.code == 1008
        else:
            raise AssertionError("Unauthenticated log socket remained open")

        password_hash = panel_module.AUTH["password_hash"]
        password = secrets.token_urlsafe(24)
        try:
            panel_module.AUTH["password_hash"] = panel_module.hash_password(
                password
            )
            response = client.post("/api/login", json={"password": password})
            assert response.status_code == 200
            assert "httponly" in response.headers["set-cookie"].lower()
            assert client.get("/api/me").json() == {"authenticated": True}
            assert client.get("/api/links").status_code == 200

            marker = f"panel-integration-{secrets.token_hex(8)}"
            panel_module.log_queue.append(marker)
            with client.websocket_connect("/ws/live-logs") as websocket:
                messages = [
                    websocket.receive_text()
                    for _ in range(len(panel_module.log_queue))
                ]
                assert marker in messages

            assert client.post("/api/logout").status_code == 200
            assert client.get("/api/me").json() == {"authenticated": False}
        finally:
            panel_module.AUTH["password_hash"] = password_hash
