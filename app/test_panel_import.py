import json
import logging
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.app import integrate_panel
import main as panel


@pytest.fixture
def isolated_import_panel(monkeypatch, tmp_path):
    db_file = tmp_path / "import-panel.json"
    monkeypatch.setattr(panel, "DB_FILE", db_file)
    monkeypatch.setattr(panel, "LINKS", {})
    monkeypatch.setattr(panel, "CUSTOM_ADDRESSES", ["old.example.net"])
    monkeypatch.setattr(
        panel,
        "AUTH",
        {"password_hash": panel.hash_password("import-test-password")},
    )
    monkeypatch.setattr(panel, "SESSIONS", {})
    monkeypatch.setitem(panel.CONFIG, "public_domain", "old.example.net")
    monkeypatch.setitem(panel.CONFIG, "telegram_token", "do-not-import-token")
    return db_file


def login(client: TestClient) -> None:
    response = client.post(
        "/api/login", json={"password": "import-test-password"}
    )
    assert response.status_code == 200


def import_payload() -> dict[str, object]:
    return {
        "public_domain": "new.example.org",
        "links_payload": {
            "links": [
                {
                    "uuid": "uid-exact-from-old-panel-001",
                    "id": "database-id-4",
                    "label": "Migrated inbound",
                    "limit_bytes": 987654321,
                    "used_bytes": 123456,
                    "max_connections": 7,
                    "active": False,
                    "created_at": "2024-02-03T04:05:06+00:00",
                    "expires_at": "2031-01-02T00:00:00+00:00",
                    "vless_link": "vless://do-not-store-this",
                    "current_connections": 8,
                    "metadata": {"plan": "legacy", "quota": 4096},
                }
            ]
        },
        "addresses_payload": {"addresses": ["one.example.org", "8.8.4.4"]},
    }


def test_authenticated_import_preserves_admin_uid_fields_and_persists(
    isolated_import_panel,
):
    admin = {
        "label": "admin",
        "active": True,
        "limit_bytes": 0,
        "used_bytes": 91,
        "max_connections": 0,
        "created_at": "2024-01-01T00:00:00+00:00",
        "expires_at": None,
        "protected_marker": "retain-this-record",
    }
    panel.LINKS["admin"] = admin
    panel.LINKS["keep-existing"] = {"label": "unrelated inbound"}
    client = TestClient(integrate_panel(FastAPI()))
    assert (
        client.post(
            "/api/import-panel-state", json=import_payload()
        ).status_code
        == 401
    )
    login(client)

    response = client.post("/api/import-panel-state", json=import_payload())

    assert response.status_code == 200
    assert response.json()["imported_links"] == 1
    assert panel.LINKS["admin"] is admin
    assert panel.LINKS["admin"]["protected_marker"] == "retain-this-record"
    assert "keep-existing" in panel.LINKS
    assert "uid-exact-from-old-panel-001" in panel.LINKS
    imported = panel.LINKS["uid-exact-from-old-panel-001"]
    assert imported["uuid"] == "uid-exact-from-old-panel-001"
    assert imported["id"] == "database-id-4"
    assert imported["limit_bytes"] == 987654321
    assert imported["used_bytes"] == 123456
    assert imported["max_connections"] == 7
    assert imported["active"] is False
    assert imported["expires_at"] == "2031-01-02T00:00:00+00:00"
    assert imported["created_at"] == "2024-02-03T04:05:06+00:00"
    assert imported["metadata"] == {"plan": "legacy", "quota": 4096}
    assert "vless_link" not in imported
    assert "current_connections" not in imported
    assert panel.CONFIG["public_domain"] == "new.example.org"
    assert panel.CUSTOM_ADDRESSES == ["one.example.org", "8.8.4.4"]
    saved = json.loads(isolated_import_panel.read_text(encoding="utf-8"))
    assert (
        saved["links"]["uid-exact-from-old-panel-001"]["id"] == "database-id-4"
    )
    assert saved["public_domain"] == "new.example.org"
    assert saved["custom_addresses"] == ["one.example.org", "8.8.4.4"]
    assert saved["telegram_token"] == "do-not-import-token"
    client.close()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "public_domain": "https://host.example",
            "links_payload": {"links": []},
        },
        {
            "public_domain": "host.example",
            "links_payload": {"links": "not-a-list"},
        },
        {
            "public_domain": "host.example",
            "links_payload": {
                "links": [{"uuid": "u1", "label": "Inbound", "limit_bytes": -1}]
            },
        },
        {
            "public_domain": "host.example",
            "links_payload": {"links": [{"uuid": "", "label": "Inbound"}]},
        },
        {
            "public_domain": "host.example",
            "links_payload": {"links": [{"uuid": "u1", "label": "Inbound"}]},
            "addresses_payload": {"addresses": ["not a host/path"]},
        },
    ],
)
def test_invalid_import_is_rejected_without_mutation(
    isolated_import_panel, payload
):
    old_link = {"label": "original", "used_bytes": 37}
    panel.LINKS["existing"] = old_link
    panel.CUSTOM_ADDRESSES[:] = ["original.example.net"]
    panel.CONFIG["public_domain"] = "original.example.net"
    client = TestClient(panel.app)
    login(client)

    response = client.post("/api/import-panel-state", json=payload)

    assert response.status_code == 400
    assert panel.LINKS == {"existing": old_link}
    assert panel.CUSTOM_ADDRESSES == ["original.example.net"]
    assert panel.CONFIG["public_domain"] == "original.example.net"
    assert not isolated_import_panel.exists()
    client.close()


def test_replace_requires_explicit_confirmation_and_keeps_admin(
    isolated_import_panel,
):
    admin = {"label": "admin", "active": True, "identity": secrets.token_hex(4)}
    panel.LINKS.update({"admin": admin, "remove-me": {"label": "old"}})
    payload = import_payload()
    payload["replace_existing"] = True
    client = TestClient(panel.app)
    login(client)

    rejected = client.post("/api/import-panel-state", json=payload)
    assert rejected.status_code == 400
    payload["confirm_replace"] = True
    accepted = client.post("/api/import-panel-state", json=payload)

    assert accepted.status_code == 200
    assert "remove-me" not in panel.LINKS
    assert panel.LINKS["admin"] is admin
    assert "uid-exact-from-old-panel-001" in panel.LINKS
    client.close()


def test_security_settings_explain_manual_json_migration_and_disk_risk():
    assert 'id="import-links-json"' in panel.PANEL_HTML
    assert 'id="import-addresses-json"' in panel.PANEL_HTML
    assert 'id="import-domain"' in panel.PANEL_HTML
    assert (
        "open the old panel's authenticated /api/links URL" in panel.PANEL_HTML
    )
    assert "will not survive an ephemeral deployment" in panel.PANEL_HTML
    assert "confirm_replace" in panel.PANEL_HTML


def test_database_failure_rolls_back_import_and_reports_error(
    isolated_import_panel, monkeypatch, caplog
):
    original_links = {
        "admin": {"label": "admin"},
        "existing": {"label": "keep"},
    }
    panel.LINKS.update(original_links)
    original_addresses = ["old.example.net"]
    panel.CUSTOM_ADDRESSES[:] = original_addresses
    panel.CONFIG["public_domain"] = "old.example.net"

    def fail_save():
        raise RuntimeError("snapshot database offline")

    monkeypatch.setattr(panel, "save_db", fail_save)
    client = TestClient(panel.app)
    login(client)
    with caplog.at_level(logging.ERROR):
        response = client.post("/api/import-panel-state", json=import_payload())

    assert response.status_code == 503
    assert "not saved" in response.json()["detail"].lower()
    assert panel.LINKS == original_links
    assert panel.CUSTOM_ADDRESSES == original_addresses
    assert panel.CONFIG["public_domain"] == "old.example.net"
    assert "snapshot database offline" in caplog.text
    assert not isolated_import_panel.exists()
    client.close()
