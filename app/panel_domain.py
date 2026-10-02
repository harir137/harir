import reflex as rx
import ipaddress
import json
import logging
import copy
import math
import os
import re
from contextlib import suppress
from pathlib import Path

from fastapi import Depends, HTTPException, Request

import main as panel


_initial_db_path = Path(panel.DB_FILE).resolve()
_original_save_db = panel.save_db
_original_load_db = panel.load_db
_original_get_domain = panel.get_domain
panel.CONFIG.setdefault("public_domain", "")


def save_db():
    _original_save_db()
    database_is_canonical = Path(
        panel.DB_FILE
    ).resolve() == _initial_db_path and bool(
        os.environ.get("REFLEX_DB_URL") or os.environ.get("DATABASE_URL")
    )
    if database_is_canonical:
        return
    db_path = Path(panel.DB_FILE)
    try:
        data = json.loads(db_path.read_text(encoding="utf-8"))
        data["public_domain"] = panel.CONFIG["public_domain"]
        db_path.write_text(
            json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logging.exception(f"Error: {e}")
        raise


def load_db():
    _original_load_db()
    db_path = Path(panel.DB_FILE)
    database_is_canonical = db_path.resolve() == _initial_db_path and bool(
        os.environ.get("REFLEX_DB_URL") or os.environ.get("DATABASE_URL")
    )
    if db_path.exists() and not database_is_canonical:
        try:
            data = json.loads(db_path.read_text(encoding="utf-8"))
            saved = data.get("public_domain", "")
            panel.CONFIG["public_domain"] = (
                saved if isinstance(saved, str) else ""
            )
        except Exception as e:
            logging.exception(f"Error: {e}")
            raise


def get_domain() -> str:
    return panel.CONFIG["public_domain"] or _original_get_domain()


def valid_public_domain(domain: object) -> bool:
    if not isinstance(domain, str) or not domain or len(domain) > 253:
        return False
    with suppress(ipaddress.AddressValueError):
        ipaddress.IPv4Address(domain)
        return True
    if re.fullmatch(r"[0-9.]+", domain):
        return False
    return all(
        len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in domain.split(".")
    )


def inferred_public_domain(hostname: str | None) -> bool:
    if not valid_public_domain(hostname):
        return False
    with suppress(ipaddress.AddressValueError):
        return ipaddress.IPv4Address(hostname).is_global
    labels = hostname.lower().split(".")
    return (
        len(labels) > 1
        and labels[-1]
        not in {
            "localhost",
            "testserver",
            "local",
            "internal",
            "private",
            "lan",
            "localdomain",
        }
        and labels[0] not in {"localhost", "testserver"}
    )


panel.save_db = save_db
panel.load_db = load_db
panel.get_domain = get_domain


@panel.app.middleware("http")
async def capture_authenticated_panel_domain(request: Request, call_next):
    if (
        not panel.CONFIG["public_domain"]
        and _original_get_domain().lower() == "localhost"
    ):
        hostname = request.url.hostname
        token = request.cookies.get(panel.SESSION_COOKIE)
        if (
            token
            and inferred_public_domain(hostname)
            and await panel.is_valid_session(token)
        ):
            if (
                not panel.CONFIG["public_domain"]
                and _original_get_domain().lower() == "localhost"
            ):
                panel.CONFIG["public_domain"] = hostname.lower()
                panel.save_db()
    return await call_next(request)


@panel.app.get("/api/storage-status")
async def storage_status(_=Depends(panel.require_auth)):
    database_backed = bool(
        os.environ.get("REFLEX_DB_URL") or os.environ.get("DATABASE_URL")
    )
    return {"database_backed": database_backed}


@panel.app.get("/api/domain")
async def read_domain(_=Depends(panel.require_auth)):
    return {
        "public_domain": panel.CONFIG["public_domain"],
        "effective_domain": panel.get_domain(),
    }


@panel.app.post("/api/domain")
async def update_domain(request: Request, _=Depends(panel.require_auth)):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as e:
        logging.exception(f"Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from e
    domain = body.get("public_domain") if isinstance(body, dict) else None
    if not valid_public_domain(domain):
        raise HTTPException(
            status_code=400,
            detail="Enter a hostname or IPv4 address without a port or protocol",
        )
    panel.CONFIG["public_domain"] = domain
    panel.save_db()
    return {"public_domain": domain, "effective_domain": panel.get_domain()}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _clean_import_value(value: object) -> object:
    sensitive_keys = {
        "password",
        "password_hash",
        "auth_hash",
        "telegram_token",
        "token",
        "secret",
    }
    if isinstance(value, dict):
        return {
            key: _clean_import_value(item)
            for key, item in value.items()
            if str(key).lower() not in sensitive_keys
        }
    if isinstance(value, list):
        return [_clean_import_value(item) for item in value]
    return value


def _valid_import_link(record: object) -> tuple[str, dict[str, object]]:
    if not isinstance(record, dict):
        raise ValueError("Each inbound must be a JSON object")
    uid = record.get("uuid", record.get("id", record.get("uid")))
    if (
        not isinstance(uid, str)
        or not uid
        or len(uid) > 200
        or any(ord(char) < 32 for char in uid)
    ):
        raise ValueError("Each inbound must include a valid uuid or id")
    label = record.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ValueError(f"Inbound {uid} must include a valid label")
    for field in (
        "limit_bytes",
        "used_bytes",
        "max_connections",
        "quota",
        "usage",
    ):
        field_value = record.get(field)
        if field_value is not None and (
            isinstance(field_value, bool)
            or not isinstance(field_value, (int, float))
            or not math.isfinite(float(field_value))
            or field_value < 0
        ):
            raise ValueError(f"Inbound {uid} has an invalid {field}")
    if "active" in record and not isinstance(record["active"], bool):
        raise ValueError(f"Inbound {uid} has an invalid active status")
    if "created_at" in record and not isinstance(record["created_at"], str):
        raise ValueError(f"Inbound {uid} has an invalid created_at")
    if (
        "expires_at" in record
        and record["expires_at"] is not None
        and not isinstance(record["expires_at"], str)
    ):
        raise ValueError(f"Inbound {uid} has an invalid expiry")
    cleaned = _clean_import_value(record)
    cleaned.pop("vless_link", None)
    cleaned.pop("current_connections", None)
    return uid, cleaned


@panel.app.post("/api/import-panel-state")
async def import_panel_state(request: Request, _=Depends(panel.require_auth)):
    raw_body = await request.body()
    if len(raw_body) > 2_000_000:
        raise HTTPException(
            status_code=413, detail="Import payload is too large"
        )
    try:
        body = json.loads(raw_body, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeDecodeError) as e:
        logging.exception(f"Error: {e}")
        raise HTTPException(
            status_code=400, detail="Invalid JSON import payload"
        ) from e
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400, detail="Import payload must be a JSON object"
        )
    domain = body.get("public_domain")
    if not valid_public_domain(domain):
        raise HTTPException(
            status_code=400,
            detail="Enter a hostname without a protocol or port",
        )
    replace_existing = body.get("replace_existing", False)
    confirm_replace = body.get("confirm_replace", False)
    if not isinstance(replace_existing, bool) or not isinstance(
        confirm_replace, bool
    ):
        raise HTTPException(
            status_code=400, detail="Replacement confirmation must be boolean"
        )
    if replace_existing and not confirm_replace:
        raise HTTPException(
            status_code=400,
            detail="Confirm replacement before removing existing inbounds",
        )
    link_payload = body.get("links_payload")
    if not isinstance(link_payload, dict) or not isinstance(
        link_payload.get("links"), list
    ):
        raise HTTPException(
            status_code=400,
            detail="Links JSON must match the /api/links response",
        )
    raw_links = link_payload["links"]
    if len(raw_links) > 5000:
        raise HTTPException(
            status_code=400, detail="Too many inbounds in import"
        )
    try:
        imported: dict[str, dict[str, object]] = {}
        for item in raw_links:
            if len(json.dumps(item, ensure_ascii=False)) > 65536:
                raise ValueError("An inbound record is too large")
            uid, record = _valid_import_link(item)
            if uid in imported:
                raise ValueError(f"Duplicate inbound identifier: {uid}")
            if uid != "admin":
                imported[uid] = record
        addresses_payload = body.get("addresses_payload")
        new_addresses: list[str] | None = None
        if addresses_payload is not None:
            if not isinstance(addresses_payload, dict) or not isinstance(
                addresses_payload.get("addresses"), list
            ):
                raise ValueError(
                    "Addresses JSON must match the /api/addresses response"
                )
            address_values = addresses_payload["addresses"]
            if len(address_values) > 1000:
                raise ValueError("Too many addresses in import")
            new_addresses = []
            for address in address_values:
                if (
                    not isinstance(address, str)
                    or len(address) > 253
                    or not re.fullmatch(r"[a-zA-Z0-9\-_. ]+", address)
                    or not address.strip()
                ):
                    raise ValueError(
                        "Addresses must be non-empty hostnames or IP values"
                    )
                if address not in new_addresses:
                    new_addresses.append(address)
    except (TypeError, ValueError, OverflowError) as e:
        logging.exception("Unexpected error")
        raise HTTPException(status_code=400, detail=str(e)) from e

    previous_links = copy.deepcopy(panel.LINKS)
    previous_addresses = list(panel.CUSTOM_ADDRESSES)
    previous_domain = panel.CONFIG["public_domain"]
    db_path = Path(panel.DB_FILE)
    file_existed = db_path.exists()
    try:
        previous_file = db_path.read_bytes() if file_existed else b""
    except OSError as e:
        logging.exception(f"Error: failed to snapshot local panel file: {e}")
        raise HTTPException(
            status_code=503, detail="Panel storage is unavailable"
        ) from e
    next_links = {} if replace_existing else copy.deepcopy(panel.LINKS)
    protected_admin = panel.LINKS.get("admin")
    next_links.update(imported)
    if protected_admin is not None:
        next_links["admin"] = protected_admin
    elif "admin" not in next_links:
        next_links["admin"] = {
            "label": "admin",
            "limit_bytes": 0,
            "used_bytes": 0,
            "max_connections": 0,
            "created_at": panel.datetime.now(panel.timezone.utc).isoformat(),
            "active": True,
            "expires_at": None,
            "ports": [panel.DEFAULT_PORT],
        }
    async with panel.LINKS_LOCK:
        async with panel.CUSTOM_ADDRESSES_LOCK:
            panel.LINKS.clear()
            panel.LINKS.update(next_links)
            if new_addresses is not None:
                panel.CUSTOM_ADDRESSES[:] = new_addresses
            panel.CONFIG["public_domain"] = domain
            try:
                panel.save_db()
            except Exception as e:
                logging.exception(
                    f"Error: panel import persistence failed: {e}"
                )
                panel.LINKS.clear()
                panel.LINKS.update(previous_links)
                panel.CUSTOM_ADDRESSES[:] = previous_addresses
                panel.CONFIG["public_domain"] = previous_domain
                try:
                    if file_existed:
                        db_path.write_bytes(previous_file)
                    elif db_path.exists():
                        db_path.unlink()
                except OSError as restore_error:
                    logging.exception(
                        f"Error: failed to restore panel JSON after import failure: {restore_error}"
                    )
                raise HTTPException(
                    status_code=503,
                    detail="Import was not saved; storage is unavailable",
                ) from e
    return {
        "ok": True,
        "imported_links": len(imported),
        "address_count": len(panel.CUSTOM_ADDRESSES),
        "public_domain": domain,
        "replaced_existing": replace_existing,
    }


STORAGE_IMPORT_CARD = """      <div class="card" style="margin-top:14px;">
        <div class="card-hd"><div class="card-title" data-en="Import panel data" data-fa="وارد کردن اطلاعات پنل">Import panel data</div></div>
        <div style="font-size:11px;color:var(--text3);line-height:1.7;margin-bottom:12px" data-en="In another tab, open the old panel's authenticated /api/links URL and optionally /api/addresses, then paste the JSON responses here. Do not send credentials in chat. Passwords, hashes and Telegram tokens are not imported." data-fa="در زبانه‌ای دیگر، آدرس‌های احراز هویت‌شدهٔ /api/links و در صورت نیاز /api/addresses پنل قبلی را باز کنید و پاسخ JSON را اینجا بچسبانید. اطلاعات ورود را در گفتگو ارسال نکنید. رمزها، هش‌ها و توکن تلگرام وارد نمی‌شوند.">In another tab, open the old panel's authenticated /api/links URL and optionally /api/addresses, then paste the JSON responses here. Do not send credentials in chat. Passwords, hashes and Telegram tokens are not imported.</div>
        <div id="disk-storage-warning" role="status" style="display:none;background:rgba(202,162,74,.10);border:1px solid rgba(202,162,74,.25);padding:10px;border-radius:8px;color:var(--gold);font-size:11px;line-height:1.6;margin-bottom:12px" data-en="Database storage is unavailable. This panel is using local disk JSON, which will not survive an ephemeral deployment or instance replacement." data-fa="ذخیره‌سازی پایگاه‌داده در دسترس نیست. پنل از فایل محلی استفاده می‌کند که با استقرار موقت یا جایگزینی نمونه باقی نمی‌ماند.">Database storage is unavailable. This panel is using local disk JSON, which will not survive an ephemeral deployment or instance replacement.</div>
        <div class="fg"><label class="fl" for="import-domain" data-en="Public domain (host only; no protocol or port)" data-fa="دامنه عمومی (فقط میزبان، بدون پروتکل یا پورت)">Public domain (host only; no protocol or port)</label><input class="fi" id="import-domain" dir="ltr" placeholder="example.com" autocomplete="off"></div>
        <div class="fg"><label class="fl" for="import-links-json" data-en="/api/links JSON" data-fa="خروجی JSON از /api/links">/api/links JSON</label><textarea class="fi" id="import-links-json" rows="7" style="resize:vertical;font-family:monospace" placeholder='{"links":[]}'></textarea></div>
        <div class="fg"><label class="fl" for="import-addresses-json" data-en="/api/addresses JSON (optional)" data-fa="خروجی JSON از /api/addresses (اختیاری)">/api/addresses JSON (optional)</label><textarea class="fi" id="import-addresses-json" rows="3" style="resize:vertical;font-family:monospace" placeholder='{"addresses":[]}'></textarea></div>
        <label style="display:flex;align-items:flex-start;gap:8px;font-size:11px;color:var(--text2);line-height:1.5;margin:8px 0 12px"><input type="checkbox" id="replace-inbounds" style="margin-top:2px"><span data-en="Replace all current non-admin inbounds only after the confirmation prompt. Otherwise, imported IDs are merged and existing unrelated inbounds remain." data-fa="فقط پس از تأیید، همهٔ اینباندهای فعلی به‌جز مدیر جایگزین شوند. در غیر این صورت، شناسه‌های واردشده ادغام و سایر اینباندها حفظ می‌شوند.">Replace all current non-admin inbounds only after the confirmation prompt. Otherwise, imported IDs are merged and existing unrelated inbounds remain.</span></label>
        <button class="btn btn-gold" type="button" onclick="importPanelData()" data-en="Validate and import" data-fa="اعتبارسنجی و وارد کردن">Validate and import</button>
        <div id="import-status" role="status" aria-live="polite" style="font-size:11px;color:var(--text3);margin-top:10px"></div>
      </div>
"""

DOMAIN_CARD = """      <div class="card" style="margin-top: 14px;">
        <div class="card-hd"><div class="card-title" data-en="Public domain for VLESS links" data-fa="دامنه عمومی برای لینک‌های VLESS">Public domain for VLESS links</div></div>
        <div class="fg"><label class="fl" for="public-domain" data-en="Hostname or IPv4 (no port)" data-fa="نام دامنه یا IPv4 (بدون پورت)">Hostname or IPv4 (no port)</label><input class="fi" type="text" id="public-domain" dir="ltr" autocomplete="off" spellcheck="false" data-ph-en="example.com" data-ph-fa="example.com" placeholder="example.com" oninput="markDomainUnsaved()"></div>
        <div id="domain-status" role="status" aria-live="polite" style="font-size:11px;color:var(--text3);margin-bottom:12px" data-en="Loading domain…" data-fa="در حال بارگذاری دامنه…">Loading domain…</div>
        <div class="fr" style="margin-bottom:12px">
          <button class="btn btn-ghost" type="button" onclick="usePanelDomain()" data-en="Use current panel domain" data-fa="استفاده از دامنه فعلی پنل">Use current panel domain</button>
          <button class="btn btn-gold" type="button" onclick="saveDomain()" data-en="Save domain" data-fa="ذخیره دامنه">Save domain</button>
        </div>
        <div style="font-size:11px;color:var(--text3);line-height:1.6" data-en="Changes generated URLs only. DNS, TLS and WSS on port 443 must be configured externally." data-fa="فقط آدرس‌های تولیدشده را تغییر می‌دهد. DNS، TLS و WSS روی پورت ۴۴۳ باید جداگانه تنظیم شوند.">Changes generated URLs only. DNS, TLS and WSS on port 443 must be configured externally.</div>
      </div>
"""

DOMAIN_SCRIPT = """function domainStatus(key){
  const el=$m('domain-status');
  const messages={
    saved:{en:'Saved · Generated links use this domain.',fa:'ذخیره شد · لینک‌های تولیدشده از این دامنه استفاده می‌کنند.'},
    unsaved:{en:'Not saved yet · Save domain to update links.',fa:'هنوز ذخیره نشده · برای تغییر دامنه، آن را ذخیره کنید.'},
    fallback:{en:'Using automatic domain · Save a public domain to override it.',fa:'دامنه خودکار استفاده می‌شود · برای جایگزینی، دامنه عمومی را ذخیره کنید.'}
  };
  el.dataset.en=messages[key].en;
  el.dataset.fa=messages[key].fa;
  el.textContent=messages[key][lang];
  el.style.color=key==='saved'?'var(--green)':key==='unsaved'?'var(--yellow)':'var(--text3)';
}
function markDomainUnsaved(){domainStatus('unsaved');}
function usePanelDomain(){$m('public-domain').value=location.hostname;markDomainUnsaved();}
async function loadDomain(){
  try{const r=await fetch('/api/domain');if(r.status===401){showLogin();return;}if(!r.ok)throw new Error();const d=await r.json();$m('public-domain').value=d.public_domain||(d.effective_domain==='localhost'?location.hostname:d.effective_domain);domainStatus(d.public_domain?'saved':d.effective_domain==='localhost'?'unsaved':'fallback');}catch(e){toast('Failed to load domain',true);}
}
async function saveDomain(){
  const domain=$m('public-domain').value;
  try{const r=await fetch('/api/domain',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({public_domain:domain})});if(r.status===401){showLogin();return;}const d=await r.json();if(!r.ok)throw new Error(d.detail||'Failed to save domain');$m('public-domain').value=d.public_domain;domainStatus('saved');toast(lang==='fa'?'دامنه ذخیره شد':'Domain saved');
    await loadStats();
    await loadLinks();
  }catch(e){toast(e.message||'Failed to save domain',true);}
}
"""

IMPORT_SCRIPT = """async function loadStorageStatus(){
  try{const r=await fetch('/api/storage-status');if(r.status===401){showLogin();return;}if(!r.ok)throw new Error();const d=await r.json();if(!d.database_backed)$m('disk-storage-warning').style.display='block';}catch(e){$m('disk-storage-warning').style.display='block';}
}
async function importPanelData(){
  const status=$m('import-status'),linksText=$m('import-links-json').value.trim(),addressText=$m('import-addresses-json').value.trim(),domain=$m('import-domain').value.trim();
  if(!linksText||!domain){status.textContent=lang==='fa'?'دامنه و JSON لینک‌ها الزامی است.':'A public domain and links JSON are required.';return;}
  if(linksText.length>1500000||addressText.length>400000){status.textContent=lang==='fa'?'حجم JSON بیش از حد مجاز است.':'The pasted JSON exceeds the allowed size.';return;}
  let linksPayload,addressesPayload;try{linksPayload=JSON.parse(linksText);if(addressText)addressesPayload=JSON.parse(addressText);}catch(e){status.textContent=lang==='fa'?'JSON نامعتبر است.':'Invalid JSON. Nothing was changed.';return;}
  const replace=$m('replace-inbounds').checked;if(replace&&!confirm(lang==='fa'?'همهٔ اینباندهای موجود به‌جز مدیر جایگزین شوند؟':'Replace all existing non-admin inbounds? The protected admin inbound will remain.'))return;
  status.textContent=lang==='fa'?'در حال اعتبارسنجی و ذخیره…':'Validating and saving…';
  try{const r=await fetch('/api/import-panel-state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({links_payload:linksPayload,addresses_payload:addressesPayload,public_domain:domain,replace_existing:replace,confirm_replace:replace})});const result=await r.json();if(r.status===401){showLogin();return;}if(!r.ok)throw new Error(result.detail||'Import failed');status.textContent=lang==='fa'?`ذخیره شد؛ ${result.imported_links} اینباند وارد شد.`:`Saved successfully · ${result.imported_links} inbounds imported.`;$m('public-domain').value=result.public_domain;domainStatus('saved');await Promise.all([loadLinks(),loadAddrs(),loadStats()]);}catch(e){status.textContent=(e.message||'Import failed')+' · No success was reported.';}
}
"""


_domain_ui_installed = False


def install_domain_ui():
    global _domain_ui_installed
    if _domain_ui_installed:
        return
    html = panel.PANEL_HTML
    html = html.replace(
        '      <div class="card" style="margin-top: 14px;">\n        <div class="card-hd"><div class="card-title" data-en="Live Logs"',
        f'{DOMAIN_CARD}{STORAGE_IMPORT_CARD}      <div class="card" style="margin-top: 14px;">\n        <div class="card-hd"><div class="card-title" data-en="Live Logs"',
    )
    html = html.replace(
        "  loadSettings();\n  connectLogsWS();",
        "  loadSettings();\n  loadDomain();\n  loadStorageStatus();\n  connectLogsWS();",
    )
    html = html.replace(
        "await navigator.clipboard.writeText('https://'+location.host+'/sub/'+uid);",
        "await navigator.clipboard.writeText('https://'+(sData.domain||'localhost')+'/sub/'+encodeURIComponent(uid));",
    )
    html = html.replace(
        "async function loadStats(){",
        f"{DOMAIN_SCRIPT}{IMPORT_SCRIPT}async function loadStats(){{",
    )
    panel.PANEL_HTML = html
    _domain_ui_installed = True
