import reflex as rx
import base64
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.app import integrate_panel
import main as panel


@pytest.fixture
def isolated_panel(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, "DB_FILE", tmp_path / "panel-test.json")
    monkeypatch.setattr(panel, "LINKS", {})
    monkeypatch.setattr(panel, "CUSTOM_ADDRESSES", ["www.speedtest.net"])
    monkeypatch.setattr(
        panel,
        "AUTH",
        {"password_hash": panel.hash_password("test-only-password")},
    )
    monkeypatch.setattr(panel, "SESSIONS", {})
    monkeypatch.setitem(panel.CONFIG, "public_domain", "")
    monkeypatch.setitem(panel.CONFIG, "telegram_token", "")
    monkeypatch.setitem(panel.CONFIG, "telegram_admin_id", "")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://localhost")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    return tmp_path / "panel-test.json"


def test_domain_override_and_legacy_db(isolated_panel, monkeypatch):
    assert panel.get_domain() == "localhost"
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "railway.example.com")
    monkeypatch.delenv("RENDER_EXTERNAL_URL")
    assert panel.get_domain() == "railway.example.com"
    panel.CONFIG["public_domain"] = "vpn.example.com"
    assert panel.get_domain() == "vpn.example.com"
    panel.CONFIG["public_domain"] = ""
    try:
        isolated_panel.write_text(
            json.dumps({"auth_hash": panel.AUTH["password_hash"], "links": {}})
        )
    except OSError as e:
        logging.exception(f"Error: {e}")
        raise
    panel.load_db()
    assert panel.CONFIG["public_domain"] == ""
    assert panel.get_domain() == "railway.example.com"


def test_domain_auth_validation_persistence_and_existing_links(isolated_panel):
    client = TestClient(panel.app)
    assert client.get("/api/domain").status_code == 401
    assert (
        client.post(
            "/api/domain", json={"public_domain": "vpn.example.com"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/login", json={"password": "test-only-password"}
        ).status_code
        == 200
    )
    assert client.get("/api/domain").json() == {
        "public_domain": "",
        "effective_domain": "localhost",
    }

    created = client.post("/api/links", json={"label": "Existing"})
    assert created.status_code == 200
    assert "@localhost:443" in created.json()["vless_link"]
    panel.CONFIG["telegram_token"] = "unchanged-token"
    panel.CONFIG["telegram_admin_id"] = "42"

    invalid = [
        "",
        " example.com",
        "example.com ",
        "example. com",
        "example.com\n",
        "https://example.com",
        "example.com/path",
        "user@example.com",
        "example.com:443",
        "example.com?x=1",
        "example.com#fragment",
        "-bad.example",
        "bad-.example",
        "bad..example",
        "example.com.",
        "256.1.2.3",
        "1.2.3.999",
        "[::1]",
        "xn--.example",
        "a" * 64 + ".example",
        7,
        None,
        [],
    ]
    for domain in invalid:
        assert (
            client.post(
                "/api/domain", json={"public_domain": domain}
            ).status_code
            == 400
        )
    assert client.post("/api/domain", json={}).status_code == 400
    assert client.post("/api/domain", content="{").status_code == 400
    assert panel.CONFIG["public_domain"] == ""

    response = client.post(
        "/api/domain", json={"public_domain": "vpn.example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "public_domain": "vpn.example.com",
        "effective_domain": "vpn.example.com",
    }
    assert client.get("/api/domain").json() == response.json()
    assert client.get("/stats").json()["domain"] == "vpn.example.com"
    link = client.get("/api/links").json()["links"][0]["vless_link"]
    assert "@vpn.example.com:443" in link
    assert "host=vpn.example.com" in link and "sni=vpn.example.com" in link
    assert "localhost" not in link
    subscription = base64.b64decode(
        client.get("/sub/Existing").content
    ).decode()
    assert "@vpn.example.com:443" in subscription
    assert "host=vpn.example.com" in subscription
    assert "localhost" not in subscription
    try:
        saved = json.loads(isolated_panel.read_text())
    except (OSError, ValueError) as e:
        logging.exception(f"Error: {e}")
        raise
    assert saved["public_domain"] == "vpn.example.com"
    assert saved["links"]["Existing"]["label"] == "Existing"
    assert saved["telegram_token"] == "unchanged-token"
    panel.CONFIG["public_domain"] = ""
    panel.load_db()
    assert panel.get_domain() == "vpn.example.com"
    assert (
        "@vpn.example.com:443"
        in client.get("/api/links").json()["links"][0]["vless_link"]
    )
    assert (
        client.post(
            "/api/domain", json={"public_domain": "192.0.2.10"}
        ).status_code
        == 200
    )
    assert (
        "@192.0.2.10:443"
        in client.get("/api/links").json()["links"][0]["vless_link"]
    )


def test_authenticated_public_host_is_persisted_before_links_render(
    isolated_panel, monkeypatch
):
    local = TestClient(panel.app)
    assert (
        local.post(
            "/api/login", json={"password": "test-only-password"}
        ).status_code
        == 200
    )
    assert (
        local.post("/api/links", json={"label": "Existing"}).status_code == 200
    )
    assert panel.get_domain() == "localhost"

    public = TestClient(panel.app, base_url="https://panel.example.com")
    assert public.get("/api/me").json() == {"authenticated": False}
    assert public.get("/api/links").status_code == 401
    public.cookies.set(panel.SESSION_COOKIE, "invalid-session")
    assert public.get("/api/me").json() == {"authenticated": False}
    assert panel.CONFIG["public_domain"] == ""
    assert json.loads(isolated_panel.read_text())["public_domain"] == ""

    public.cookies.set(
        panel.SESSION_COOKIE, local.cookies.get(panel.SESSION_COOKIE)
    )
    saves = []
    original_save = panel.save_db

    def count_save():
        saves.append(True)
        original_save()

    monkeypatch.setattr(panel, "save_db", count_save)
    assert public.get("/api/me").json() == {"authenticated": True}
    assert panel.get_domain() == "panel.example.com"
    assert (
        json.loads(isolated_panel.read_text())["public_domain"]
        == "panel.example.com"
    )
    assert public.get("/stats").json()["domain"] == "panel.example.com"
    assert public.get("/api/domain").json() == {
        "public_domain": "panel.example.com",
        "effective_domain": "panel.example.com",
    }
    link = public.get("/api/links").json()["links"][0]["vless_link"]
    assert "@panel.example.com:443" in link
    assert "host=panel.example.com" in link
    assert "sni=panel.example.com" in link
    subscription = base64.b64decode(
        public.get("/sub/Existing").content
    ).decode()
    assert "@panel.example.com:443" in subscription
    assert "host=panel.example.com" in subscription
    assert "sni=panel.example.com" in subscription
    assert "localhost" not in subscription
    assert len(saves) == 1
    panel.CONFIG["public_domain"] = ""
    panel.load_db()
    assert panel.get_domain() == "panel.example.com"


@pytest.mark.parametrize(
    "hostname",
    [
        "localhost",
        "testserver",
        "panel.localhost",
        "panel.testserver",
        "panel.local",
        "panel.internal",
        "panel.private",
        "panel.lan",
        "10.0.0.2",
        "127.0.0.1",
        "169.254.1.1",
        "192.168.0.1",
        "not-a-public-host",
        "bad..example.com",
    ],
)
def test_authenticated_non_public_host_is_ignored(isolated_panel, hostname):
    client = TestClient(panel.app, base_url=f"http://{hostname}")
    assert (
        client.post(
            "/api/login", json={"password": "test-only-password"}
        ).status_code
        == 200
    )
    assert client.get("/api/me").json() == {"authenticated": True}
    assert panel.CONFIG["public_domain"] == ""
    assert panel.get_domain() == "localhost"
    assert not isolated_panel.exists()


def test_forwarded_host_does_not_set_domain(isolated_panel):
    client = TestClient(panel.app)
    assert (
        client.post(
            "/api/login", json={"password": "test-only-password"}
        ).status_code
        == 200
    )
    assert client.get(
        "/api/me", headers={"x-forwarded-host": "panel.example.com"}
    ).json() == {"authenticated": True}
    assert panel.CONFIG["public_domain"] == ""
    assert not isolated_panel.exists()


def test_explicit_and_environment_domains_remain_authoritative(
    isolated_panel, monkeypatch
):
    client = TestClient(panel.app, base_url="https://browser.example.com")
    assert (
        client.post(
            "/api/login", json={"password": "test-only-password"}
        ).status_code
        == 200
    )
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://render.example.com")
    assert client.get("/api/me").json() == {"authenticated": True}
    assert panel.get_domain() == "render.example.com"
    assert panel.CONFIG["public_domain"] == ""
    assert not isolated_panel.exists()

    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://localhost")
    local = TestClient(panel.app)
    local.cookies.set(
        panel.SESSION_COOKIE, client.cookies.get(panel.SESSION_COOKIE)
    )
    assert (
        local.post(
            "/api/domain", json={"public_domain": "manual.example.com"}
        ).status_code
        == 200
    )
    assert client.get("/api/me").json() == {"authenticated": True}
    assert panel.get_domain() == "manual.example.com"
    assert (
        json.loads(isolated_panel.read_text())["public_domain"]
        == "manual.example.com"
    )


def test_public_ipv4_can_be_inferred(isolated_panel):
    client = TestClient(panel.app, base_url="https://8.8.8.8")
    assert (
        client.post(
            "/api/login", json={"password": "test-only-password"}
        ).status_code
        == 200
    )
    assert client.get("/api/me").json() == {"authenticated": True}
    assert panel.get_domain() == "8.8.8.8"
    assert json.loads(isolated_panel.read_text())["public_domain"] == "8.8.8.8"


def test_domain_controls_are_present_in_existing_panel():
    assert 'id="public-domain"' in panel.PANEL_HTML
    assert "location.hostname" in panel.PANEL_HTML
    assert "loadDomain();" in panel.PANEL_HTML
    assert "await loadStats();\n    await loadLinks();" in panel.PANEL_HTML
    assert "(sData.domain||'localhost')+'/sub/'" in panel.PANEL_HTML
    assert "location.host+'/sub/'" not in panel.PANEL_HTML
    assert 'onclick="saveSettings()"' in panel.PANEL_HTML
