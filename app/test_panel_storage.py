import reflex as rx

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import panel_storage


def panel_fixture(tmp_path, monkeypatch):
    path = tmp_path / "panel_db.json"
    rows = {}
    writes = []
    panel = SimpleNamespace(
        DB_FILE=path,
        CONFIG={"secret": "test-process-secret", "public_domain": ""},
        AUTH={"password_hash": "default-hash"},
        LINKS={},
        CUSTOM_ADDRESSES=["www.speedtest.net"],
    )

    def local_save():
        path_to_write = Path(panel.DB_FILE)
        path_to_write.write_text(
            json.dumps(
                {
                    "auth_hash": panel.AUTH["password_hash"],
                    "links": panel.LINKS,
                    "custom_addresses": panel.CUSTOM_ADDRESSES,
                    "public_domain": panel.CONFIG["public_domain"],
                }
            ),
            encoding="utf-8",
        )

    def local_load():
        source = Path(panel.DB_FILE)
        if source.exists():
            data = json.loads(source.read_text(encoding="utf-8"))
            panel.AUTH["password_hash"] = data["auth_hash"]
            panel.LINKS.clear()
            panel.LINKS.update(data["links"])
            panel.CUSTOM_ADDRESSES[:] = data["custom_addresses"]
            panel.CONFIG["public_domain"] = data.get("public_domain", "")

    async def local_ensure():
        if not panel.LINKS:
            panel.LINKS["admin"] = {"label": "admin"}

    panel.save_db = local_save
    panel.load_db = local_load
    panel.ensure_default_link = local_ensure

    def read():
        return rows.get("panel")

    def write(payload, *, seed=False):
        writes.append(seed)
        if not seed or "panel" not in rows:
            rows["panel"] = payload

    monkeypatch.setattr(panel_storage, "_read_snapshot", read)
    monkeypatch.setattr(panel_storage, "_write_snapshot", write)
    panel_storage.install_panel_storage(panel)
    return panel, path, rows, writes


def test_first_start_with_no_file_persists_after_default_link(
    tmp_path, monkeypatch
):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)
    panel.load_db()
    assert rows == {}
    asyncio.run(panel.ensure_default_link())
    assert writes == [True]
    assert json.loads(rows["panel"])["links"]["admin"]["label"] == "admin"
    assert "secret" not in json.loads(path.read_text(encoding="utf-8"))
    asyncio.run(panel.ensure_default_link())
    assert writes == [True]


def test_legacy_seed_keeps_hash_and_all_fields(tmp_path, monkeypatch):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)
    legacy = {
        "auth_hash": "old-process-hash",
        "links": {"existing": {"label": "existing"}},
        "custom_addresses": ["example.org"],
        "public_domain": "panel.example.org",
        "telegram_token": "legacy-test-only",
        "telegram_admin_id": "123",
        "bot_lang": "fa",
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    panel.load_db()
    asyncio.run(panel.ensure_default_link())
    saved = json.loads(rows["panel"])
    assert {key: saved[key] for key in legacy} == legacy
    assert panel.AUTH["password_hash"] == legacy["auth_hash"]
    assert panel.CONFIG["public_domain"] == legacy["public_domain"]
    assert panel.LINKS["existing"]["label"] == "existing"
    assert writes == [True]
    assert json.loads(path.read_text(encoding="utf-8")) == legacy


def test_database_is_canonical_and_restores_secret_before_auth_load(
    tmp_path, monkeypatch
):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)
    path.write_text(
        json.dumps({"auth_hash": "stale", "links": {}, "custom_addresses": []}),
        encoding="utf-8",
    )
    rows["panel"] = json.dumps(
        {
            "auth_hash": "database-hash",
            "links": {"kept": {"label": "kept"}},
            "custom_addresses": ["db.example.org"],
            "public_domain": "db.example.org",
            "secret": "saved-test-secret",
        }
    )
    panel.load_db()
    assert panel.CONFIG["secret"] == "saved-test-secret"
    assert panel.AUTH["password_hash"] == "database-hash"
    assert panel.CONFIG["public_domain"] == "db.example.org"
    assert "kept" in panel.LINKS
    assert writes == []
    panel.CONFIG["public_domain"] = "updated.example.org"
    panel.save_db()
    assert writes == [False]
    assert json.loads(rows["panel"])["public_domain"] == "updated.example.org"
    assert "secret" not in json.loads(path.read_text(encoding="utf-8"))


def test_failed_database_write_is_not_reported_as_success(
    tmp_path, monkeypatch
):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)

    def fail_write(payload, *, seed=False):
        raise RuntimeError("test database unavailable")

    monkeypatch.setattr(panel_storage, "_write_snapshot", fail_write)
    with pytest.raises(RuntimeError, match="test database unavailable"):
        panel.save_db()
    assert path.exists()
    assert rows == {}


def test_monkeypatched_file_remains_local_only(tmp_path, monkeypatch):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)
    isolated = tmp_path / "isolated.json"
    panel.DB_FILE = isolated
    panel.save_db()
    panel.load_db()
    assert isolated.exists()
    assert rows == {}
    assert writes == []


def test_concurrent_seed_does_not_replace_existing_snapshot(
    tmp_path, monkeypatch
):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)
    path.write_text(
        json.dumps(
            {"auth_hash": "legacy", "links": {}, "custom_addresses": []}
        ),
        encoding="utf-8",
    )
    canonical = {
        "auth_hash": "canonical",
        "links": {"customer": {"label": "customer"}},
        "custom_addresses": ["existing.example.org"],
        "public_domain": "existing.example.org",
        "secret": "canonical-test-secret",
    }

    def concurrent_seed(payload, *, seed=False):
        assert seed
        rows["panel"] = json.dumps(canonical)

    monkeypatch.setattr(panel_storage, "_write_snapshot", concurrent_seed)
    panel.load_db()
    assert panel.AUTH["password_hash"] == "canonical"
    assert panel.CONFIG["public_domain"] == "existing.example.org"
    assert panel.LINKS["customer"]["label"] == "customer"
    assert json.loads(rows["panel"]) == canonical


def test_database_read_failure_is_not_silently_ignored(tmp_path, monkeypatch):
    panel, path, rows, writes = panel_fixture(tmp_path, monkeypatch)

    def fail_read():
        raise RuntimeError("test database unavailable")

    monkeypatch.setattr(panel_storage, "_read_snapshot", fail_read)
    with pytest.raises(RuntimeError, match="test database unavailable"):
        panel.load_db()
    assert rows == {}
