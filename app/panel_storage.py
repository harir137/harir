import reflex as rx

import json
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from app.models import PanelStateSnapshot


SNAPSHOT_ID = "panel"


@lru_cache(maxsize=1)
def _engine() -> Engine:
    url = os.environ.get("REFLEX_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "REFLEX_DB_URL or DATABASE_URL is required for panel persistence"
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def _read_snapshot() -> str | None:
    try:
        with _engine().connect() as connection:
            return connection.execute(
                select(PanelStateSnapshot.payload).where(
                    PanelStateSnapshot.id == SNAPSHOT_ID
                )
            ).scalar_one_or_none()
    except Exception as e:
        logging.exception(
            f"Error: panel snapshot read failed ({type(e).__name__})"
        )
        raise


def _write_snapshot(payload: str, *, seed: bool = False) -> None:
    try:
        statement = insert(PanelStateSnapshot).values(
            id=SNAPSHOT_ID, payload=payload
        )
        statement = (
            statement.on_conflict_do_nothing(
                index_elements=[PanelStateSnapshot.id]
            )
            if seed
            else statement.on_conflict_do_update(
                index_elements=[PanelStateSnapshot.id],
                set_={"payload": statement.excluded.payload},
            )
        )
        with _engine().begin() as connection:
            connection.execute(statement)
    except Exception as e:
        logging.exception(
            f"Error: panel snapshot write failed ({type(e).__name__})"
        )
        raise


def _decode_snapshot(payload: str) -> dict[str, object]:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Panel snapshot must be a JSON object")
        return data
    except (ValueError, TypeError) as e:
        logging.exception(f"Error: invalid panel snapshot ({type(e).__name__})")
        raise


def install_panel_storage(panel: object) -> None:
    original_path = Path(panel.DB_FILE).resolve()
    original_save = panel.save_db
    original_load = panel.load_db
    original_ensure = panel.ensure_default_link
    needs_initial_save = False

    def managed_file() -> bool:
        return Path(panel.DB_FILE).resolve() == original_path

    def database_configured() -> bool:
        return bool(
            os.environ.get("REFLEX_DB_URL") or os.environ.get("DATABASE_URL")
        )

    def save_db() -> None:
        nonlocal needs_initial_save
        original_save()
        if not managed_file() or not database_configured():
            return
        try:
            with Path(panel.DB_FILE).open("r", encoding="utf-8") as file:
                data = _decode_snapshot(file.read())
            data["public_domain"] = panel.CONFIG.get("public_domain", "")
            Path(panel.DB_FILE).write_text(
                json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
            )
            # The local JSON retains its legacy shape; only Postgres holds the secret.
            data["secret"] = panel.CONFIG["secret"]
            payload = json.dumps(data, ensure_ascii=False)
            _write_snapshot(payload, seed=needs_initial_save)
            if needs_initial_save:
                canonical = _read_snapshot()
                if canonical is None:
                    raise RuntimeError("Panel snapshot was not committed")
                if canonical != payload:
                    load_db()
            needs_initial_save = False
        except (OSError, ValueError, TypeError, KeyError) as e:
            logging.exception(
                f"Error: panel snapshot save failed ({type(e).__name__})"
            )
            raise

    def load_db() -> None:
        nonlocal needs_initial_save
        if not managed_file() or not database_configured():
            original_load()
            local_path = Path(panel.DB_FILE)
            if local_path.exists():
                try:
                    local_data = _decode_snapshot(
                        local_path.read_text(encoding="utf-8")
                    )
                    saved_domain = local_data.get("public_domain", "")
                    panel.CONFIG["public_domain"] = (
                        saved_domain if isinstance(saved_domain, str) else ""
                    )
                except (OSError, ValueError, TypeError) as e:
                    logging.exception(
                        f"Error: panel local snapshot load failed ({type(e).__name__})"
                    )
                    raise
            return
        payload = _read_snapshot()
        if payload is None:
            local_path = Path(panel.DB_FILE)
            if not local_path.exists():
                original_load()
                needs_initial_save = True
                return
            try:
                payload = local_path.read_text(encoding="utf-8")
                data = _decode_snapshot(payload)
                data.setdefault(
                    "public_domain", panel.CONFIG.get("public_domain", "")
                )
                data["secret"] = panel.CONFIG["secret"]
                # Insert-only: a concurrent startup may already have seeded the row.
                _write_snapshot(json.dumps(data, ensure_ascii=False), seed=True)
                payload = _read_snapshot()
                if payload is None:
                    raise RuntimeError("Panel snapshot seed was not committed")
            except (OSError, ValueError, TypeError, KeyError) as e:
                logging.exception(
                    f"Error: panel snapshot seed failed ({type(e).__name__})"
                )
                raise
        data = _decode_snapshot(payload)
        saved_domain = data.get("public_domain", "")
        panel.CONFIG["public_domain"] = (
            saved_domain if isinstance(saved_domain, str) else ""
        )
        secret = data.get("secret")
        if secret is not None:
            if not isinstance(secret, str) or not secret:
                raise ValueError("Invalid panel snapshot secret")
            panel.CONFIG["secret"] = secret
        # Legacy files contain a salted hash but not the old process secret. Keep
        # that hash unchanged: a password cannot be recovered or verified against
        # a newly generated secret. An external reset is needed if it no longer works.
        try:
            with tempfile.TemporaryDirectory(
                prefix="panel-snapshot-"
            ) as directory:
                staging_path = Path(directory) / "snapshot.json"
                # The original loaders need only the legacy fields, not the secret.
                staging_path.write_text(
                    json.dumps(
                        {
                            key: value
                            for key, value in data.items()
                            if key != "secret"
                        }
                    ),
                    encoding="utf-8",
                )
                active_path = panel.DB_FILE
                try:
                    panel.DB_FILE = staging_path
                    original_load()
                finally:
                    panel.DB_FILE = active_path
        except (OSError, ValueError, TypeError) as e:
            logging.exception(
                f"Error: panel snapshot load failed ({type(e).__name__})"
            )
            raise
        panel.CONFIG["public_domain"] = (
            saved_domain if isinstance(saved_domain, str) else ""
        )
        needs_initial_save = False

    async def ensure_default_link() -> None:
        await original_ensure()
        if managed_file() and needs_initial_save:
            save_db()

    panel.save_db = save_db
    panel.load_db = load_db
    panel.ensure_default_link = ensure_default_link
