import reflex as rx
import ipaddress
import json
import logging
import re
from contextlib import suppress
from pathlib import Path

from fastapi import Depends, HTTPException, Request

import main as panel


_original_save_db = panel.save_db
_original_load_db = panel.load_db
_original_get_domain = panel.get_domain
panel.CONFIG.setdefault("public_domain", "")


def save_db():
    _original_save_db()
    try:
        db_path = Path(panel.DB_FILE)
        with db_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        data["public_domain"] = panel.CONFIG["public_domain"]
        with db_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
    except (OSError, ValueError, TypeError) as e:
        logging.exception(f"Error: {e}")
        raise


def load_db():
    panel.CONFIG["public_domain"] = ""
    _original_load_db()
    db_path = Path(panel.DB_FILE)
    if not db_path.exists():
        return
    try:
        with db_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        saved = data.get("public_domain", "")
        panel.CONFIG["public_domain"] = saved if isinstance(saved, str) else ""
    except (OSError, ValueError, TypeError) as e:
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
    unsaved:{en:'Not saved yet · Save domain to update links.',fa:'هنوز ذخیره نشده · برای تغییر لینک‌ها دامنه را ذخیره کنید.'},
    fallback:{en:'Using automatic domain · Save a public domain to override it.',fa:'دامنه خودکار استفاده می‌شود · برای جایگزینی، دامنه عمومی را ذخیره کنید.'}
  };
  el.dataset.en=messages[key].en;
  el.dataset.fa=messages[key].fa;
  el.textContent=messages[key][lang];
  el.style.color=key==='saved'?'var(--green)':key==='unsaved'?'var(--yellow)':'var(--text3)';
}

function markDomainUnsaved(){domainStatus('unsaved');}

function usePanelDomain(){
  $m('public-domain').value=location.hostname;
  markDomainUnsaved();
}

async function loadDomain(){
  try{
    const r=await fetch('/api/domain');
    if(r.status===401){showLogin();return;}
    if(!r.ok)throw new Error();
    const d=await r.json();
    $m('public-domain').value=d.public_domain||(d.effective_domain==='localhost'?location.hostname:d.effective_domain);
    domainStatus(d.public_domain?'saved':d.effective_domain==='localhost'?'unsaved':'fallback');
  }catch(e){toast('Failed to load domain',true);}
}

async function saveDomain(){
  const domain=$m('public-domain').value;
  try{
    const r=await fetch('/api/domain',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({public_domain:domain})
    });
    if(r.status===401){showLogin();return;}
    if(!r.ok){
      const d=await r.json();
      throw new Error(d.detail||'Failed to save domain');
    }
    const d=await r.json();
    $m('public-domain').value=d.public_domain;
    domainStatus('saved');
    toast(lang==='fa'?'دامنه ذخیره شد':'Domain saved');
    await loadStats();
    await loadLinks();
  }catch(e){toast(e.message||'Failed to save domain',true);}
}

"""


def install_domain_ui():
    html = panel.PANEL_HTML
    html = html.replace(
        '      <div class="card" style="margin-top: 14px;">\n        <div class="card-hd"><div class="card-title" data-en="Live Logs"',
        f'{DOMAIN_CARD}      <div class="card" style="margin-top: 14px;">\n        <div class="card-hd"><div class="card-title" data-en="Live Logs"',
    )
    html = html.replace(
        "  loadSettings();\n  connectLogsWS();",
        "  loadSettings();\n  loadDomain();\n  connectLogsWS();",
    )
    html = html.replace(
        "await navigator.clipboard.writeText('https://'+location.host+'/sub/'+uid);",
        "await navigator.clipboard.writeText('https://'+(sData.domain||'localhost')+'/sub/'+encodeURIComponent(uid));",
    )
    html = html.replace(
        "async function loadStats(){",
        f"{DOMAIN_SCRIPT}async function loadStats(){{",
    )
    panel.PANEL_HTML = html
