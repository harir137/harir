import asyncio

import pytest
from fastapi.testclient import TestClient

import app.app
import main as panel


@pytest.fixture
def isolated_panel(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, "DB_FILE", tmp_path / "isolated-panel.json")
    monkeypatch.setattr(panel, "LINKS", {})
    monkeypatch.setattr(panel, "SESSIONS", {})
    monkeypatch.setattr(
        panel,
        "AUTH",
        {"password_hash": panel.hash_password("test-admin-password")},
    )

    async def no_telegram_restart():
        return None

    monkeypatch.setattr(panel, "restart_telegram_bot", no_telegram_restart)
    return tmp_path / "isolated-panel.json"


def test_admin_startup_normalization_is_idempotent_and_preserves_identity(
    isolated_panel, monkeypatch
):
    admin_record = {
        "label": "old label",
        "active": False,
        "expires_at": "2030-01-01T00:00:00+00:00",
        "limit_bytes": 1024,
        "max_connections": 4,
        "used_bytes": 987654,
        "created_at": "2024-01-02T03:04:05+00:00",
        "ports": [8443],
        "identity_marker": "keep-existing-identity",
    }
    default_record = {
        "label": "Default",
        "active": True,
        "limit_bytes": 0,
        "used_bytes": 321,
        "max_connections": 0,
        "created_at": "2024-01-01T00:00:00+00:00",
        "expires_at": None,
    }
    monkeypatch.setattr(
        panel,
        "LINKS",
        {"admin": admin_record, "Default": default_record},
    )
    saves = []

    def count_save():
        saves.append(True)

    monkeypatch.setattr(panel, "save_db", count_save)

    asyncio.run(panel.ensure_default_link())
    assert admin_record == {
        "label": "admin",
        "active": True,
        "expires_at": None,
        "limit_bytes": 0,
        "max_connections": 0,
        "used_bytes": 987654,
        "created_at": "2024-01-02T03:04:05+00:00",
        "ports": [8443],
        "identity_marker": "keep-existing-identity",
    }
    assert panel.LINKS["Default"] is default_record
    assert saves == [True]

    asyncio.run(panel.ensure_default_link())
    assert saves == [True]


def test_admin_is_added_without_replacing_existing_links(
    isolated_panel, monkeypatch
):
    default_record = {"label": "Default", "used_bytes": 15}
    other_record = {"label": "Customer", "used_bytes": 29}
    monkeypatch.setattr(
        panel,
        "LINKS",
        {"Default": default_record, "Customer": other_record},
    )

    asyncio.run(panel.ensure_default_link())

    assert panel.LINKS["Default"] is default_record
    assert panel.LINKS["Customer"] is other_record
    assert panel.LINKS["admin"]["label"] == "admin"
    assert panel.LINKS["admin"]["active"] is True
    assert panel.LINKS["admin"]["expires_at"] is None
    assert panel.LINKS["admin"]["limit_bytes"] == 0
    assert panel.LINKS["admin"]["max_connections"] == 0


def test_api_rejects_admin_mutations_but_allows_usage_reset(
    isolated_panel, monkeypatch
):
    admin_record = {
        "label": "admin",
        "active": True,
        "expires_at": None,
        "limit_bytes": 0,
        "used_bytes": 812,
        "max_connections": 0,
        "created_at": "2024-01-02T03:04:05+00:00",
    }
    other_record = {
        "label": "Other",
        "active": True,
        "expires_at": None,
        "limit_bytes": 0,
        "used_bytes": 10,
        "max_connections": 0,
        "created_at": "2024-01-01T03:04:05+00:00",
    }
    monkeypatch.setattr(
        panel, "LINKS", {"admin": admin_record, "Other": other_record}
    )

    with TestClient(panel.app) as client:
        login = client.post(
            "/api/login", json={"password": "test-admin-password"}
        )
        assert login.status_code == 200

        protected_updates = [
            {"active": False},
            {"limit_value": 5, "limit_unit": "GB"},
            {"days_valid": 7},
            {"expires_at": "2030-01-01T00:00:00Z"},
            {"max_connections": 2},
            {"label": "renamed"},
        ]
        for update in protected_updates:
            response = client.patch("/api/links/admin", json=update)
            assert response.status_code == 403
            assert response.json()["detail"]

        deletion = client.delete("/api/links/admin")
        assert deletion.status_code == 403
        assert "permanent" in deletion.json()["detail"].lower()
        assert panel.LINKS["admin"] == admin_record

        reset = client.patch("/api/links/admin", json={"reset_usage": True})
        assert reset.status_code == 200
        assert panel.LINKS["admin"]["used_bytes"] == 0

        disabled_other = client.patch(
            "/api/links/Other", json={"active": False}
        )
        assert disabled_other.status_code == 200
        assert panel.LINKS["Other"]["active"] is False


def test_telegram_cannot_disable_admin_but_can_disable_other_links(
    isolated_panel, monkeypatch
):
    admin_record = {"label": "admin", "active": True}
    other_record = {"label": "Other", "active": True}
    monkeypatch.setattr(
        panel, "LINKS", {"admin": admin_record, "Other": other_record}
    )

    admin_result = asyncio.run(
        panel.handle_toggle_command("/disable admin", False)
    )
    assert "permanent" in admin_result.lower()
    assert admin_record["active"] is True

    other_result = asyncio.run(
        panel.handle_toggle_command("/disable Other", False)
    )
    assert "disabled" in other_result.lower()
    assert other_record["active"] is False
