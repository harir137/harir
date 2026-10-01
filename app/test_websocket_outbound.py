import reflex as rx
from collections import defaultdict, deque
import logging
import socket
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.app import integrate_panel
import main as panel


@pytest.fixture
def isolated_tunnel(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, "DB_FILE", tmp_path / "panel-test.json")
    monkeypatch.setitem(panel.CONFIG, "telegram_token", "")
    monkeypatch.setitem(panel.CONFIG, "telegram_admin_id", "")
    uid = str(uuid4())
    monkeypatch.setattr(
        panel,
        "LINKS",
        {
            uid: {
                "label": "Test",
                "active": True,
                "limit_bytes": 0,
                "used_bytes": 0,
                "max_connections": 0,
                "expires_at": None,
            }
        },
    )
    monkeypatch.setattr(panel, "connections", {})
    monkeypatch.setattr(panel, "connection_sockets", {})
    monkeypatch.setattr(panel, "link_ip_map", defaultdict(set))
    monkeypatch.setattr(
        panel,
        "stats",
        {"total_bytes": 0, "total_requests": 0, "total_errors": 0},
    )
    monkeypatch.setattr(panel, "error_logs", deque(maxlen=50))
    return uid


def vless_ipv4_header(uid: str, port: int) -> bytes:
    return (
        b"\x00"
        + UUID(uid).bytes
        + b"\x00\x01"
        + port.to_bytes(2, "big")
        + b"\x01"
        + socket.inet_aton("127.0.0.1")
    )


def test_idle_initial_message_timeout_closes_without_error(
    isolated_tunnel, tmp_path, caplog
):
    uid = isolated_tunnel

    with caplog.at_level(logging.WARNING):
        with TestClient(integrate_panel(FastAPI())) as client:
            with client.websocket_connect(f"/ws/{uid}") as websocket:
                with pytest.raises(WebSocketDisconnect) as closed:
                    websocket.receive_bytes()

            assert closed.value.code == 1008
            assert closed.value.reason == "initial message timeout"
            assert panel.stats["total_errors"] == 0
            assert not panel.error_logs
            assert panel.connections == {}
            assert panel.connection_sockets == {}
            assert not panel.link_ip_map.get(uid)

    assert not any(
        record.levelno >= logging.ERROR or record.exc_info
        for record in caplog.records
    )
    assert "Traceback (most recent call last)" not in caplog.text
    assert not (tmp_path / "panel-test.json").exists()


@pytest.mark.parametrize("failure", ["refused", "timeout"])
def test_outbound_failure_closes_only_tunnel_and_cleans_up(
    isolated_tunnel, monkeypatch, tmp_path, caplog, failure
):
    uid = isolated_tunnel
    if failure == "timeout":

        async def timed_out_connection(address, port):
            assert address == "127.0.0.1"
            raise TimeoutError("sensitive destination details")

        monkeypatch.setattr(
            panel.asyncio, "open_connection", timed_out_connection
        )
        port = 443
    else:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        except OSError as e:
            logging.exception(f"Error: {e}")
            raise

    tunnel_route = next(
        route
        for route in panel.app.router.routes
        if getattr(route, "path", None) == "/ws/{uuid}"
    )
    assert tunnel_route.endpoint is panel.websocket_tunnel

    with caplog.at_level(logging.WARNING):
        with TestClient(integrate_panel(FastAPI())) as client:
            with client.websocket_connect(f"/ws/{uid}") as websocket:
                websocket.send_bytes(vless_ipv4_header(uid, port))
                with pytest.raises(WebSocketDisconnect) as closed:
                    websocket.receive_bytes()
            assert closed.value.code == 1013
            assert closed.value.reason == "destination temporarily unavailable"
            assert panel.stats["total_errors"] == 1
            assert len(panel.error_logs) == 1
            expected = (
                "Outbound TCP connection timed out"
                if failure == "timeout"
                else "Outbound TCP connection failed"
            )
            assert panel.error_logs[0]["error"] == expected
            assert panel.error_logs[0]["time"]
            assert panel.connections == {}
            assert panel.connection_sockets == {}
            assert not panel.link_ip_map.get(uid)
            gateway_records = [
                record
                for record in caplog.records
                if record.name == "Luffy-Gateway"
            ]
            assert not any(
                record.levelno >= logging.ERROR or record.exc_info
                for record in caplog.records
            )
            assert "sensitive destination details" not in caplog.text
            assert "Traceback (most recent call last)" not in caplog.text
            assert any(
                record.levelno == logging.WARNING
                and record.getMessage() == expected
                for record in gateway_records
            )
    assert not (tmp_path / "panel-test.json").exists()
