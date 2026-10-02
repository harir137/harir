import json
import os
import subprocess
import sys


def test_reflex_import_installs_panel_routes_and_snapshot_storage(
    tmp_path,
):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = r"""
import json
from fastapi.testclient import TestClient
from app import panel_storage

snapshots = {}
panel_storage._read_snapshot = lambda: snapshots.get("panel")

def write_snapshot(payload, *, seed=False):
    if not seed or "panel" not in snapshots:
        snapshots["panel"] = payload

panel_storage._write_snapshot = write_snapshot
import app.app
import main as panel
from app.panel_bootstrap import install_panel_integrations

assert panel._panel_integrations_installed is True
panel.CONFIG["public_domain"] = "direct.example.org"
assert panel.get_domain() == "direct.example.org"
routes = [
    (getattr(route, "path", None), method)
    for route in panel.app.routes
    for method in getattr(route, "methods", set())
]
assert routes.count(("/api/domain", "GET")) == 1
assert routes.count(("/api/domain", "POST")) == 1
assert routes.count(("/api/import-panel-state", "POST")) == 1
install_panel_integrations(panel)
assert [
    (getattr(route, "path", None), method)
    for route in panel.app.routes
    for method in getattr(route, "methods", set())
] == routes

panel.load_db()
panel.CONFIG["public_domain"] = "bootstrap.example.org"
test_link = {
    "label": "Snapshot test link",
    "limit_bytes": 0,
    "used_bytes": 0,
    "max_connections": 0,
    "active": True,
}
panel.LINKS["snapshot-test-link"] = test_link
panel.save_db()
saved_snapshot = json.loads(snapshots["panel"])
assert saved_snapshot["public_domain"] == "bootstrap.example.org"
assert saved_snapshot["links"]["snapshot-test-link"] == test_link
panel.CONFIG["public_domain"] = ""
panel.LINKS.clear()
panel.load_db()
assert panel.CONFIG["public_domain"] == "bootstrap.example.org"
assert panel.LINKS["snapshot-test-link"] == test_link

client = TestClient(panel.app)
assert client.get("/api/domain").status_code == 401
assert client.post("/api/import-panel-state", json={}).status_code == 401
client.close()
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [project_root, environment.get("PYTHONPATH", "")])
    )
    environment["REFLEX_DB_URL"] = "postgresql+psycopg://localhost/panel_test"
    environment.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    local_snapshot_path = tmp_path / "panel_db.json"
    assert local_snapshot_path.exists()
    local_snapshot = json.loads(local_snapshot_path.read_text(encoding="utf-8"))
    assert local_snapshot["public_domain"] == "bootstrap.example.org"
    assert "snapshot-test-link" in local_snapshot["links"]
    assert "secret" not in local_snapshot
