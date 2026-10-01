import reflex as rx
import secrets
import time
from collections import deque

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.app import integrate_panel
from app.luffy_view import luffy_page
import main as panel_module
import logging


def test_root_panel_redirects_to_backend_login() -> None:
    page = luffy_page()
    script = page.children[0]
    script_text = str(script.render())
    link = page.children[2].children[3]
    href = link.to

    assert "window.location.replace" in script_text
    assert "window.location.protocol" in script_text
    assert "backendHost" in script_text
    assert "/login" in script_text
    assert "/^8080-/" in script_text
    assert "8000-" in script_text
    assert "/:8080$/" in script_text
    assert ":8000" in script_text
    assert "iframe" not in str(page.render()).lower()
    assert isinstance(href, rx.Var)
    assert 'replaceAll("/_upload/login", "/login")' in str(href)
    assert 'replaceAll("://8080-", "://8000-")' in str(href)
    assert str(href).index('replaceAll("/_upload/login", "/login")') < str(
        href
    ).index('replaceAll("://8080-", "://8000-")')
    assert "<reflex.Var>" not in str(href)

    with TestClient(integrate_panel(FastAPI())) as client:
        login = client.get("/login")
        assert login.status_code == 200
        assert "Luffy Panel" in login.text


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


def test_idle_live_logs_socket_does_not_log_expected_timeouts_or_disconnect(
    monkeypatch, tmp_path, caplog
) -> None:
    db_file = tmp_path / "isolated-panel.json"
    password = secrets.token_urlsafe(24)
    monkeypatch.setattr(panel_module, "DB_FILE", db_file)
    monkeypatch.setattr(
        panel_module,
        "AUTH",
        {"password_hash": panel_module.hash_password(password)},
    )
    monkeypatch.setattr(panel_module, "SESSIONS", {})
    monkeypatch.setattr(panel_module, "log_queue", deque(maxlen=150))

    # No lifespan startup: this test exercises login and the routed socket only.
    client = TestClient(integrate_panel(FastAPI()))
    try:
        response = client.post("/api/login", json={"password": password})
        assert response.status_code == 200
        assert client.get("/api/me").json() == {"authenticated": True}

        with caplog.at_level(logging.ERROR):
            caplog.clear()
            with client.websocket_connect("/ws/live-logs") as websocket:
                time.sleep(1.1)
                closed_at = time.monotonic()
                websocket.close()
            assert time.monotonic() - closed_at < 1.5

        assert not any(
            record.levelno >= logging.ERROR or record.exc_info
            for record in caplog.records
        )
        assert "Traceback (most recent call last)" not in caplog.text
        assert not panel_module.log_queue
        assert not db_file.exists()
    finally:
        client.close()
