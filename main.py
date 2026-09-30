import asyncio
import json
import os
import hashlib
import secrets
import time
import re
import base64
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from collections import deque, defaultdict

from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    Depends,
)
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import httpx
import logging
import psutil

try:
    import telebot
    from telebot.async_telebot import AsyncTeleBot
    from telebot import types

    TELEBOT_AVAILABLE = True
except ImportError:
    TELEBOT_AVAILABLE = False
    print(
        "WARNING: Please install pyTelegramBotAPI to enable the Telegram Bot: pip install pyTelegramBotAPI"
    )

log_queue = deque(maxlen=150)


class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.append(msg)
        except Exception:
            pass


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("Luffy-Gateway")

q_handler = QueueHandler()
q_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
logger.addHandler(q_handler)
logging.getLogger("uvicorn.error").addHandler(q_handler)
logging.getLogger("uvicorn.access").addHandler(q_handler)

app = FastAPI(title="Luffy Panel", docs_url=None, redoc_url=None)

CONFIG = {
    "port": int(os.environ.get("PORT", 8000)),
    "secret": os.environ.get("SECRET_KEY", secrets.token_urlsafe(32)),
    "telegram_token": "",
    "telegram_admin_id": "",
    "bot_lang": "en",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connections: dict = {}
connections_lock = asyncio.Lock()
connection_sockets: dict = {}
link_ip_map: dict = defaultdict(set)
stats = {
    "total_bytes": 0,
    "total_requests": 0,
    "total_errors": 0,
    "start_time": time.time(),
}
error_logs: deque = deque(maxlen=50)
hourly_traffic: dict = defaultdict(int)
daily_traffic: dict = defaultdict(int)
http_client: httpx.AsyncClient | None = None

LINKS: dict = {}
LINKS_LOCK = asyncio.Lock()
CUSTOM_ADDRESSES: list = ["www.speedtest.net"]
CUSTOM_ADDRESSES_LOCK = asyncio.Lock()

notified_uids = set()

SESSION_COOKIE = "ren_session"
SESSION_TTL = 60 * 60 * 24 * 7
UNLIMITED_QUOTA_BYTES = 53687091200000
DEFAULT_PORT = 443

DB_FILE = "panel_db.json"
bot = None
bot_polling_task: asyncio.Task | None = None

# ── Telegram Bot i18n ─────────────────────────────────────────────────────────
BOT_I18N = {
    "en": {
        "btn_stats": "📊 Stats",
        "btn_users": "👥 Users",
        "btn_top": "🔝 Top Users",
        "btn_create": "➕ Create User",
        "btn_addip": "🌐 Add Clean IP",
        "btn_lang": "🇮🇷 فارسی",
        "welcome": "👑 <b>Welcome to Luffy Panel Telegram Bot!</b>\nManage your VLESS inbounds directly from your Telegram.",
        "lang_switched": "🌐 Language switched to <b>English</b>.",
        "stats": (
            "<b>📊 Server Status Dashboard</b>\n\n"
            "🌐 <b>Domain:</b> <code>{domain}</code>\n"
            "🔋 <b>CPU:</b> <code>{cpu:.1f}%</code>\n"
            "💾 <b>Memory:</b> <code>{mem:.1f}%</code>\n"
            "⏱ <b>Uptime:</b> <code>{uptime}</code>\n"
            "👥 <b>Active Connections:</b> <code>{active}</code>\n"
            "📈 <b>Total Traffic:</b> <code>{traffic} MB</code>\n"
            "🔑 <b>Total Inbounds:</b> <code>{links}</code>"
        ),
        "users_title": "<b>👥 Users List & Usage:</b>\n",
        "users_line": "• <b>{label}</b>: {used} / {limit} (⌛ {exp}) | {status}",
        "no_inbounds": "No inbounds found.",
        "status_on": "🟢 On",
        "status_off": "🔴 Off",
        "top_title": "<b>🔝 Top 5 Users by Usage:</b>\n",
        "top_line": "{i}. <b>{label}</b>: Used {used} of {limit}",
        "create_format": (
            "❌ <b>Invalid format.</b>\n"
            "Format: <code>/create [name] [limit_GB] [days]</code>\n"
            "Example: <code>/create Ali 15 30</code>"
        ),
        "create_bad_name": "❌ <b>Name must contain only English letters and numbers.</b>",
        "create_bad_limit": "❌ <b>Traffic limit must be a number.</b>",
        "create_bad_days": "❌ <b>Days valid must be an integer.</b>",
        "create_exists": "❌ <b>An inbound with the name '{label}' already exists.</b>",
        "create_success": (
            "✅ <b>Inbound Created Successfully!</b>\n\n"
            "👤 <b>Name:</b> <code>{label}</code>\n"
            "📊 <b>Quota:</b> <code>{quota}</code>\n"
            "⌛ <b>Expiry:</b> <code>{expiry}</code>\n\n"
            "🔗 <b>VLESS Link:</b>\n<code>{vless}</code>\n\n"
            "🌐 <b>Subscription URL:</b>\n<code>{sub}</code>"
        ),
        "unlimited": "Unlimited",
        "days_fmt": "{days} days",
        "addaddr_format": "❌ Format: <code>/addaddr [ip_or_domain]</code>",
        "addaddr_invalid": "❌ Invalid address format.",
        "addaddr_exists": "⚠️ Address '{addr}' is already in the list.",
        "addaddr_success": "✅ Clean IP/Domain <code>{addr}</code> successfully added.",
        "toggle_format": "❌ Format: <code>/{action} [username]</code>",
        "toggle_not_found": "❌ User '{name}' not found.",
        "toggle_success": "✅ User <code>{name}</code> successfully <b>{state}</b>.",
        "state_enabled": "Enabled",
        "state_disabled": "Disabled",
        "reset_format": "❌ Format: <code>/reset [username]</code>",
        "reset_success": "🔄 Usage reset to 0 for user <code>{name}</code>.",
        "create_guide": (
            "➕ <b>How to create a user:</b>\n\n"
            "Use the <code>/create</code> command. Format:\n"
            "<code>/create [name] [limit_GB] [days]</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/create Ali 15 30</code> (15GB limit, 30 days validity)\n"
            "• <code>/create Reza 0 0</code> (Unlimited, No Expiry)"
        ),
        "addip_guide": (
            "🌐 <b>How to add Clean IP:</b>\n\n"
            "Use the <code>/addaddr</code> command. Format:\n"
            "<code>/addaddr [ip_or_domain]</code>\n\n"
            "<b>Example:</b>\n"
            "• <code>/addaddr cf.example.com</code>\n"
            "• <code>/addaddr 1.1.1.1</code>"
        ),
        "quota_alert": (
            "⚠️ <b>Quota Alert!</b>\n"
            "User: <code>{label}</code> has reached their limit.\n"
            "Usage: <code>{used} / {limit}</code>"
        ),
        "expiry_alert": (
            "⏰ <b>Expiry Alert!</b>\n"
            "User: <code>{label}</code> has expired.\n"
            "Expiry date: <code>{exp}</code>"
        ),
    },
    "fa": {
        "btn_stats": "📊 آمار",
        "btn_users": "👥 کاربران",
        "btn_top": "🔝 پرمصرف‌ترین‌ها",
        "btn_create": "➕ ساخت کاربر",
        "btn_addip": "🌐 افزودن آی‌پی تمیز",
        "btn_lang": "🇬🇧 English",
        "welcome": "👑 <b>به ربات تلگرامی پنل لافی خوش اومدی!</b>\nاینباندهای VLESS رو مستقیم از تلگرام مدیریت کن.",
        "lang_switched": "🌐 زبان به <b>فارسی</b> تغییر یافت.",
        "stats": (
            "<b>📊 وضعیت سرور</b>\n\n"
            "🌐 <b>دامنه:</b> <code>{domain}</code>\n"
            "🔋 <b>پردازنده:</b> <code>{cpu:.1f}%</code>\n"
            "💾 <b>رم:</b> <code>{mem:.1f}%</code>\n"
            "⏱ <b>آپ‌تایم:</b> <code>{uptime}</code>\n"
            "👥 <b>اتصالات فعال:</b> <code>{active}</code>\n"
            "📈 <b>ترافیک کل:</b> <code>{traffic} MB</code>\n"
            "🔑 <b>تعداد کاربران:</b> <code>{links}</code>"
        ),
        "users_title": "<b>👥 لیست کاربران و میزان مصرف:</b>\n",
        "users_line": "• <b>{label}</b>: {used} / {limit} (⌛ {exp}) | {status}",
        "no_inbounds": "هیچ کاربری یافت نشد.",
        "status_on": "🟢 فعال",
        "status_off": "🔴 غیرفعال",
        "top_title": "<b>🔝 ۵ کاربر پرمصرف:</b>\n",
        "top_line": "{i}. <b>{label}</b>: مصرف {used} از {limit}",
        "create_format": (
            "❌ <b>فرمت اشتباه است.</b>\n"
            "فرمت: <code>/create [نام] [حجم_GB] [روز]</code>\n"
            "مثال: <code>/create Ali 15 30</code>"
        ),
        "create_bad_name": "❌ <b>نام فقط باید شامل حروف انگلیسی و عدد باشد.</b>",
        "create_bad_limit": "❌ <b>حجم ترافیک باید عدد باشد.</b>",
        "create_bad_days": "❌ <b>تعداد روز باید عدد صحیح باشد.</b>",
        "create_exists": "❌ <b>کاربری با نام «{label}» از قبل وجود دارد.</b>",
        "create_success": (
            "✅ <b>کاربر با موفقیت ساخته شد!</b>\n\n"
            "👤 <b>نام:</b> <code>{label}</code>\n"
            "📊 <b>حجم:</b> <code>{quota}</code>\n"
            "⌛ <b>انقضا:</b> <code>{expiry}</code>\n\n"
            "🔗 <b>لینک VLESS:</b>\n<code>{vless}</code>\n\n"
            "🌐 <b>آدرس اشتراک:</b>\n<code>{sub}</code>"
        ),
        "unlimited": "نامحدود",
        "days_fmt": "{days} روز",
        "addaddr_format": "❌ فرمت: <code>/addaddr [آی‌پی_یا_دامنه]</code>",
        "addaddr_invalid": "❌ فرمت آدرس نامعتبر است.",
        "addaddr_exists": "⚠️ آدرس «{addr}» قبلاً در لیست موجود است.",
        "addaddr_success": "✅ آی‌پی/دامنه‌ی <code>{addr}</code> با موفقیت اضافه شد.",
        "toggle_format": "❌ فرمت: <code>/{action} [نام‌کاربری]</code>",
        "toggle_not_found": "❌ کاربر «{name}» پیدا نشد.",
        "toggle_success": "✅ کاربر <code>{name}</code> با موفقیت <b>{state}</b> شد.",
        "state_enabled": "فعال",
        "state_disabled": "غیرفعال",
        "reset_format": "❌ فرمت: <code>/reset [نام‌کاربری]</code>",
        "reset_success": "🔄 مصرف کاربر <code>{name}</code> به صفر بازنشانی شد.",
        "create_guide": (
            "➕ <b>راهنمای ساخت کاربر:</b>\n\n"
            "از دستور <code>/create</code> استفاده کن. فرمت:\n"
            "<code>/create [نام] [حجم_GB] [روز]</code>\n\n"
            "<b>مثال‌ها:</b>\n"
            "• <code>/create Ali 15 30</code> (۱۵ گیگ، ۳۰ روز اعتبار)\n"
            "• <code>/create Reza 0 0</code> (نامحدود، بدون انقضا)"
        ),
        "addip_guide": (
            "🌐 <b>راهنمای افزودن آی‌پی تمیز:</b>\n\n"
            "از دستور <code>/addaddr</code> استفاده کن. فرمت:\n"
            "<code>/addaddr [آی‌پی_یا_دامنه]</code>\n\n"
            "<b>مثال:</b>\n"
            "• <code>/addaddr cf.example.com</code>\n"
            "• <code>/addaddr 1.1.1.1</code>"
        ),
        "quota_alert": (
            "⚠️ <b>هشدار اتمام حجم!</b>\n"
            "کاربر: <code>{label}</code> به سقف مصرف رسید.\n"
            "مصرف: <code>{used} / {limit}</code>"
        ),
        "expiry_alert": (
            "⏰ <b>هشدار انقضا!</b>\n"
            "کاربر: <code>{label}</code> منقضی شد.\n"
            "تاریخ انقضا: <code>{exp}</code>"
        ),
    },
}


def bot_lang() -> str:
    return (
        CONFIG.get("bot_lang")
        if CONFIG.get("bot_lang") in ("en", "fa")
        else "en"
    )


def L(key: str, **kwargs) -> str:
    lang = bot_lang()
    template = BOT_I18N.get(lang, BOT_I18N["en"]).get(key) or BOT_I18N[
        "en"
    ].get(key, key)
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def build_main_keyboard():
    if not TELEBOT_AVAILABLE:
        return None
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(L("btn_stats"), callback_data="tg_stats"),
        types.InlineKeyboardButton(L("btn_users"), callback_data="tg_users"),
        types.InlineKeyboardButton(L("btn_top"), callback_data="tg_top"),
        types.InlineKeyboardButton(
            L("btn_create"), callback_data="tg_create_guide"
        ),
        types.InlineKeyboardButton(
            L("btn_addip"), callback_data="tg_add_ip_guide"
        ),
        types.InlineKeyboardButton(
            L("btn_lang"), callback_data="tg_lang_toggle"
        ),
    )
    return kb


# ── Database Storage (JSON DB) ────────────────────────────────────────────────
def save_db():
    data = {
        "auth_hash": AUTH["password_hash"],
        "links": LINKS,
        "custom_addresses": CUSTOM_ADDRESSES,
        "telegram_token": CONFIG["telegram_token"],
        "telegram_admin_id": CONFIG["telegram_admin_id"],
        "bot_lang": CONFIG["bot_lang"],
    }
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving DB: {e}")


def load_db():
    global CUSTOM_ADDRESSES, LINKS
    if not os.path.exists(DB_FILE):
        return
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        AUTH["password_hash"] = data.get("auth_hash", AUTH["password_hash"])
        LINKS.clear()
        LINKS.update(data.get("links", {}))
        CUSTOM_ADDRESSES.clear()
        CUSTOM_ADDRESSES.extend(
            data.get("custom_addresses", ["www.speedtest.net"])
        )
        CONFIG["telegram_token"] = data.get("telegram_token", "")
        CONFIG["telegram_admin_id"] = data.get("telegram_admin_id", "")
        CONFIG["bot_lang"] = (
            data.get("bot_lang", "en")
            if data.get("bot_lang") in ("en", "fa")
            else "en"
        )
    except Exception as e:
        logger.error(f"Error loading DB: {e}")


# ── Auth ─────────────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return hashlib.sha256(f"{pw}{CONFIG['secret']}".encode()).hexdigest()


AUTH = {"password_hash": hash_password("admin")}
SESSIONS: dict = {}
SESSIONS_LOCK = asyncio.Lock()


async def create_session() -> str:
    token = secrets.token_urlsafe(32)
    async with SESSIONS_LOCK:
        SESSIONS[token] = time.time() + SESSION_TTL
    return token


async def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    async with SESSIONS_LOCK:
        exp = SESSIONS.get(token)
        if exp is None or exp < time.time():
            SESSIONS.pop(token, None)
            return False
        return True


async def destroy_session(token: str | None):
    if token:
        async with SESSIONS_LOCK:
            SESSIONS.pop(token, None)


async def require_auth(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not await is_valid_session(token):
        raise HTTPException(status_code=401, detail="unauthorized")
    return token


# ── Keep-alive ────────────────────────────────────────────────────────────────
async def keep_alive():
    while True:
        await asyncio.sleep(600)
        try:
            domain = get_domain()
            if domain and domain != "localhost":
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.get(f"https://{domain}/health")
        except Exception:
            pass


@app.on_event("startup")
async def startup():
    global http_client
    load_db()
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
    timeout = httpx.Timeout(30.0, connect=10.0)
    http_client = httpx.AsyncClient(
        limits=limits, timeout=timeout, follow_redirects=True
    )
    asyncio.create_task(keep_alive())
    await restart_telegram_bot()
    asyncio.create_task(telegram_notifier_cron())
    await ensure_default_link()


@app.on_event("shutdown")
async def shutdown():
    await _stop_telegram_bot()
    if http_client:
        await http_client.aclose()


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_domain() -> str:
    return (
        os.environ.get(
            "RENDER_EXTERNAL_URL",
            os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost"),
        )
        .replace("https://", "")
        .replace("http://", "")
    )


def generate_vless_link(
    uuid: str, remark: str = "Luffy", address: str = None, port: int = None
) -> str:
    domain = get_domain()
    addr = address if address else domain
    use_port = port if port else DEFAULT_PORT
    path = f"/ws/{uuid}"
    params = {
        "encryption": "none",
        "security": "tls",
        "type": "ws",
        "host": domain,
        "path": path,
        "sni": domain,
        "fp": "chrome",
        "alpn": "http/1.1",
    }
    query = "&".join(f"{k}={quote(v)}" for k, v in params.items())
    return f"vless://{uuid}@{addr}:{use_port}?{query}#{quote(remark)}"


def uptime() -> str:
    secs = int(time.time() - stats["start_time"])
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_size_to_bytes(value: float, unit: str) -> int:
    unit = unit.upper()
    if unit == "GB":
        return int(value * 1024 * 1024 * 1024)
    if unit == "MB":
        return int(value * 1024 * 1024)
    if unit == "KB":
        return int(value * 1024)
    return int(value)


def parse_expires_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        normalised = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def seconds_until_expiry(expires_at_str: str | None) -> int | None:
    exp = parse_expires_at(expires_at_str)
    if exp is None:
        return None
    remaining = (exp - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))


async def ensure_default_link():
    async with LINKS_LOCK:
        if not LINKS:
            LINKS["Default"] = {
                "label": "Default",
                "limit_bytes": 0,
                "used_bytes": 0,
                "max_connections": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "active": True,
                "expires_at": None,
                "ports": [DEFAULT_PORT],
            }


def get_client_ip(websocket: WebSocket) -> str:
    forwarded = websocket.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if websocket.client:
        return websocket.client.host
    return "unknown"


async def count_connections_for_link(uid: str) -> int:
    async with connections_lock:
        return sum(
            1 for info in connections.values() if info.get("uuid") == uid
        )


async def remove_ip_from_link(uid: str, ip: str):
    async with connections_lock:
        if uid in link_ip_map:
            link_ip_map[uid].discard(ip)
            if not link_ip_map[uid]:
                link_ip_map.pop(uid, None)


async def close_connections_for_link(uid: str):
    async with connections_lock:
        to_close = [
            cid for cid, info in connections.items() if info.get("uuid") == uid
        ]
    for cid in to_close:
        ws = connection_sockets.get(cid)
        if ws:
            try:
                await ws.close(code=1000, reason="link deleted")
            except Exception:
                pass
        async with connections_lock:
            connections.pop(cid, None)
        connection_sockets.pop(cid, None)
    async with connections_lock:
        link_ip_map.pop(uid, None)


# ── Telegram Bot Engine ───────────────────────────────────────────────────────
def _is_admin_chat(chat_id, admin_id) -> bool:
    if str(chat_id) != str(admin_id):
        logger.warning(
            f"Telegram Bot: ignored message from chat_id={chat_id} "
            f"(configured admin_id={admin_id!r} does not match)"
        )
        return False
    return True


async def _stop_telegram_bot():
    """Stop any previously running bot/poller before starting a new one.
    Without this, every restart (e.g. each time settings are saved) leaves
    the old long-polling loop running, and Telegram only allows ONE active
    getUpdates connection per bot token — the duplicate pollers fight each
    other (409 Conflict) and commands like /start can silently stop being
    delivered."""
    global bot, bot_polling_task
    if bot is not None:
        try:
            bot.stop_polling()
        except Exception:
            pass
    if bot_polling_task is not None and not bot_polling_task.done():
        bot_polling_task.cancel()
        try:
            await bot_polling_task
        except (asyncio.CancelledError, Exception):
            pass
    bot = None
    bot_polling_task = None


async def restart_telegram_bot():
    global bot, bot_polling_task
    if not TELEBOT_AVAILABLE:
        logger.warning(
            "Telegram Bot is disabled because pyTelegramBotAPI library is not installed."
        )
        return

    await _stop_telegram_bot()

    token = CONFIG.get("telegram_token")
    admin_id = CONFIG.get("telegram_admin_id")
    if not token or not admin_id:
        logger.info("Telegram Bot configuration is incomplete. Disabled.")
        return

    logger.info("Restarting Telegram Bot with official library...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.get(
                f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=true"
            )
            me_resp = await client.get(
                f"https://api.telegram.org/bot{token}/getMe"
            )
            me_data = me_resp.json()
            if not me_data.get("ok"):
                logger.error(
                    f"Telegram Bot: token rejected by Telegram ({me_data.get('description')}). Bot NOT started."
                )
                return
            logger.info(
                f"Telegram Bot: token verified, connected as @{me_data['result'].get('username')}"
            )
    except Exception as e:
        logger.error(
            f"Telegram Bot: could not reach Telegram API, bot NOT started: {e}"
        )
        return

    bot = AsyncTeleBot(token)

    @bot.message_handler(commands=["start"])
    async def cmd_start(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        await bot.send_message(
            message.chat.id,
            L("welcome"),
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )

    @bot.message_handler(commands=["stats"])
    async def cmd_stats(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        s_data = await get_internal_stats()
        await bot.send_message(
            message.chat.id, make_stats_text(s_data), parse_mode="HTML"
        )

    @bot.message_handler(commands=["users"])
    async def cmd_users(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        utext = await make_users_text()
        await bot.send_message(message.chat.id, utext, parse_mode="HTML")

    @bot.message_handler(commands=["top"])
    async def cmd_top(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        utext = await make_top_users_text()
        await bot.send_message(message.chat.id, utext, parse_mode="HTML")

    @bot.message_handler(commands=["create"])
    async def cmd_create(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        resp = await handle_create_command(message.text)
        await bot.send_message(message.chat.id, resp, parse_mode="HTML")

    @bot.message_handler(commands=["addaddr"])
    async def cmd_addaddr(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        resp = await handle_addaddr_command(message.text)
        await bot.send_message(message.chat.id, resp, parse_mode="HTML")

    @bot.message_handler(commands=["disable"])
    async def cmd_disable(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        resp = await handle_toggle_command(message.text, False)
        await bot.send_message(message.chat.id, resp, parse_mode="HTML")

    @bot.message_handler(commands=["enable"])
    async def cmd_enable(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        resp = await handle_toggle_command(message.text, True)
        await bot.send_message(message.chat.id, resp, parse_mode="HTML")

    @bot.message_handler(commands=["reset"])
    async def cmd_reset(message):
        if not _is_admin_chat(message.chat.id, admin_id):
            return
        resp = await handle_reset_command(message.text)
        await bot.send_message(message.chat.id, resp, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda call: True)
    async def handle_callback(call):
        if not _is_admin_chat(call.message.chat.id, admin_id):
            return
        await bot.answer_callback_query(call.id)

        if call.data == "tg_lang_toggle":
            CONFIG["bot_lang"] = "fa" if bot_lang() == "en" else "en"
            save_db()
            await bot.send_message(
                call.message.chat.id,
                L("lang_switched"),
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )
        elif call.data == "tg_stats":
            s_data = await get_internal_stats()
            await bot.send_message(
                call.message.chat.id,
                make_stats_text(s_data),
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )
        elif call.data == "tg_users":
            utext = await make_users_text()
            await bot.send_message(
                call.message.chat.id,
                utext,
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )
        elif call.data == "tg_top":
            utext = await make_top_users_text()
            await bot.send_message(
                call.message.chat.id,
                utext,
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )
        elif call.data == "tg_create_guide":
            await bot.send_message(
                call.message.chat.id,
                L("create_guide"),
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )
        elif call.data == "tg_add_ip_guide":
            await bot.send_message(
                call.message.chat.id,
                L("addip_guide"),
                parse_mode="HTML",
                reply_markup=build_main_keyboard(),
            )

    async def _run_polling(bot_instance):
        try:
            # NOTE: skip_pending_updates is intentionally NOT passed here — the
            # pyTelegramBotAPI version installed on this server forwards it into
            # _process_polling(), which rejects it (TypeError), crashing polling
            # every few seconds and preventing the bot from ever receiving
            # updates. Pending updates are already cleared above via
            # deleteWebhook?drop_pending_updates=true, so this isn't needed.
            await bot_instance.infinity_polling()
        except Exception as e:
            logger.error(
                f"Telegram Bot: polling loop stopped unexpectedly: {e}"
            )

    bot_polling_task = asyncio.create_task(_run_polling(bot))
    logger.info("Telegram Bot is now polling for updates.")


async def send_tg_message(text: str):
    global bot
    admin_id = CONFIG.get("telegram_admin_id")
    if bot and admin_id:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error sending TG notification: {e}")


def fmt_exp_py(ea: str | None) -> str:
    if not ea:
        return "∞"
    exp = parse_expires_at(ea)
    if not exp:
        return "∞"
    diff = exp - datetime.now(timezone.utc)
    seconds = diff.total_seconds()
    if seconds <= 0:
        return "Expired"
    days = int(seconds // 86400)
    if days > 0:
        return f"{days}d"
    hours = int(seconds // 3600)
    if hours > 0:
        return f"{hours}h"
    minutes = int(seconds // 60)
    return f"{minutes}m"


async def get_internal_stats():
    async with connections_lock:
        conn_count = len(connections)
    return {
        "active_connections": conn_count,
        "total_traffic_mb": round(stats["total_bytes"] / (1024 * 1024), 2),
        "total_requests": stats["total_requests"],
        "total_errors": stats["total_errors"],
        "uptime": uptime(),
        "links_count": len(LINKS),
        "domain": get_domain(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
    }


def make_stats_text(s_data) -> str:
    return L(
        "stats",
        domain=s_data.get("domain", "–"),
        cpu=s_data.get("cpu_percent", 0),
        mem=s_data.get("memory_percent", 0),
        uptime=s_data.get("uptime", "–"),
        active=s_data.get("active_connections", 0),
        traffic=s_data.get("total_traffic_mb", 0),
        links=s_data.get("links_count", 0),
    )


async def make_users_text() -> str:
    lines = [L("users_title")]
    async with LINKS_LOCK:
        items = list(LINKS.items())

    if not items:
        return L("no_inbounds")

    for uid, data in items:
        used = _fmt_bytes(data["used_bytes"])
        limit = (
            _fmt_bytes(data["limit_bytes"]) if data["limit_bytes"] > 0 else "∞"
        )
        ex = fmt_exp_py(data.get("expires_at"))
        status = L("status_on") if data["active"] else L("status_off")
        lines.append(
            L(
                "users_line",
                label=data["label"],
                used=used,
                limit=limit,
                exp=ex,
                status=status,
            )
        )

    return "\n".join(lines[:35])


async def make_top_users_text() -> str:
    lines = [L("top_title")]
    async with LINKS_LOCK:
        items = list(LINKS.items())
    if not items:
        return L("no_inbounds")

    sorted_items = sorted(
        items, key=lambda x: x[1].get("used_bytes", 0), reverse=True
    )[:5]
    for i, (uid, data) in enumerate(sorted_items, 1):
        used = _fmt_bytes(data["used_bytes"])
        limit = (
            _fmt_bytes(data["limit_bytes"]) if data["limit_bytes"] > 0 else "∞"
        )
        lines.append(
            L("top_line", i=i, label=data["label"], used=used, limit=limit)
        )
    return "\n".join(lines)


async def handle_create_command(text: str):
    parts = text.split()
    if len(parts) < 2:
        return L("create_format")
    label = parts[1]
    if not re.match(r"^[a-zA-Z0-9\-_. ]+$", label):
        return L("create_bad_name")

    limit_value = 0.0
    days_valid = 0

    if len(parts) >= 3:
        try:
            limit_value = float(parts[2])
        except ValueError:
            return L("create_bad_limit")

    if len(parts) >= 4:
        try:
            days_valid = int(parts[3])
        except ValueError:
            return L("create_bad_days")

    async with LINKS_LOCK:
        if label in LINKS:
            return L("create_exists", label=label)

    limit_bytes = (
        0 if limit_value <= 0 else parse_size_to_bytes(limit_value, "GB")
    )
    expires_at = None
    if days_valid > 0:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=days_valid)
        ).isoformat()

    uid = label
    async with LINKS_LOCK:
        LINKS[uid] = {
            "label": label,
            "limit_bytes": limit_bytes,
            "used_bytes": 0,
            "max_connections": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
            "expires_at": expires_at,
        }

    save_db()
    vless_link = generate_vless_link(
        uid, remark=f"Luffy-{label}", port=DEFAULT_PORT
    )
    sub_url = f"https://{get_domain()}/sub/{uid}"

    quota_str = _fmt_bytes(limit_bytes) if limit_bytes > 0 else L("unlimited")
    expiry_str = (
        L("days_fmt", days=days_valid) if days_valid > 0 else L("unlimited")
    )

    return L(
        "create_success",
        label=label,
        quota=quota_str,
        expiry=expiry_str,
        vless=vless_link,
        sub=sub_url,
    )


async def handle_addaddr_command(text: str) -> str:
    parts = text.split()
    if len(parts) < 2:
        return L("addaddr_format")
    addr = parts[1].strip()
    if not re.match(r"^[a-zA-Z0-9\-_. ]+$", addr):
        return L("addaddr_invalid")
    async with CUSTOM_ADDRESSES_LOCK:
        if addr in CUSTOM_ADDRESSES:
            return L("addaddr_exists", addr=addr)
        CUSTOM_ADDRESSES.append(addr)
    save_db()
    return L("addaddr_success", addr=addr)


async def handle_toggle_command(text: str, active_state: bool) -> str:
    parts = text.split()
    if len(parts) < 2:
        action_name = "enable" if active_state else "disable"
        return L("toggle_format", action=action_name)
    name = parts[1].strip()
    async with LINKS_LOCK:
        if name not in LINKS:
            return L("toggle_not_found", name=name)
        LINKS[name]["active"] = active_state
    save_db()
    state_str = L("state_enabled") if active_state else L("state_disabled")
    return L("toggle_success", name=name, state=state_str)


async def handle_reset_command(text: str) -> str:
    parts = text.split()
    if len(parts) < 2:
        return L("reset_format")
    name = parts[1].strip()
    async with LINKS_LOCK:
        if name not in LINKS:
            return L("toggle_not_found", name=name)
        LINKS[name]["used_bytes"] = 0
    save_db()
    return L("reset_success", name=name)


async def telegram_notifier_cron():
    while True:
        try:
            token = CONFIG.get("telegram_token")
            admin_id = CONFIG.get("telegram_admin_id")
            if not token or not admin_id:
                await asyncio.sleep(60)
                continue

            async with LINKS_LOCK:
                items = list(LINKS.items())

            for uid, data in items:
                if not data["active"]:
                    continue

                # Check Quota
                used = data["used_bytes"]
                limit = data["limit_bytes"]
                label = data["label"]

                if limit > 0 and used >= limit:
                    notif_key = f"quota_{uid}"
                    if notif_key not in notified_uids:
                        msg = L(
                            "quota_alert",
                            label=label,
                            used=_fmt_bytes(used),
                            limit=_fmt_bytes(limit),
                        )
                        await send_tg_message(msg)
                        notified_uids.add(notif_key)

                # Check Expiry
                expires_at_str = data.get("expires_at")
                if expires_at_str:
                    exp = parse_expires_at(expires_at_str)
                    if exp and exp < datetime.now(timezone.utc):
                        notif_key = f"expiry_{uid}"
                        if notif_key not in notified_uids:
                            msg = L(
                                "expiry_alert", label=label, exp=expires_at_str
                            )
                            await send_tg_message(msg)
                            notified_uids.add(notif_key)

        except Exception as e:
            logger.error(f"Error in notification cron: {e}")

        await asyncio.sleep(60)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    # NOTE: this used to return {"service": "Luffy Panel", ...} — a plaintext
    # admission that this server is a proxy panel, visible to anyone who simply
    # curls the domain. Active-probing/DPI systems check exactly this kind of
    # thing. Return something generic instead.
    return Response(content="OK", media_type="text/plain")


@app.get("/health")
async def health():
    async with connections_lock:
        conn_count = len(connections)
    return {"status": "ok", "connections": conn_count, "uptime": uptime()}


@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    password = str(body.get("password") or "")
    if hash_password(password) != AUTH["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = await create_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    await destroy_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    return {"authenticated": await is_valid_session(token)}


@app.post("/api/change-password")
async def api_change_password(request: Request, _=Depends(require_auth)):
    body = await request.json()
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    if hash_password(current) != AUTH["password_hash"]:
        raise HTTPException(
            status_code=400, detail="Current password is incorrect"
        )
    if len(new) < 4:
        raise HTTPException(
            status_code=400, detail="Password must be at least 4 characters"
        )
    AUTH["password_hash"] = hash_password(new)
    save_db()
    current_token = request.cookies.get(SESSION_COOKIE)
    async with SESSIONS_LOCK:
        SESSIONS.clear()
        if current_token:
            SESSIONS[current_token] = time.time() + SESSION_TTL
    return {"ok": True}


@app.get("/api/settings")
async def get_settings(_=Depends(require_auth)):
    return {
        "telegram_token": CONFIG["telegram_token"],
        "telegram_admin_id": CONFIG["telegram_admin_id"],
    }


@app.post("/api/settings")
async def update_settings(request: Request, _=Depends(require_auth)):
    body = await request.json()
    CONFIG["telegram_token"] = body.get("telegram_token", "").strip()
    CONFIG["telegram_admin_id"] = body.get("telegram_admin_id", "").strip()
    save_db()
    await restart_telegram_bot()
    return {"ok": True}


@app.get("/stats")
async def get_stats(_=Depends(require_auth)):
    async with connections_lock:
        conn_count = len(connections)
    return {
        "active_connections": conn_count,
        "total_traffic_mb": round(stats["total_bytes"] / (1024 * 1024), 2),
        "total_requests": stats["total_requests"],
        "total_errors": stats["total_errors"],
        "uptime": uptime(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recent_errors": list(error_logs)[-10:],
        "links_count": len(LINKS),
        "domain": get_domain(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "hourly_traffic": dict(hourly_traffic),
    }


@app.post("/api/links")
async def create_link(request: Request, _=Depends(require_auth)):
    body = await request.json()
    label = (body.get("label") or "New Link").strip()[:60]
    if not re.match(r"^[a-zA-Z0-9\-_. ]+$", label):
        raise HTTPException(
            status_code=400,
            detail="Inbound name must contain only English letters, numbers, and characters: - _ . space",
        )
    if not label:
        raise HTTPException(status_code=400, detail="Inbound name is required")
    async with LINKS_LOCK:
        if label in LINKS:
            raise HTTPException(
                status_code=400,
                detail="An inbound with this name already exists",
            )
    limit_value = float(body.get("limit_value") or 0)
    limit_unit = body.get("limit_unit") or "GB"
    limit_bytes = (
        0 if limit_value <= 0 else parse_size_to_bytes(limit_value, limit_unit)
    )
    max_conn = int(body.get("max_connections") or 0)
    if max_conn < 0:
        max_conn = 0
    days_valid = body.get("days_valid")
    expires_at: str | None = None
    if days_valid is not None:
        try:
            days_valid = int(days_valid)
            if days_valid > 0:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=days_valid)
                ).isoformat()
        except (ValueError, TypeError):
            pass
    uid = label
    async with LINKS_LOCK:
        LINKS[uid] = {
            "label": label,
            "limit_bytes": limit_bytes,
            "used_bytes": 0,
            "max_connections": max_conn,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
            "expires_at": expires_at,
        }
    save_db()
    return {
        "uuid": uid,
        "label": label,
        "limit_bytes": limit_bytes,
        "used_bytes": 0,
        "max_connections": max_conn,
        "active": True,
        "created_at": LINKS[uid]["created_at"],
        "expires_at": expires_at,
        "vless_link": generate_vless_link(
            uid, remark=f"Luffy-{label}", port=DEFAULT_PORT
        ),
    }


@app.get("/api/links")
async def list_links(_=Depends(require_auth)):
    result = []
    async with LINKS_LOCK:
        items = list(LINKS.items())
    for uid, data in items:
        result.append(
            {
                "uuid": uid,
                "label": data["label"],
                "limit_bytes": data["limit_bytes"],
                "used_bytes": data["used_bytes"],
                "max_connections": data.get("max_connections", 0),
                "active": data["active"],
                "created_at": data["created_at"],
                "expires_at": data.get("expires_at"),
                "current_connections": await count_connections_for_link(uid),
                "vless_link": generate_vless_link(
                    uid, remark=f"Luffy-{data['label']}", port=DEFAULT_PORT
                ),
            }
        )
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return {"links": result}


@app.patch("/api/links/{uid}")
async def toggle_link(uid: str, request: Request, _=Depends(require_auth)):
    body = await request.json()
    async with LINKS_LOCK:
        if uid not in LINKS:
            raise HTTPException(status_code=404, detail="link not found")
        if "active" in body:
            LINKS[uid]["active"] = bool(body["active"])
        if "limit_value" in body:
            limit_value = float(body.get("limit_value") or 0)
            limit_unit = body.get("limit_unit") or "GB"
            LINKS[uid]["limit_bytes"] = (
                0
                if limit_value <= 0
                else parse_size_to_bytes(limit_value, limit_unit)
            )
        if "reset_usage" in body and body["reset_usage"]:
            LINKS[uid]["used_bytes"] = 0
        if "label" in body:
            LINKS[uid]["label"] = str(body["label"])[:60]
        if "max_connections" in body:
            mc = int(body["max_connections"] or 0)
            LINKS[uid]["max_connections"] = mc if mc >= 0 else 0
        if "days_valid" in body:
            try:
                dv = int(body["days_valid"])
                if dv > 0:
                    LINKS[uid]["expires_at"] = (
                        datetime.now(timezone.utc) + timedelta(days=dv)
                    ).isoformat()
                else:
                    LINKS[uid]["expires_at"] = None
            except (ValueError, TypeError):
                pass
    save_db()
    return {"ok": True}


@app.delete("/api/links/{uid}")
async def delete_link(uid: str, _=Depends(require_auth)):
    async with LINKS_LOCK:
        LINKS.pop(uid, None)
    save_db()
    await close_connections_for_link(uid)
    return {"ok": True}


@app.get("/api/addresses")
async def list_addresses(_=Depends(require_auth)):
    async with CUSTOM_ADDRESSES_LOCK:
        return {"addresses": list(CUSTOM_ADDRESSES)}


@app.post("/api/addresses")
async def add_address(request: Request, _=Depends(require_auth)):
    body = await request.json()
    address = (body.get("address") or "").strip()
    if not address:
        raise HTTPException(status_code=400, detail="Address is required")
    if not re.match(r"^[a-zA-Z0-9\-_. ]+$", address):
        raise HTTPException(
            status_code=400,
            detail="Address must contain only English letters, numbers, and characters: - _ .",
        )
    async with CUSTOM_ADDRESSES_LOCK:
        if address in CUSTOM_ADDRESSES:
            raise HTTPException(
                status_code=400, detail="Address already exists"
            )
        CUSTOM_ADDRESSES.append(address)
    save_db()
    return {"ok": True, "addresses": list(CUSTOM_ADDRESSES)}


@app.delete("/api/addresses")
async def delete_all_addresses(_=Depends(require_auth)):
    async with CUSTOM_ADDRESSES_LOCK:
        CUSTOM_ADDRESSES.clear()
    save_db()
    return {"ok": True, "addresses": list(CUSTOM_ADDRESSES)}


@app.delete("/api/addresses/{index}")
async def delete_address(index: int, _=Depends(require_auth)):
    async with CUSTOM_ADDRESSES_LOCK:
        if 0 <= index < len(CUSTOM_ADDRESSES):
            CUSTOM_ADDRESSES.pop(index)
        else:
            raise HTTPException(status_code=404, detail="Address not found")
    save_db()
    return {"ok": True, "addresses": list(CUSTOM_ADDRESSES)}


# ── Live Logs WebSocket ───────────────────────────────────────────────────────
@app.websocket("/ws/live-logs")
async def ws_live_logs(websocket: WebSocket, token: str | None = None):
    await websocket.accept()
    if not token or not await is_valid_session(token):
        await websocket.close(code=1008, reason="Unauthorized")
        return
    for item in list(log_queue):
        await websocket.send_text(item)
    last_idx = len(log_queue)
    try:
        while True:
            await asyncio.sleep(0.5)
            curr = list(log_queue)
            if len(curr) > last_idx:
                for idx in range(last_idx, len(curr)):
                    await websocket.send_text(curr[idx])
                last_idx = len(curr)
            elif len(curr) < last_idx:
                last_idx = len(curr)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ── Landing Page Generator ────────────────────────────────────────────────────
def _fmt_bytes(b: int) -> str:
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f}GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f}MB"
    return f"{b / 1024:.1f}KB"


def generate_landing_page(link: dict, uid: str, addresses: list[str]) -> str:
    used = link["used_bytes"]
    limit = link["limit_bytes"]
    expires_at_str = link.get("expires_at")

    usage_str = (
        f"{_fmt_bytes(used)} / Unlimited"
        if limit == 0
        else f"{_fmt_bytes(used)} / {_fmt_bytes(limit)}"
    )
    pct = round((used / limit) * 100, 1) if limit > 0 else 0
    rem = limit - used if limit > 0 else -1
    rem_str = _fmt_bytes(rem) if rem >= 0 else "Unlimited"

    secs_left = seconds_until_expiry(expires_at_str)
    if secs_left is None:
        expiry_str = "Unlimited"
    elif secs_left == 0:
        expiry_str = "Expired"
    else:
        days = secs_left // 86400
        hours = (secs_left % 86400) // 3600
        expiry_str = f"{days} Days, {hours} Hours Left"

    configs = [
        generate_vless_link(
            uid, remark=f"Luffy-{link['label']}", port=DEFAULT_PORT
        )
    ]
    for i, addr in enumerate(addresses):
        configs.append(
            generate_vless_link(
                uid,
                remark=f"Luffy-{link['label']}-IP{i + 1}",
                address=addr,
                port=DEFAULT_PORT,
            )
        )

    configs_json = json.dumps(configs)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Luffy Connection Status</title>
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@300;400;500;600;700&family=Vazirmatn:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{
            background: #060608;
            font-family: 'Inter', 'Vazirmatn', sans-serif;
            color: rgba(255,255,255,0.92);
            padding: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }}
        .container {{
            width: 100%;
            max-width: 500px;
            background: rgba(12,12,18,0.97);
            border: 1px solid rgba(255,215,0,0.15);
            border-radius: 20px;
            padding: 24px;
            box-shadow: 0 0 24px rgba(255,215,0,0.1);
        }}
        .brand {{
            text-align: center;
            margin-bottom: 20px;
        }}
        .brand h1 {{
            font-family: 'Cinzel', serif;
            font-size: 22px;
            color: #FFD700;
            letter-spacing: 2px;
        }}
        .card {{
            background: rgba(20,20,28,0.9);
            border: 1px solid rgba(255,215,0,0.08);
            border-radius: 14px;
            padding: 16px;
            margin-bottom: 14px;
        }}
        .user-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
        }}
        .username {{
            font-size: 18px;
            font-weight: 700;
            color: #fff;
        }}
        .status-badge {{
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
        }}
        .status-active {{ background: rgba(74,222,128,0.15); color: #4ade80; border: 1px solid rgba(74,222,128,0.3); }}
        .status-expired {{ background: rgba(248,113,113,0.15); color: #f87171; border: 1px solid rgba(248,113,113,0.3); }}
        .label {{ font-size: 11px; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 1px; }}
        .val {{ font-size: 16px; font-weight: 600; color: #fff; margin-top: 2px; }}
        .progress-bar {{
            height: 6px;
            background: rgba(255,255,255,0.05);
            border-radius: 3px;
            overflow: hidden;
            margin: 12px 0;
        }}
        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #FFD700, #C8900A);
            transition: width 0.5s;
        }}
        .config-list {{
            margin-top: 15px;
        }}
        .config-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(28,28,40,0.8);
            border: 1px solid rgba(255,255,255,0.05);
            padding: 10px 12px;
            border-radius: 8px;
            margin-bottom: 8px;
            font-size: 12.5px;
        }}
        .config-name {{
            font-weight: 600;
            color: rgba(255,255,255,0.8);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 60%;
        }}
        .btn {{
            font-family: inherit;
            font-size: 11.5px;
            font-weight: 700;
            border-radius: 6px;
            padding: 5px 10px;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }}
        .btn-gold {{ background: #FFD700; color: #000; }}
        .btn-gold:hover {{ filter: brightness(1.1); }}
        .btn-ghost {{ background: rgba(255,255,255,0.05); color: #fff; border: 1px solid rgba(255,255,255,0.1); }}
        .btn-ghost:hover {{ background: rgba(255,255,255,0.1); }}
        .mo {{
            position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 200; display: none; align-items: center; justify-content: center; backdrop-filter: blur(8px);
        }}
        .mo.show {{ display: flex; }}
        .mo-box {{
            background: rgba(20,20,28,0.95); border: 1px solid rgba(255,215,0,0.2); border-radius: 18px; padding: 24px; width: 90%; max-width: 320px; text-align: center; position: relative;
        }}
        .mo-box img {{ max-width: 100%; border-radius: 8px; border: 3px solid rgba(255,215,0,0.15); margin-top: 15px; }}
        .mo-close {{ position: absolute; top: 12px; right: 12px; font-size: 16px; cursor: pointer; color: rgba(255,255,255,0.4); background: none; border: none; }}
        .toast {{
            position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%) translateY(16px); background: #0c0c10; color: #FFD700; border: 1px solid rgba(255,215,0,0.2); border-radius: 10px; padding: 10px 18px; font-size: 13px; font-weight: 600; opacity: 0; transition: all 0.3s; z-index: 999;
        }}
        .toast.show {{ opacity: 1; transform: translateX(-50%) translateY(0); }}
    </style>
</head>
<body>
    <div class="toast" id="toast">Copied!</div>
    <div class="container">
        <div class="brand">
            <h1>LUFFY GATEWAY</h1>
        </div>
        <div class="card">
            <div class="user-header">
                <span class="username">{link["label"]}</span>
                <span class="status-badge {"status-active" if link["active"] else "status-expired"}">
                    {"Active" if link["active"] else "Inactive"}
                </span>
            </div>
            
            <div class="progress-bar">
                <div class="progress-fill" style="width: {pct}%"></div>
            </div>

            <div style="display:flex; justify-content: space-between; margin-top: 10px;">
                <div>
                    <div class="label">Usage</div>
                    <div class="val">{usage_str}</div>
                </div>
                <div>
                    <div class="label">Remaining</div>
                    <div class="val">{rem_str}</div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="label">Time Validity / Expiration</div>
            <div class="val" style="color: #FFD700; font-size: 18px; font-weight: 700; margin-top: 4px;">{expiry_str}</div>
        </div>

        <h3 style="margin: 20px 0 10px 5px; font-size: 14px; letter-spacing: 1px; color: rgba(255,255,255,0.5);">AVAILABLE NODES</h3>
        <div class="config-list" id="config-list"></div>
    </div>

    <div class="mo" id="qr-modal" onclick="if(event.target===this)this.classList.remove('show')">
        <div class="mo-box">
            <button class="mo-close" onclick="document.getElementById('qr-modal').classList.remove('show')">✕</button>
            <h3 style="color:#FFD700; font-family:'Cinzel',serif; font-size:14px;">QR Code</h3>
            <img id="qr-img" src="" alt="QR">
        </div>
    </div>

    <script>
        const configs = {configs_json};
        const listEl = document.getElementById('config-list');
        
        function showToast(txt) {{
            const t = document.getElementById('toast');
            t.textContent = txt;
            t.className = 'toast show';
            clearTimeout(t.timer);
            t.timer = setTimeout(() => t.className = 'toast', 2500);
        }}

        function copyTxt(text) {{
            navigator.clipboard.writeText(text)
                .then(() => showToast('Copied Successfully!'))
                .catch(() => showToast('Failed to copy.'));
        }}

        function showQR(text) {{
            document.getElementById('qr-img').src = 'https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=' + encodeURIComponent(text);
            document.getElementById('qr-modal').classList.add('show');
        }}

        listEl.innerHTML = configs.map((cfg, i) => {{
            const parts = cfg.split('#');
            const remark = parts[1] ? decodeURIComponent(parts[1]) : 'Node ' + (i+1);
            return `
                <div class="config-item">
                    <span class="config-name">${{remark}}</span>
                    <div style="display:flex; gap: 5px;">
                        <button class="btn btn-ghost" onclick="copyTxt('${{cfg}}')">Copy</button>
                        <button class="btn btn-gold" onclick="showQR('${{cfg}}')">QR</button>
                    </div>
                </div>
            `;
        }}).join('');
    </script>
</body>
</html>"""
    return html


def generate_subscription_content(
    link: dict, uid: str, addresses: list[str]
) -> str:
    used = link["used_bytes"]
    limit = link["limit_bytes"]
    expires_at_str = link.get("expires_at")
    usage_str = (
        f"{_fmt_bytes(used)} / ∞"
        if limit == 0
        else f"{_fmt_bytes(used)} / {_fmt_bytes(limit)}"
    )
    secs_left = seconds_until_expiry(expires_at_str)
    if secs_left is None:
        expiry_str = "∞"
    elif secs_left == 0:
        expiry_str = "Expired"
    else:
        expiry_str = f"{secs_left // 86400} Days Left"

    status_node = generate_vless_link(
        uid,
        remark=f"📊 {usage_str} | ⏳ {expiry_str}",
        address="0.0.0.0",
        port=DEFAULT_PORT,
    )
    links_out = [status_node]

    links_out.append(
        generate_vless_link(
            uid, remark=f"Luffy-{link['label']}", port=DEFAULT_PORT
        )
    )
    for i, addr in enumerate(addresses):
        links_out.append(
            generate_vless_link(
                uid,
                remark=f"Luffy-{link['label']}-IP{i + 1}",
                address=addr,
                port=DEFAULT_PORT,
            )
        )

    return "\n".join(links_out)


@app.get("/sub/{uid}")
async def subscription_endpoint(uid: str, request: Request):
    async with LINKS_LOCK:
        link = LINKS.get(uid)
        if link is None:
            raise HTTPException(status_code=404, detail="link not found")
        link = dict(link)

    if not link["active"]:
        raise HTTPException(status_code=403, detail="link disabled")

    expires_at = parse_expires_at(link.get("expires_at"))
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="link expired")

    async with CUSTOM_ADDRESSES_LOCK:
        addresses = list(CUSTOM_ADDRESSES)

    ua = request.headers.get("user-agent", "").lower()
    accept = request.headers.get("accept", "").lower()
    is_browser = (
        any(x in ua for x in ["mozilla", "chrome", "safari", "opera", "edge"])
        and "text/html" in accept
    )

    if is_browser:
        return HTMLResponse(content=generate_landing_page(link, uid, addresses))

    sub_content = generate_subscription_content(link, uid, addresses)
    encoded = base64.b64encode(sub_content.encode()).decode()
    total_bytes = (
        link["limit_bytes"]
        if link["limit_bytes"] > 0
        else UNLIMITED_QUOTA_BYTES
    )
    expire_ts = 0
    if expires_at is not None:
        expire_ts = int(expires_at.timestamp())

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": 'attachment; filename="sub.txt"',
        "profile-update-interval": "6",
        "subscription-userinfo": f"upload={link['used_bytes']}; download=0; total={total_bytes}; expire={expire_ts}",
    }
    return Response(content=encoded, headers=headers)


# ── WebSocket tunnel ──────────────────────────────────────────────────────────
RELAY_BUF = 64 * 1024


async def parse_vless_header(first_chunk: bytes):
    if len(first_chunk) < 24:
        raise ValueError("chunk too small")
    pos = 1 + 16
    addon_len = first_chunk[pos]
    pos += 1 + addon_len
    command = first_chunk[pos]
    pos += 1
    port = int.from_bytes(first_chunk[pos : pos + 2], "big")
    pos += 2
    addr_type = first_chunk[pos]
    pos += 1
    if addr_type == 1:
        addr_bytes = first_chunk[pos : pos + 4]
        pos += 4
        address = ".".join(str(b) for b in addr_bytes)
    elif addr_type == 2:
        domain_len = first_chunk[pos]
        pos += 1
        address = first_chunk[pos : pos + domain_len].decode(
            "utf-8", errors="ignore"
        )
        pos += domain_len
    elif addr_type == 3:
        addr_bytes = first_chunk[pos : pos + 16]
        pos += 16
        address = ":".join(
            f"{addr_bytes[i]:02x}{addr_bytes[i + 1]:02x}"
            for i in range(0, 16, 2)
        )
    else:
        raise ValueError(f"unknown address type: {addr_type}")
    return command, address, port, first_chunk[pos:]


async def check_quota(uid: str, extra_bytes: int) -> bool:
    async with LINKS_LOCK:
        link = LINKS.get(uid)
        if link is None or not link["active"]:
            return False
        expires_at = parse_expires_at(link.get("expires_at"))
        if expires_at is not None and expires_at < datetime.now(timezone.utc):
            return False
        if link["limit_bytes"] == 0:
            return True
        return (link["used_bytes"] + extra_bytes) <= link["limit_bytes"]


async def add_usage(uid: str, n: int):
    async with LINKS_LOCK:
        if uid in LINKS:
            LINKS[uid]["used_bytes"] += n


async def ws_to_tcp(websocket, writer, conn_id, link_uid):
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            if not data:
                continue
            size = len(data)
            if not await check_quota(link_uid, size):
                await websocket.close(code=1008, reason="quota exceeded")
                break
            stats["total_bytes"] += size
            stats["total_requests"] += 1
            async with connections_lock:
                if conn_id in connections:
                    connections[conn_id]["bytes"] += size
            hourly_traffic[datetime.now(timezone.utc).strftime("%H:00")] += size
            daily_traffic[datetime.now(timezone.utc).strftime("%Y-%m-%d")] += (
                size
            )
            await add_usage(link_uid, size)
            try:
                writer.write(data)
                await writer.drain()
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            if not writer.is_closing():
                writer.write_eof()
        except Exception:
            pass


async def tcp_to_ws(websocket, reader, conn_id, link_uid):
    first = True
    try:
        while True:
            data = await reader.read(RELAY_BUF)
            if not data:
                break
            size = len(data)
            if not await check_quota(link_uid, size):
                await websocket.close(code=1008, reason="quota exceeded")
                break
            stats["total_bytes"] += size
            async with connections_lock:
                if conn_id in connections:
                    connections[conn_id]["bytes"] += size
            hourly_traffic[datetime.now(timezone.utc).strftime("%H:00")] += size
            daily_traffic[datetime.now(timezone.utc).strftime("%Y-%m-%d")] += (
                size
            )
            await add_usage(link_uid, size)
            try:
                await websocket.send_bytes(
                    (b"\x00\x00" + data) if first else data
                )
                first = False
            except Exception:
                break
    except Exception:
        pass


@app.websocket("/ws/{uuid}")
async def websocket_tunnel(websocket: WebSocket, uuid: str):
    await ensure_default_link()

    # IMPORTANT: all validation that doesn't require reading client data
    # happens BEFORE accept(). Calling websocket.close() before accept()
    # makes the ASGI server reply with a plain HTTP 403 instead of
    # completing the WebSocket upgrade (101) and then dropping the
    # connection. The latter is a strong, easily-scriptable fingerprint
    # for active-probing systems: "this server fully completes a WS
    # handshake for literally any /ws/<uuid> path, then closes it" is
    # exactly the kind of behavior DPI/censor probes look for. Rejecting
    # pre-handshake makes invalid requests look like a normal closed/
    # forbidden endpoint instead of a live VLESS server.
    async with LINKS_LOCK:
        link_data = LINKS.get(uuid)
        if link_data is None or not link_data["active"]:
            await websocket.close(code=1008)
            return
        max_conn = link_data.get("max_connections", 0)
        link_data_copy = dict(link_data)

    expires_at = parse_expires_at(link_data_copy.get("expires_at"))
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        await websocket.close(code=1008)
        return

    if max_conn > 0:
        current_conns = await count_connections_for_link(uuid)
        if current_conns >= max_conn:
            await websocket.close(code=1008)
            return

    await websocket.accept()
    writer = None
    conn_id = None
    client_ip = get_client_ip(websocket)
    try:
        first_msg = await asyncio.wait_for(websocket.receive(), timeout=15.0)
        if first_msg["type"] == "websocket.disconnect":
            return
        first_chunk = (
            first_msg.get("bytes") or (first_msg.get("text") or "").encode()
        )
        if not first_chunk:
            return

        try:
            command, address, port, initial_payload = await parse_vless_header(
                first_chunk
            )
        except ValueError as e:
            logger.warning(f"Invalid VLESS header: {e}")
            await websocket.close(code=1008, reason="invalid header")
            return

        conn_id = secrets.token_urlsafe(8)
        async with connections_lock:
            connections[conn_id] = {
                "uuid": uuid,
                "ip": client_ip,
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "bytes": 0,
            }
            connection_sockets[conn_id] = websocket
            link_ip_map[uuid].add(client_ip)

        size = len(first_chunk)
        stats["total_bytes"] += size
        stats["total_requests"] += 1
        async with connections_lock:
            if conn_id in connections:
                connections[conn_id]["bytes"] += size
        hourly_traffic[datetime.now(timezone.utc).strftime("%H:00")] += size
        daily_traffic[datetime.now(timezone.utc).strftime("%Y-%m-%d")] += size
        await add_usage(uuid, size)

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(address, port), timeout=10.0
        )

        if initial_payload:
            p_size = len(initial_payload)
            stats["total_bytes"] += p_size
            async with connections_lock:
                if conn_id in connections:
                    connections[conn_id]["bytes"] += p_size
            hourly_traffic[datetime.now(timezone.utc).strftime("%H:00")] += (
                p_size
            )
            daily_traffic[datetime.now(timezone.utc).strftime("%Y-%m-%d")] += (
                p_size
            )
            await add_usage(uuid, p_size)
            try:
                writer.write(initial_payload)
                await writer.drain()
            except Exception:
                pass

        task_up = asyncio.create_task(
            ws_to_tcp(websocket, writer, conn_id, uuid)
        )
        task_down = asyncio.create_task(
            tcp_to_ws(websocket, reader, conn_id, uuid)
        )
        done, pending = await asyncio.wait(
            {task_up, task_down}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        stats["total_errors"] += 1
        error_logs.append(
            {"error": str(exc), "time": datetime.now(timezone.utc).isoformat()}
        )
        logger.exception("WebSocket error")
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        if conn_id:
            async with connections_lock:
                info = connections.pop(conn_id, None)
                connection_sockets.pop(conn_id, None)
                if info:
                    uid = info.get("uuid")
                    ip = info.get("ip")
                    if uid and ip:
                        has_other = any(
                            c.get("uuid") == uid and c.get("ip") == ip
                            for c in connections.values()
                        )
                        if not has_other:
                            if uid in link_ip_map:
                                link_ip_map[uid].discard(ip)
                                if not link_ip_map[uid]:
                                    link_ip_map.pop(uid, None)


# ── HTML ──────────────────────────────────────────────────────────────────────
PANEL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title data-en="Luffy Panel" data-fa="LUFFY PANEL">Luffy Panel</title>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@300;400;500;600;700&family=Vazirmatn:wght@400;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --gold:#FFD700;--gold2:#FFC200;--gold3:#C8900A;--gold-dim:rgba(255,215,0,0.12);
  --black:#060608;--black2:#0c0c10;--black3:#111118;
  --surface:rgba(12,12,18,0.97);--surface2:rgba(20,20,28,0.9);--surface3:rgba(28,28,40,0.8);
  --border:rgba(255,215,0,0.1);--border2:rgba(255,215,0,0.2);
  --text:rgba(255,255,255,0.92);--text2:rgba(255,215,0,0.7);--text3:rgba(255,255,255,0.4);
  --white-neon:rgba(255,255,255,0.85);--white-glow:0 0 16px rgba(255,255,255,0.25);
  --gold-glow:0 0 20px rgba(255,215,0,0.4);
  --green:#4ade80;--green-dim:rgba(74,222,128,0.1);
  --red:#f87171;--red-dim:rgba(248,113,113,0.1);
  --yellow:#fbbf24;
  --nav-w:64px;
}
body.light-mode {
  --black: #f0f2f5; --black2: #ffffff; --black3: #e4e6eb;
  --surface: rgba(255,255,255,0.95); --surface2: #ffffff; --surface3: #f9fafb;
  --border: rgba(0,0,0,0.1); --border2: rgba(0,0,0,0.2);
  --text: #111827; --text2: #4b5563; --text3: #6b7280;
  --gold-dim: rgba(218, 165, 32, 0.15);
  --gold-glow: 0 4px 14px rgba(0,0,0,0.1);
}
html,body{height:100%;background:var(--black);transition: background 0.3s, color 0.3s;}
body{font-family:'Inter','Vazirmatn',sans-serif;color:var(--text);display:flex;min-height:100vh;}
body[dir="rtl"]{direction:rtl;text-align:right}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:rgba(255,215,0,0.2);border-radius:4px}
.bg-fixed{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 70% 50% at 50% -10%,var(--gold-dim),transparent 60%)}
.grid-fixed{position:fixed;inset:0;z-index:0;pointer-events:none;background-image:linear-gradient(rgba(128,128,128,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(128,128,128,0.05) 1px,transparent 1px);background-size:56px 56px}
.sidebar{position:fixed;left:0;top:0;bottom:0;width:var(--nav-w);background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;z-index:100;transition:all .3s cubic-bezier(.4,0,.2,1);backdrop-filter:blur(20px);}
.sidebar::after{content:'';position:absolute;top:0;right:0;bottom:0;width:1px;background:linear-gradient(180deg,transparent,rgba(255,215,0,0.3) 30%,rgba(255,215,0,0.3) 70%,transparent)}
.light-mode .sidebar::after{display:none;}
.sb-brand{padding:16px 0;display:flex;flex-direction:column;align-items:center;gap:2px;border-bottom:1px solid var(--border);flex-shrink:0}
.sb-hat{filter:drop-shadow(0 0 10px rgba(255,215,0,.6));transition:filter .3s}
.sb-hat:hover{filter:drop-shadow(0 0 18px rgba(255,215,0,.9))}
.sb-title{font-family:'Cinzel',serif;font-size:8px;letter-spacing:.18em;color:rgba(255,215,0,.6);text-transform:uppercase;white-space:nowrap;overflow:hidden}
.sb-nav{flex:1;display:flex;flex-direction:column;justify-content:flex-end;padding-bottom:12px;gap:2px;padding-left:8px;padding-right:8px}
.nav-item{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;padding:10px 6px;border-radius:12px;color:var(--text3);cursor:pointer;transition:all .2s cubic-bezier(.4,0,.2,1);border:1px solid transparent;position:relative;overflow:hidden;text-decoration:none;background:none;width:100%;font-family:inherit;}
.nav-item::before{content:'';position:absolute;inset:0;border-radius:12px;background:linear-gradient(135deg,var(--gold-dim),transparent);opacity:0;transition:opacity .2s}
.nav-item:hover{color:var(--gold);border-color:rgba(255,215,0,.12)}
.nav-item:hover::before{opacity:1}
.nav-item.active{color:var(--gold);border-color:rgba(255,215,0,.22);background:var(--gold-dim);box-shadow:0 0 16px rgba(255,215,0,.1),inset 0 1px 0 rgba(255,215,0,.12)}
.nav-item.active::before{opacity:1}
.nav-icon{width:18px;height:18px;flex-shrink:0;transition:transform .2s}
.nav-item:hover .nav-icon,.nav-item.active .nav-icon{transform:scale(1.1)}
.nav-label{font-size:8.5px;font-weight:600;letter-spacing:.05em;white-space:nowrap;overflow:hidden}
.nav-badge{position:absolute;top:5px;right:5px;background:var(--gold);color:#000;font-size:8px;font-weight:800;min-width:14px;height:14px;border-radius:7px;display:flex;align-items:center;justify-content:center;padding:0 3px}
.sb-bottom{padding:8px;border-top:1px solid var(--border);display:flex;flex-direction:column;gap:6px;flex-shrink:0}
.lang-row{display:flex;gap:4px}
.lang-btn{flex:1;padding:5px 2px;border:1px solid var(--border);border-radius:7px;background:none;color:var(--text3);font-size:9px;font-weight:700;cursor:pointer;transition:all .2s;font-family:inherit;letter-spacing:.05em}
.lang-btn.active{background:var(--gold-dim);border-color:var(--gold);color:var(--gold)}
.lang-btn:hover:not(.active){border-color:rgba(255,215,0,.15);color:rgba(255,215,0,.5)}
.logout-btn{display:flex;align-items:center;justify-content:center;padding:7px;border:1px solid rgba(248,113,113,.15);border-radius:8px;background:rgba(248,113,113,.06);color:rgba(248,113,113,.6);cursor:pointer;transition:all .2s;font-size:10px;gap:4px;font-weight:600;font-family:inherit}
.logout-btn:hover{background:rgba(248,113,113,.12);border-color:rgba(248,113,113,.3);color:var(--red)}
.theme-toggle{background:transparent;border:1px solid var(--border);color:var(--text3);border-radius:7px;padding:4px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.2s;}
.theme-toggle:hover{background:var(--surface3);color:var(--gold);border-color:var(--gold);}
.main{margin-left:var(--nav-w);flex:1;padding:24px 28px 48px;min-height:100vh;position:relative;z-index:1}
.page{display:none;animation:pgIn .35s ease}
.page.active{display:block}
@keyframes pgIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.page-header{margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.page-title{font-family:'Cinzel',serif;font-size:16px;font-weight:700;color:var(--text);letter-spacing:.04em}
.page-sub{font-size:11px;color:var(--text3);margin-top:3px;letter-spacing:.02em}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
.stat-card{background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;position:relative;overflow:hidden;transition:all .25s;animation:cIn .5s ease both}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(255,215,0,0.4),transparent)}
.light-mode .stat-card::before{display:none;}
.stat-card:hover{border-color:var(--border2);transform:translateY(-2px);box-shadow:var(--gold-glow)}
@keyframes cIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.stat-label{font-size:9.5px;color:var(--text3);font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.stat-val{font-size:20px;font-weight:700;color:var(--text);letter-spacing:-.02em}
.stat-unit{font-size:11px;font-weight:400;color:var(--text3)}
.card{background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:10px;position:relative;overflow:hidden;transition:all .25s;animation:cIn .5s ease both}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(255,215,0,0.25),transparent)}
.light-mode .card::before{display:none;}
.card-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-title{font-size:12px;font-weight:600;color:var(--text);display:flex;align-items:center;gap:6px}
.chart-container{height:170px;width:100%}
.btn{font-family:inherit;font-size:11.5px;font-weight:700;border-radius:8px;padding:7px 14px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;border:none;transition:all .2s;letter-spacing:.03em}
.btn-gold{background:linear-gradient(135deg,#FFD700,#C8900A);color:#000;box-shadow:0 0 16px rgba(255,215,0,.25)}
.btn-gold:hover{filter:brightness(1.1);transform:translateY(-1px);box-shadow:0 0 24px rgba(255,215,0,.4)}
.btn-ghost{background:var(--surface3);color:var(--text);border:1px solid var(--border)}
.btn-danger{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,113,113,.15)}
.btn-sm{padding:4px 9px;font-size:10.5px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tbl-wrap{overflow-x:auto}
.tbl{width:100%;border-collapse:collapse}
.tbl th{text-align:left;font-size:9.5px;font-weight:700;color:var(--text3);padding:9px 11px;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--border);background:var(--surface3)}
.tbl td{padding:9px 11px;border-bottom:1px solid var(--border);font-size:12.5px;vertical-align:middle}
.tag{display:inline-flex;align-items:center;padding:2px 7px;border-radius:4px;font-size:9px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}
.tag-vless{background:var(--gold-dim);color:var(--gold);border:1px solid var(--border)}
.tag-port{background:rgba(167,139,250,.1);color:#a78bfa;border:1px solid rgba(167,139,250,.2)}
.tag-on{background:var(--green-dim);color:var(--green);border:1px solid rgba(74,222,128,.2)}
.tag-off{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,113,113,.2)}
.pill{display:flex;align-items:center;gap:7px;font-size:11px}
.pill-used{color:var(--text);font-weight:600}
.pill-bar{flex:1;height:4px;background:var(--border);border-radius:2px;min-width:40px}
.pill-fill{height:100%;border-radius:2px;transition:width .4s}
.pill-lim{color:var(--text3);font-size:10px}
.toggle{width:32px;height:17px;border-radius:9px;background:var(--surface3);position:relative;cursor:pointer;transition:all .28s;border:1px solid var(--border);flex-shrink:0}
.toggle::after{content:'';position:absolute;width:11px;height:11px;border-radius:50%;background:var(--text3);top:2px;left:2px;transition:all .28s cubic-bezier(.4,0,.2,1)}
.toggle.on{background:var(--green);border-color:var(--green);box-shadow:0 0 10px rgba(74,222,128,.3)}
.toggle.on::after{left:17px;background:#fff}
.sys-bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.sys-fill{height:100%;border-radius:3px;transition:width .4s}
.sl-item{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border)}
.sl-k{color:var(--text3);font-size:11.5px}
.sl-v{color:var(--text);font-weight:600;font-size:11.5px}
.fg{display:flex;flex-direction:column;gap:4px;margin-bottom:11px}
.fl{font-size:9.5px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.08em}
.fi,.fs{padding:8px 12px;border-radius:8px;border:1px solid var(--border);font-family:inherit;font-size:12.5px;outline:none;color:var(--text);background:var(--surface);transition:all .2s}
.fi:focus,.fs:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(255,215,0,.08)}
.fr{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}
.fr .fg{margin-bottom:0;flex:1;min-width:90px}
.act-btn{font-family:inherit;font-size:9.5px;font-weight:700;border-radius:6px;padding:4px 8px;cursor:pointer;display:inline-flex;align-items:center;gap:3px;border:1px solid;transition:all .18s}
.act-copy{background:var(--gold-dim);color:var(--gold);border-color:var(--border)}
.act-sub{background:var(--green-dim);color:var(--green);border-color:rgba(74,222,128,.2)}
.act-qr{background:rgba(167,139,250,.1);color:#a78bfa;border-color:rgba(167,139,250,.2)}
.act-edit{background:rgba(251,191,36,.08);color:var(--yellow);border-color:rgba(251,191,36,.2)}
.act-del{background:var(--red-dim);color:var(--red);border-color:rgba(248,113,113,.18)}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(16px);background:var(--surface);color:var(--text);border:1px solid var(--border2);border-radius:10px;padding:12px 20px;font-size:13px;font-weight:600;opacity:0;transition:all .3s;z-index:999;backdrop-filter:blur(24px);box-shadow:var(--gold-glow)}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.mo{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;display:none;align-items:center;justify-content:center;backdrop-filter:blur(8px)}
.mo.show{display:flex}
.mo-box{background:var(--surface2);border:1px solid var(--border2);border-radius:18px;padding:24px;width:100%;max-width:460px;position:relative;box-shadow:var(--gold-glow);transform:scale(.92);opacity:0;transition:all .38s cubic-bezier(.34,1.56,.64,1)}
.mo.show .mo-box{transform:scale(1);opacity:1}
.mo-title{font-family:'Cinzel',serif;font-size:14px;font-weight:700;margin-bottom:16px;color:var(--gold);letter-spacing:.06em}
.mo-close{position:absolute;top:14px;right:14px;background:var(--surface3);border:1px solid var(--border);color:var(--text3);width:30px;height:30px;border-radius:7px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;}
.qr-box{text-align:center;padding:20px;background:var(--surface3);border-radius:12px;border:1px solid var(--border);margin-top:12px}
.qr-box img{max-width:200px;border-radius:8px;border:3px solid var(--border);box-shadow:var(--gold-glow)}
.tb{display:flex;align-items:center;gap:7px;margin-bottom:14px;flex-wrap:wrap}
.search-wrap{flex:1;min-width:160px;position:relative}
.search-wrap svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--text3)}
.search-wrap input{width:100%;padding:9px 12px 9px 34px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px;font-family:inherit;outline:none;}
.filter-chips{display:flex;gap:3px;padding:3px;background:var(--surface2);border:1px solid var(--border);border-radius:8px}
.chip{padding:7px 12px;border-radius:6px;font-size:11.5px;font-weight:700;color:var(--text3);cursor:pointer;border:none;background:none;transition:all .18s;font-family:inherit}
.chip.active{background:var(--gold);color:#000}
.m-cards{display:none;flex-direction:column;gap:12px}
.m-card{border:1px solid var(--border);border-radius:12px;padding:16px;background:var(--surface2)}
.m-card-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.m-card-acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}
.empty{text-align:center;padding:36px;color:var(--text3)}
.mob-hd{display:none;position:fixed;top:0;left:0;right:0;background:var(--surface);border-bottom:1px solid var(--border);z-index:90;align-items:center;justify-content:space-between;backdrop-filter:blur(20px);}
.mob-tl-group{display:flex;gap:10px;align-items:center;flex-direction:row;}
.logout-mob{display:none;color:var(--red) !important;}
.logout-mob:hover{background:var(--red-dim) !important;border-color:rgba(248,113,113,.3) !important;}

/* Alerts and Log sections */
.alerts-box {
  background: rgba(248, 113, 113, 0.08);
  border: 1px dashed rgba(248, 113, 113, 0.3);
  border-radius: 12px;
  padding: 14px;
  margin-bottom: 14px;
  display: none;
}
.alerts-title {
  color: var(--red);
  font-size: 12.5px;
  font-weight: 700;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.alert-item {
  font-size: 12px;
  margin-bottom: 4px;
  color: var(--text);
  display: flex;
  justify-content: space-between;
}
.live-logs-container {
  background: #000;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  font-family: monospace;
  font-size: 11px;
  color: #4ade80;
  height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
}

/* Login page */
.login-wrap{display:flex;align-items:center;justify-content:center;min-height:100vh;width:100%}
.login-box{background:var(--surface2);border:1px solid var(--border2);border-radius:20px;padding:36px 32px;width:100%;max-width:360px;box-shadow:var(--gold-glow)}
.login-logo{text-align:center;margin-bottom:28px}
.login-title{font-family:'Cinzel',serif;font-size:22px;font-weight:900;color:var(--gold);letter-spacing:.1em}
.login-sub{font-size:11px;color:var(--text3);margin-top:6px}
@media(max-width:768px){
  .mob-hd{display:flex;height:65px;padding:0 20px;}
  .mob-tl-group .lang-btn{font-size:13px;padding:7px 10px;border-radius:8px;}
  .theme-toggle{font-size:18px;padding:7px 10px;border-radius:8px;}
  .mob-hd span{font-size:22px !important;}
  .sidebar{transform:none !important;width:100% !important;height:78px;top:auto;bottom:0;border-right:none;border-top:1px solid var(--border);flex-direction:row;padding:0;background:var(--surface);box-shadow:0 -4px 20px rgba(0,0,0,0.5);}
  .light-mode .sidebar{box-shadow:0 -4px 20px rgba(0,0,0,0.06);}
  .sb-brand,.sb-bottom{display:none !important;}
  .sb-nav{flex-direction:row;width:100%;padding:0;align-items:center;justify-content:space-between;gap:0;}
  .nav-item{flex:1;padding:12px 0;border-radius:0;}
  .nav-icon{width:24px;height:24px;margin-bottom:5px;}
  .nav-label{font-size:10px;letter-spacing:0;}
  .nav-badge{top:6px;right:50%;transform:translateX(10px);min-width:18px;height:18px;font-size:10px;}
  .logout-mob{display:flex;}
  .main{margin-left:0;padding-top:85px;padding-left:18px;padding-right:18px;padding-bottom:100px;}
  .page-title{font-size:24px;}
  .page-sub{font-size:13px;margin-top:5px;}
  .btn{font-size:14px;padding:10px 18px;}
  .btn-sm{font-size:12px;padding:8px 14px;}
  .stats-row{grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;}
  .stat-card{padding:22px;border-radius:16px;}
  .stat-label{font-size:12px;margin-bottom:12px;}
  .stat-val{font-size:26px;}
  .stat-unit{font-size:14px;}
  .grid-2{grid-template-columns:1fr;gap:14px;margin-bottom:14px;}
  .card{padding:22px;border-radius:16px;margin-bottom:14px;}
  .card-title{font-size:16px;margin-bottom:16px;}
  .chart-container{height:220px;width:100%}
  #cpu-v,#mem-v{font-size:22px !important;}
  .sl-k,.sl-v{font-size:14px;padding:14px 0;}
  .tbl-wrap{display:none}
  .m-cards{display:flex;}
  .m-card{padding:18px;border-radius:14px;}
  .m-card-hd span{font-size:16px !important;}
  .pill-used{font-size:13px;}
  .pill-lim{font-size:12px;}
  .m-card-acts .act-btn{font-size:12px;padding:8px 14px;border-radius:8px;}
  .mo-box{padding:28px 24px;border-radius:20px;}
  .fi,.fs{font-size:16px;padding:12px 16px;}
  .fl{font-size:11px;margin-bottom:6px;}
}
@media(max-width:460px){.stats-row{grid-template-columns:1fr;gap:14px;}}
</style>
</head>
<body>
<div class="bg-fixed"></div>
<div class="grid-fixed"></div>
<div class="toast" id="toast"></div>

<!-- LOGIN PAGE -->
<div id="login-page" style="display:none;width:100%">
  <div class="login-wrap">
    <div class="login-box">
      <div class="login-logo">
        <svg width="52" height="44" viewBox="0 0 84 68" fill="none">
          <ellipse cx="42" cy="52" rx="40" ry="11" fill="#C8900A" opacity=".85"/>
          <ellipse cx="42" cy="52" rx="40" ry="11" fill="none" stroke="#FFD700" stroke-width="1.4" opacity=".6"/>
          <path d="M19 50 Q21 22 42 17 Q63 22 65 50" fill="#D4960C" stroke="#FFD700" stroke-width="1.4"/>
          <ellipse cx="42" cy="17" rx="23" ry="5.5" fill="#C8900A" stroke="#FFD700" stroke-width="1"/>
          <path d="M20 45 Q21.5 41.5 42 39.5 Q62.5 41.5 64 45" fill="none" stroke="#CC2200" stroke-width="4.5" stroke-linecap="round" opacity=".92"/>
        </svg>
        <div class="login-title">LUFFY PANEL</div>
        <div class="login-sub">Enter your password to continue</div>
      </div>
      <div class="fg">
        <label class="fl">PASSWORD</label>
        <input class="fi" type="password" id="login-pw" placeholder="••••••••" onkeydown="if(event.key==='Enter')doLogin()">
      </div>
      <button class="btn btn-gold" onclick="doLogin()" style="width:100%;justify-content:center;padding:12px;margin-top:6px;">LOGIN</button>
      <div id="login-err" style="color:var(--red);font-size:12px;margin-top:10px;text-align:center;display:none">Invalid password</div>
    </div>
  </div>
</div>

<!-- DASHBOARD -->
<div id="dashboard-page" style="display:none;width:100%">

  <!-- MOBILE HEADER -->
  <div class="mob-hd">
    <div class="mob-tl-group">
      <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn-mob">🌙</button>
      <div class="lang-row">
        <button class="lang-btn lang-en active" onclick="setLang('en')">EN</button>
        <button class="lang-btn lang-fa" onclick="setLang('fa')">FA</button>
      </div>
    </div>
    <span style="font-family:'Cinzel',serif;font-size:16px;font-weight:700;color:var(--gold);letter-spacing:1px;">LUFFY</span>
  </div>

  <!-- SIDEBAR -->
  <aside class="sidebar" id="sb">
    <div class="sb-brand">
      <div class="sb-hat">
        <svg width="36" height="30" viewBox="0 0 84 68" fill="none">
          <ellipse cx="42" cy="52" rx="40" ry="11" fill="#C8900A" opacity=".85"/>
          <ellipse cx="42" cy="52" rx="40" ry="11" fill="none" stroke="#FFD700" stroke-width="1.4" opacity=".6"/>
          <path d="M19 50 Q21 22 42 17 Q63 22 65 50" fill="#D4960C" stroke="#FFD700" stroke-width="1.4"/>
          <ellipse cx="42" cy="17" rx="23" ry="5.5" fill="#C8900A" stroke="#FFD700" stroke-width="1"/>
          <path d="M20 45 Q21.5 41.5 42 39.5 Q62.5 41.5 64 45" fill="none" stroke="#CC2200" stroke-width="4.5" stroke-linecap="round" opacity=".92"/>
          <ellipse cx="35" cy="24" rx="5" ry="3" fill="rgba(255,255,255,.1)" transform="rotate(-20 35 24)"/>
        </svg>
      </div>
      <div class="sb-title">LUFFY</div>
    </div>
    <nav class="sb-nav">
      <button class="nav-item active" data-page="dashboard">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        <span class="nav-label" data-en="Dashboard" data-fa="داشبورد">Dashboard</span>
      </button>
      <button class="nav-item" data-page="inbounds">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="23" y1="11" x2="17" y2="11"/><line x1="20" y1="8" x2="20" y2="14"/></svg>
        <span class="nav-label" data-en="Inbounds" data-fa="اینباندها">Inbounds</span>
        <span class="nav-badge" id="nb">0</span>
      </button>
      <button class="nav-item" data-page="traffic">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span class="nav-label" data-en="Traffic" data-fa="ترافیک">Traffic</span>
      </button>
      <button class="nav-item" data-page="addresses">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
        <span class="nav-label" data-en="Clean IP" data-fa="آی‌پی تمیز">Clean IP</span>
      </button>
      <button class="nav-item" data-page="security">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>
        <span class="nav-label" data-en="Security" data-fa="امنیت">Security</span>
      </button>
      <button class="nav-item logout-mob" onclick="doLogout()">
        <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        <span class="nav-label" data-en="Logout" data-fa="خروج">Logout</span>
      </button>
    </nav>
    <div class="sb-bottom">
      <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn-desk" style="margin-bottom:4px;font-size:12px">🌙 Theme</button>
      <div class="lang-row">
        <button class="lang-btn lang-en active" onclick="setLang('en')">EN</button>
        <button class="lang-btn lang-fa" onclick="setLang('fa')">FA</button>
      </div>
      <button class="logout-btn" onclick="doLogout()" style="margin-top:2px">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        <span data-en="Logout" data-fa="خروج">Logout</span>
      </button>
    </div>
  </aside>

  <!-- MAIN CONTENT -->
  <main class="main">

    <!-- Dashboard -->
    <section class="page active" id="page-dashboard">
      <div class="page-header">
        <div>
          <div class="page-title" data-en="Dashboard" data-fa="داشبورد">Dashboard</div>
          <div class="page-sub" id="last-up">–</div>
        </div>
      </div>

      <!-- Critical alerts section -->
      <div class="alerts-box" id="alerts-box">
        <div class="alerts-title">
          <span>⚠️</span>
          <span data-en="SYSTEM WARNINGS" data-fa="هشدارهای سیستم">SYSTEM WARNINGS</span>
        </div>
        <div id="alerts-list"></div>
      </div>

      <div class="stats-row">
        <div class="stat-card" style="animation-delay:.08s"><div class="stat-label" data-en="Traffic" data-fa="ترافیک">Traffic</div><div class="stat-val" id="sv-traffic">–<span class="stat-unit"> MB</span></div></div>
        <div class="stat-card" style="animation-delay:.16s"><div class="stat-label" data-en="Inbounds" data-fa="اینباندها">Inbounds</div><div class="stat-val" id="sv-links">–</div></div>
        <div class="stat-card" style="animation-delay:.24s"><div class="stat-label" data-en="Uptime" data-fa="آپتایم">Uptime</div><div class="stat-val" id="sv-uptime" style="font-size:15px">–</div></div>
        <div class="stat-card" style="animation-delay:.32s"><div class="stat-label" data-en="Domain" data-fa="دامنه">Domain</div><div class="stat-val" id="sv-domain" style="font-size:10px;word-break:break-all;font-weight:500">–</div></div>
      </div>
      <div class="grid-2">
        <div class="card">
          <div class="card-hd"><div class="card-title" data-en="CPU" data-fa="پردازنده">CPU</div><span id="cpu-v" style="font-size:17px;font-weight:700;color:var(--gold)">–%</span></div>
          <div class="sys-bar"><div class="sys-fill" id="cpu-b" style="background:var(--gold)"></div></div>
        </div>
        <div class="card">
          <div class="card-hd"><div class="card-title" data-en="Memory" data-fa="حافظه">Memory</div><span id="mem-v" style="font-size:17px;font-weight:700;color:var(--green)">–%</span></div>
          <div class="sys-bar"><div class="sys-fill" id="mem-b" style="background:var(--green)"></div></div>
        </div>
      </div>
      <div class="card">
        <div class="card-hd"><div class="card-title" data-en="Hourly Traffic" data-fa="ترافیک ساعتی">Hourly Traffic</div></div>
        <div class="chart-container"><canvas id="tc"></canvas></div>
      </div>
    </section>

    <!-- Inbounds -->
    <section class="page" id="page-inbounds">
      <div class="page-header">
        <div>
          <div class="page-title" data-en="Inbounds" data-fa="اینباندها">Inbounds</div>
          <div class="page-sub" data-en="VLESS over WebSocket · TLS" data-fa="VLESS روی WebSocket با TLS">VLESS over WebSocket · TLS</div>
        </div>
        <button class="btn btn-gold" onclick="showAddMo()" data-en="+ Add" data-fa="+ افزودن">+ Add</button>
      </div>
      <div class="tb">
        <div class="search-wrap">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input id="srch" data-ph-en="Search name…" data-ph-fa="جستجوی نام…" placeholder="Search name…" oninput="filterLinks()">
        </div>
        <div class="filter-chips">
          <button class="chip active" data-filter="all" onclick="setFilter('all',this)" data-en="All" data-fa="همه">All</button>
          <button class="chip" data-filter="active" onclick="setFilter('active',this)" data-en="Active" data-fa="فعال">Active</button>
          <button class="chip" data-filter="off" onclick="setFilter('off',this)" data-en="Off" data-fa="غیرفعال">Off</button>
        </div>
      </div>
      <div class="card" style="padding:0;overflow:hidden">
        <div class="tbl-wrap">
          <table class="tbl">
            <thead><tr>
              <th data-en="#" data-fa="#">#</th>
              <th data-en="Name" data-fa="نام">Name</th>
              <th data-en="Type" data-fa="نوع">Type</th>
              <th data-en="Usage" data-fa="مصرف">Usage</th>
              <th data-en="IPs" data-fa="آی‌پی">IPs</th>
              <th data-en="Expiry" data-fa="انقضا">Expiry</th>
              <th data-en="Status" data-fa="وضعیت">Status</th>
              <th data-en="Actions" data-fa="عملیات">Actions</th>
            </tr></thead>
            <tbody id="ltb"></tbody>
          </table>
        </div>
        <div class="m-cards" id="mcards"></div>
        <div class="empty" id="lempty" style="display:none" data-en="No inbounds found" data-fa="هیچ اینباندی یافت نشد">No inbounds found</div>
      </div>
    </section>

    <!-- Traffic -->
    <section class="page" id="page-traffic">
      <div class="page-header"><div><div class="page-title" data-en="Traffic" data-fa="ترافیک">Traffic</div><div class="page-sub" data-en="Statistics & Inbound comparison" data-fa="آمار و مقایسه مصرف کاربران">Statistics & Inbound comparison</div></div></div>
      <div class="grid-2" style="margin-bottom:14px">
        <div class="card">
          <div class="sl-item"><span class="sl-k" data-en="Total Traffic" data-fa="کل ترافیک">Total Traffic</span><span class="sl-v" id="t-tr">–</span></div>
          <div class="sl-item"><span class="sl-k" data-en="Total Requests" data-fa="کل درخواست‌ها">Total Requests</span><span class="sl-v" id="t-rq">–</span></div>
          <div class="sl-item"><span class="sl-k" data-en="Uptime" data-fa="آپتایم">Uptime</span><span class="sl-v" id="t-up">–</span></div>
        </div>
        <div class="card">
          <div class="card-hd"><div class="card-title" data-en="Inbound Traffic Share" data-fa="سهم ترافیک کاربران">Inbound Traffic Share</div></div>
          <div class="chart-container"><canvas id="inbound-chart"></canvas></div>
        </div>
      </div>
    </section>

    <!-- Clean IP -->
    <section class="page" id="page-addresses">
      <div class="page-header">
        <div><div class="page-title" data-en="Clean IP" data-fa="آی‌پی تمیز">Clean IP</div><div class="page-sub" data-en="Subscription alternative addresses" data-fa="آدرس‌های جایگزین اشتراک">Subscription alternative addresses</div></div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-danger" onclick="delAllAddrs()" data-en="Delete All" data-fa="پاک کردن همه">Delete All</button>
          <button class="btn btn-gold" onclick="showAddAddrMo()" data-en="+ Add" data-fa="+ افزودن">+ Add</button>
        </div>
      </div>
      <div class="card">
        <div style="font-size:12px;color:var(--text3);margin-bottom:12px" data-en="Default: www.speedtest.net" data-fa="پیش‌فرض: www.speedtest.net">Default: www.speedtest.net</div>
        <div id="addr-list"></div>
      </div>
    </section>

    <!-- Security & Settings -->
    <section class="page" id="page-security">
      <div class="page-header"><div><div class="page-title" data-en="Security & Settings" data-fa="امنیت و تنظیمات">Security & Settings</div><div class="page-sub" data-en="Settings, Password & Live logs" data-fa="تنظیمات، تغییر رمز پنل و لاگ‌های زنده">Settings, Password & Live logs</div></div></div>
      <div class="grid-2">
        <div class="card">
          <div class="card-hd"><div class="card-title" data-en="Telegram Bot Settings" data-fa="تنظیمات ربات تلگرام">Telegram Bot Settings</div></div>
          <div class="fg"><label class="fl" data-en="Telegram Bot Token" data-fa="توکن ربات تلگرام">Bot Token</label><input class="fi" type="text" id="tg-token" placeholder="123456:ABC-DEF..."></div>
          <div class="fg"><label class="fl" data-en="Telegram Admin ID" data-fa="شناسه عددی ادمین">Admin Chat ID</label><input class="fi" type="text" id="tg-admin-id" placeholder="987654321"></div>
          <button class="btn btn-gold" onclick="saveSettings()" style="margin-top:10px;width:100%;justify-content:center;" data-en="Save Bot Settings" data-fa="ذخیره تنظیمات ربات">Save Bot Settings</button>
        </div>
        <div class="card">
          <div class="card-hd"><div class="card-title" data-en="Change Password" data-fa="تغییر رمز عبور">Change Password</div></div>
          <div class="fg"><label class="fl" data-en="Current Password" data-fa="رمز فعلی">Current Password</label><input class="fi" type="password" id="cpw" data-ph-en="Current password" data-ph-fa="رمز فعلی" placeholder="Current password"></div>
          <div class="fg"><label class="fl" data-en="New Password" data-fa="رمز جدید">New Password</label><input class="fi" type="password" id="npw" data-ph-en="Min 4 chars" data-ph-fa="حداقل ۴ کاراکتر" placeholder="Min 4 chars"></div>
          <button class="btn btn-gold" onclick="chgPw()" style="margin-top:10px;width:100%;justify-content:center;" data-en="Update Password" data-fa="بروزرسانی رمز">Update Password</button>
        </div>
      </div>
      <div class="card" style="margin-top: 14px;">
        <div class="card-hd"><div class="card-title" data-en="Live Logs" data-fa="لاگ‌های زنده">Live Logs</div></div>
        <div class="live-logs-container" id="log-container">Initializing live logs connection...</div>
      </div>
    </section>

  </main>
</div>

<!-- Modals -->
<div class="mo" id="mo-add" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="mo-box">
    <button class="mo-close" onclick="document.getElementById('mo-add').classList.remove('show')">✕</button>
    <div class="mo-title" data-en="ADD INBOUND" data-fa="افزودن اینباند">ADD INBOUND</div>
    <div class="fg"><label class="fl" data-en="Remark" data-fa="توضیح">Remark</label><input class="fi" id="nl" data-ph-en="e.g. User 1" data-ph-fa="مثلاً کاربر ۱" placeholder="e.g. User 1"></div>
    <div class="fr">
      <div class="fg"><label class="fl" data-en="Traffic Limit" data-fa="محدودیت ترافیک">Traffic Limit</label><input class="fi" id="nv" type="number" min="0" step=".1" data-ph-en="0 = ∞" data-ph-fa="۰ = نامحدود" placeholder="0 = ∞"></div>
      <div class="fg" style="max-width:100px"><label class="fl" data-en="Unit" data-fa="واحد">Unit</label><select class="fs" id="nu"><option>GB</option></select></div>
    </div>
    <div class="fg"><label class="fl" data-en="Max IPs" data-fa="حداکثر آی‌پی">Max IPs</label><input class="fi" id="nc" type="number" min="0" data-ph-en="0 = ∞" data-ph-fa="۰ = نامحدود" placeholder="0 = ∞"></div>
    <div class="fg"><label class="fl" data-en="Days Valid" data-fa="روزهای اعتبار">Days Valid</label><input class="fi" id="nd" type="number" min="0" data-ph-en="0 = No expiry" data-ph-fa="۰ = بدون انقضا" placeholder="0 = No expiry"></div>
    <button class="btn btn-gold" onclick="createLink()" style="width:100%;justify-content:center;margin-top:12px;padding:12px;" data-en="CREATE" data-fa="ایجاد">CREATE</button>
  </div>
</div>

<div class="mo" id="mo-edit" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="mo-box">
    <button class="mo-close" onclick="document.getElementById('mo-edit').classList.remove('show')">✕</button>
    <div class="mo-title" id="et">EDIT INBOUND</div>
    <input type="hidden" id="eu">
    <div class="fg"><label class="fl" data-en="Name" data-fa="نام">Name</label><input class="fi" id="en2" readonly style="opacity:.5;cursor:not-allowed"></div>
    <div class="fr">
      <div class="fg"><label class="fl" data-en="Traffic Limit" data-fa="محدودیت ترافیک">Traffic Limit</label><input class="fi" id="el" type="number" min="0" step=".1" data-ph-en="0 = ∞" data-ph-fa="۰ = نامحدود" placeholder="0 = ∞"></div>
      <div class="fg" style="max-width:100px"><label class="fl" data-en="Unit" data-fa="واحد">Unit</label><select class="fs" id="eu2"><option>GB</option></select></div>
    </div>
    <div class="fg"><label class="fl" data-en="Max IPs" data-fa="حداکثر آی‌پی">Max IPs</label><input class="fi" id="ec" type="number" min="0" data-ph-en="0 = ∞" data-ph-fa="۰ = نامحدود" placeholder="0 = ∞"></div>
    <div class="fg"><label class="fl" data-en="Extend Days" data-fa="افزایش روزها">Extend Days</label><input class="fi" id="ed" type="number" min="0" data-ph-en="0 = no change" data-ph-fa="۰ = بدون تغییر" placeholder="0 = no change"></div>
    <div style="display:flex;gap:10px;margin-top:16px">
      <button class="btn btn-gold" onclick="saveEdit()" style="flex:1;justify-content:center;padding:12px;" data-en="SAVE" data-fa="ذخیره">SAVE</button>
      <button class="btn btn-danger" onclick="resetTraf()" style="padding:12px;" data-en="Reset" data-fa="بازنشانی ترافیک">Reset</button>
    </div>
  </div>
</div>

<div class="mo" id="mo-qr" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="mo-box" style="max-width:340px">
    <button class="mo-close" onclick="document.getElementById('mo-qr').classList.remove('show')">✕</button>
    <div class="mo-title" data-en="QR CODE" data-fa="کد QR">QR CODE</div>
    <div class="qr-box"><img id="qr-img" src="" alt="QR"></div>
    <div style="display:flex;gap:10px;margin-top:16px;justify-content:center">
      <button class="btn btn-gold btn-sm" onclick="dlQR()" style="padding:10px 16px;" data-en="Download" data-fa="دانلود">Download</button>
      <button class="btn btn-ghost btn-sm" onclick="document.getElementById('mo-qr').classList.remove('show')" style="padding:10px 16px;" data-en="Close" data-fa="بستن">Close</button>
    </div>
  </div>
</div>

<div class="mo" id="mo-addr" onclick="if(event.target===this)this.classList.remove('show')">
  <div class="mo-box">
    <button class="mo-close" onclick="document.getElementById('mo-addr').classList.remove('show')">✕</button>
    <div class="mo-title" data-en="ADD CLEAN IP" data-fa="افزودن آی‌پی تمیز">ADD CLEAN IP</div>
    <div class="fg"><label class="fl" data-en="IPs / Domains (one per line)" data-fa="آی‌پی‌ها / دامنه‌ها (هر خط یک)">IPs / Domains</label><textarea class="fi" id="na" rows="5" data-ph-en="8.8.8.8&#10;example.com" data-ph-fa="۸.۸.۸.۸&#10;example.com" placeholder="8.8.8.8&#10;example.com" style="resize:vertical;font-family:monospace"></textarea></div>
    <button class="btn btn-gold" onclick="addAddrs()" style="width:100%;justify-content:center;margin-top:12px;padding:12px;" data-en="ADD ALL" data-fa="افزودن همه">ADD ALL</button>
  </div>
</div>

<script>
function $(s){return document.querySelector(s);}
function $m(id){return document.getElementById(id);}
function esc(s){return String(s).replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

const langMap={
  en:{edit:'Edit',copy:'Copy',sub:'Sub',qr:'QR',del:'Del'},
  fa:{edit:'ویرایش',copy:'کپی',sub:'اشتراک',qr:'QR',del:'حذف'}
};
function tr(key){return(langMap[lang]&&langMap[lang][key])||langMap['en'][key]||key;}

let lang=localStorage.getItem('ll')||'en';
let theme=localStorage.getItem('theme')||'dark';
let allLinks=[];
let cf='all';
let sData={};
let tChart=null;
let iChart=null;
let allAddrs=[];
let isAuthenticated=false;
let defaultPort=443;
let logsWS=null;

// ── Theme ────────────────────────────────────────────────────────────────────
function setTheme(t){
  theme=t;
  if(t==='light')document.body.classList.add('light-mode');
  else document.body.classList.remove('light-mode');
  localStorage.setItem('theme',t);
  const icon=t==='light'?'☀️':'🌙';
  const mb=$m('theme-btn-mob');
  const db=$m('theme-btn-desk');
  if(mb)mb.innerHTML=icon;
  if(db)db.innerHTML=icon+' Theme';
  updChartColors();
}
function toggleTheme(){setTheme(theme==='dark'?'light':'dark');}

// ── Lang ─────────────────────────────────────────────────────────────────────
function setLang(l){
  lang=l;
  document.querySelectorAll('.lang-en').forEach(e=>e.classList.toggle('active',l==='en'));
  document.querySelectorAll('.lang-fa').forEach(e=>e.classList.toggle('active',l==='fa'));
  document.body.dir=l==='fa'?'rtl':'ltr';
  document.querySelectorAll('[data-en]').forEach(el=>{
    const v=el.getAttribute('data-'+l);
    if(v)el.textContent=v;
  });
  document.querySelectorAll('[data-ph-en]').forEach(el=>{
    const v=el.getAttribute('data-ph-'+l);
    if(v)el.placeholder=v;
  });
  localStorage.setItem('ll',l);
  filterLinks();
}

// ── Live Logs WebSocket ───────────────────────────────────────────────────────
function connectLogsWS(){
  if(logsWS) { try { logsWS.close(); } catch(e){} }
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = document.cookie.split('; ').find(row => row.startsWith('ren_session='))?.split('=')[1];
  if(!token) return;
  logsWS = new WebSocket(`${protocol}//${location.host}/ws/live-logs?token=${token}`);
  logsWS.onmessage = function(e){
    const container = $m('log-container');
    if(container){
      container.textContent += e.data + '\n';
      container.scrollTop = container.scrollHeight;
    }
  };
  logsWS.onerror = function(){
    $m('log-container').textContent = "Live log connection error. Reconnecting...";
  };
  logsWS.onclose = function(){
    setTimeout(connectLogsWS, 5000);
  };
}

// ── Auth ─────────────────────────────────────────────────────────────────────
async function checkAuth(){
  try{
    const r=await fetch('/api/me');
    const d=await r.json();
    if(d.authenticated){
      showDashboard();
    } else {
      showLogin();
    }
  } catch(e){showLogin();}
}

function showLogin(){
  isAuthenticated=false;
  $m('login-page').style.display='';
  $m('dashboard-page').style.display='none';
}

function showDashboard(){
  isAuthenticated=true;
  $m('login-page').style.display='none';
  $m('dashboard-page').style.display='';
  initChart();
  loadStats();
  loadLinks();
  loadAddrs();
  loadSettings();
  connectLogsWS();
}

async function doLogin(){
  const pw=$m('login-pw').value;
  $m('login-err').style.display='none';
  try{
    const r=await fetch('/api/login',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pw})
    });
    if(r.ok){
      $m('login-pw').value='';
      showDashboard();
    } else {
      $m('login-err').style.display='block';
    }
  } catch(e){$m('login-err').style.display='block';}
}

async function doLogout(){
  await fetch('/api/logout',{method:'POST'});
  showLogin();
}

// ── Navigation ───────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-page]').forEach(el=>{
  el.addEventListener('click',()=>switchPage(el.dataset.page));
});

function switchPage(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  const target=$m('page-'+id);
  if(target)target.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.page===id));
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg,err=false){
  const t=$m('toast');
  t.textContent=msg;
  t.className='toast'+(err?' err':'')+' show';
  clearTimeout(t._hide);
  t._hide=setTimeout(()=>t.classList.remove('show'),3000);
}

// ── Format helpers ────────────────────────────────────────────────────────────
function fmtB(b){
  if(!b||b===0)return'0 B';
  return b>=1073741824?(b/1073741824).toFixed(2)+' GB':
         b>=1048576?(b/1048576).toFixed(2)+' MB':
         (b/1024).toFixed(1)+' KB';
}
function fmtLim(b){
  if(!b||b===0)return'∞';
  const g=b/1073741824;
  return(g%1===0?g.toFixed(0):g.toFixed(1))+' GB';
}
function fmtExp(ea){
  if(!ea||ea===0)return'∞';
  const d=new Date(ea)-new Date();
  if(d<=0)return'Expired';
  const days=Math.floor(d/86400000);
  if(days>0)return days+'d';
  const hours=Math.floor(d/3600000);
  if(hours>0)return hours+'h';
  return Math.floor(d/60000)+'m';
}

// ── Links ─────────────────────────────────────────────────────────────────────
function setFilter(filter,el){
  cf=filter;
  document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
  if(el)el.classList.add('active');
  filterLinks();
}

function filterLinks(){
  const q=($m('srch')?.value||'').toLowerCase();
  let r=allLinks;
  if(cf==='active')r=r.filter(l=>l.active);
  else if(cf==='off')r=r.filter(l=>!l.active);
  if(q)r=r.filter(l=>l.label.toLowerCase().includes(q)||l.uuid.toLowerCase().includes(q));
  renderLinks(r);
}

function processAlertsAndCharts(){
  const alertsList = $m('alerts-list');
  const alertsBox = $m('alerts-box');
  alertsList.innerHTML = '';
  let alertCount = 0;

  allLinks.forEach(l => {
    const u = l.used_bytes || 0;
    const lim = l.limit_bytes || 0;
    const pct = lim > 0 ? (u / lim) * 100 : 0;
    
    if(lim > 0 && pct >= 90){
      alertCount++;
      alertsList.innerHTML += `
        <div class="alert-item">
          <span style="font-weight:600;">🔴 Inbound '${esc(l.label)}' is near quota limit:</span>
          <span>${pct.toFixed(1)}% Used</span>
        </div>`;
    }
    
    if(l.expires_at){
      const diff = new Date(l.expires_at) - new Date();
      const days = diff / 86400000;
      if(days > 0 && days <= 3){
        alertCount++;
        alertsList.innerHTML += `
          <div class="alert-item">
            <span style="font-weight:600;">🟡 Inbound '${esc(l.label)}' will expire soon:</span>
            <span>${days.toFixed(1)} Days Left</span>
          </div>`;
      }
    }
  });

  if(alertCount > 0){
    alertsBox.style.display = 'block';
  } else {
    alertsBox.style.display = 'none';
  }

  if(iChart){
    const sorted = [...allLinks].sort((a,b)=>(b.used_bytes||0)-(a.used_bytes||0)).slice(0, 8);
    iChart.data.labels = sorted.map(x=>x.label);
    iChart.data.datasets[0].data = sorted.map(x=>Math.round((x.used_bytes||0)/(1024*1024)));
    iChart.update();
  }
}

function renderLinks(links){
  const tb=$m('ltb');
  const em=$m('lempty');
  const mc=$m('mcards');
  if(!links||!links.length){
    tb.innerHTML='';
    mc.innerHTML='';
    em.style.display='block';
    const emptyText=em.getAttribute('data-'+lang)||em.getAttribute('data-en')||'No inbounds found';
    em.textContent=emptyText;
    return;
  }
  em.style.display='none';
  let idx=links.length;
  const rows=links.map(l=>{
    const u=l.used_bytes||0;
    const lim=l.limit_bytes||0;
    const pct=lim>0?Math.min(100,(u/lim)*100):0;
    const col=pct>90?'var(--red)':pct>70?'var(--yellow)':'var(--gold)';
    const ex=fmtExp(l.expires_at);
    const ec=ex==='Expired'?'var(--red)':ex==='∞'?'var(--text3)':'var(--text2)';
    const i=idx--;
    const cc=l.current_connections||0;
    const mc2=l.max_connections||0;
    return{l,pct,col,ex,ec,i,cc,mc2,u,lim};
  });

  const editText=tr('edit');
  const copyText=tr('copy');
  const subText=tr('sub');
  const qrText=tr('qr');
  const delText=tr('del');

  tb.innerHTML=rows.map(r=>`<tr>
    <td style="color:var(--text3);font-size:10.5px">${r.i}</td>
    <td style="font-weight:600">${esc(r.l.label)}</td>
    <td><span class="tag tag-vless">VLESS</span></td>
    <td><div class="pill"><span class="pill-used">${fmtB(r.u)}</span><div class="pill-bar"><div class="pill-fill" style="width:${r.pct}%;background:${r.col}"></div></div><span class="pill-lim">${fmtLim(r.lim)}</span></div></td>
    <td style="font-size:11px;font-weight:600;color:${r.mc2>0&&r.cc>=r.mc2?'var(--red)':'var(--text2)'}">${r.cc}/${r.mc2||'∞'}</td>
    <td style="font-size:10.5px;font-weight:700;color:${r.ec}">${r.ex}</td>
    <td><span class="tag ${r.l.active?'tag-on':'tag-off'}">${r.l.active?'On':'Off'}</span></td>
    <td><div style="display:flex;gap:3px;align-items:center;flex-wrap:wrap">
      <button class="toggle ${r.l.active?'on':''}" data-uid="${r.l.uuid}" onclick="togLink(this)"></button>
      <button class="act-btn act-edit" onclick="showEditMo('${r.l.uuid}')">${editText}</button>
      <button class="act-btn act-copy" onclick="cpLink('${esc(r.l.vless_link||'')}')">${copyText}</button>
      <button class="act-btn act-sub" onclick="cpSub('${r.l.uuid}')">${subText}</button>
      <button class="act-btn act-qr" onclick="showQR('${esc(r.l.vless_link||'')}')">${qrText}</button>
      <button class="act-btn act-del" onclick="delLink('${r.l.uuid}')">${delText}</button>
    </div></td>
  </tr>`).join('');

  mc.innerHTML=rows.map(r=>`<div class="m-card">
    <div class="m-card-hd">
      <div style="display:flex;align-items:center;gap:7px">
        <span style="font-size:11px;color:var(--text3)">#${r.i}</span>
        <span style="font-weight:600;font-size:14px">${esc(r.l.label)}</span>
        <span class="tag tag-vless">VLESS</span>
      </div>
      <button class="toggle ${r.l.active?'on':''}" data-uid="${r.l.uuid}" onclick="togLink(this)"></button>
    </div>
    <div class="pill"><span class="pill-used">${fmtB(r.u)}</span><div class="pill-bar"><div class="pill-fill" style="width:${r.pct}%;background:${r.col}"></div></div><span class="pill-lim">${fmtLim(r.lim)}</span></div>
    <div style="font-size:11.5px;color:${r.ec};margin-top:6px;font-weight:600">⏳ ${r.ex} · ${r.cc}/${r.mc2||'∞'} IPs</div>
    <div class="m-card-acts">
      <button class="act-btn act-edit" onclick="showEditMo('${r.l.uuid}')">${editText}</button>
      <button class="act-btn act-copy" onclick="cpLink('${esc(r.l.vless_link||'')}')">${copyText}</button>
      <button class="act-btn act-sub" onclick="cpSub('${r.l.uuid}')">${subText}</button>
      <button class="act-btn act-qr" onclick="showQR('${esc(r.l.vless_link||'')}')">${qrText}</button>
      <button class="act-btn act-del" onclick="delLink('${r.l.uuid}')">${delText}</button>
    </div>
  </div>`).join('');
  
  processAlertsAndCharts();
}

async function togLink(el){
  const uid=el.dataset.uid;
  const l=allLinks.find(x=>x.uuid===uid);
  if(!l)return;
  const na=!l.active;
  try{
    const r=await fetch('/api/links/'+uid,{
      method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({active:na})
    });
    if(!r.ok)throw new Error();
    l.active=na;
    filterLinks();
    loadStats();
  }catch(e){toast('Failed to toggle',true);}
}

function showAddMo(){$m('mo-add').classList.add('show');}

async function createLink(){
  const label=$m('nl').value.trim()||'New Link';
  if(!/^[a-zA-Z0-9\-_. ]+$/.test(label)){toast('Only English letters allowed',true);return;}
  const v=parseFloat($m('nv').value)||0;
  const mc=parseInt($m('nc').value)||0;
  const days=parseInt($m('nd').value)||0;
  try{
    const r=await fetch('/api/links',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({label,limit_value:v,limit_unit:'GB',max_connections:mc,days_valid:days})
    });
    if(!r.ok)throw new Error();
    toast('Created');
    $m('nl').value='';$m('nv').value='';$m('nc').value='';$m('nd').value='';
    $m('mo-add').classList.remove('show');
    await loadLinks();
    await loadStats();
  }catch(e){toast('Error creating link',true);}
}

function showEditMo(uid){
  const l=allLinks.find(x=>x.uuid===uid);
  if(!l)return;
  $m('eu').value=uid;
  $m('en2').value=l.label;
  $m('el').value=l.limit_bytes>0?(l.limit_bytes/1073741824):'';
  $m('ec').value=l.max_connections>0?l.max_connections:'';
  $m('ed').value='';
  $m('et').textContent=(lang==='fa'?'ویرایش: ':'EDIT: ')+l.label;
  $m('mo-edit').classList.add('show');
}

async function saveEdit(){
  const uid=$m('eu').value;
  const v=parseFloat($m('el').value)||0;
  const mc=parseInt($m('ec').value)||0;
  const days=parseInt($m('ed').value)||0;
  const body={limit_value:v,limit_unit:'GB',max_connections:mc};
  if(days>0)body.days_valid=days;
  try{
    const r=await fetch('/api/links/'+uid,{
      method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)
    });
    if(!r.ok)throw new Error();
    toast('Updated');
    $m('mo-edit').classList.remove('show');
    await loadLinks();
  }catch(e){toast('Error updating',true);}
}

async function resetTraf(){
  const uid=$m('eu').value;
  if(!confirm('Reset traffic for this inbound?'))return;
  try{
    const r=await fetch('/api/links/'+uid,{
      method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({reset_usage:true})
    });
    if(!r.ok)throw new Error();
    toast('Traffic reset');
    await loadLinks();
  }catch(e){toast('Error resetting',true);}
}

async function delLink(uid){
  if(!confirm('Delete this inbound?'))return;
  try{
    const r=await fetch('/api/links/'+uid,{method:'DELETE'});
    if(!r.ok)throw new Error();
    toast('Deleted');
    await loadLinks();
    await loadStats();
  }catch(e){toast('Error deleting',true);}
}

function cpLink(txt){
  if(!txt){toast('No link to copy',true);return;}
  navigator.clipboard.writeText(txt).then(()=>toast('Copied!')).catch(()=>toast('Failed to copy',true));
}

async function cpSub(uid){
  try{
    await navigator.clipboard.writeText('https://'+location.host+'/sub/'+uid);
    toast('Sub URL copied!');
  }catch(e){toast('Failed to copy',true);}
}

function showQR(txt){
  if(!txt){toast('No QR data',true);return;}
  $m('qr-img').src='https://api.qrserver.com/v1/create-qr-code/?size=280x280&data='+encodeURIComponent(txt);
  $m('mo-qr').classList.add('show');
}

function dlQR(){
  const a=document.createElement('a');
  a.href=$m('qr-img').src;
  a.download='luffy-qr.png';
  a.click();
}

// ── Stats & Settings API ──────────────────────────────────────────────────────
async function loadSettings(){
  try {
    const r = await fetch('/api/settings');
    if (r.ok) {
      const d = await r.json();
      $m('tg-token').value = d.telegram_token || '';
      $m('tg-admin-id').value = d.telegram_admin_id || '';
    }
  } catch(e){}
}

async function saveSettings(){
  const tok = $m('tg-token').value.trim();
  const adm = $m('tg-admin-id').value.trim();
  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({telegram_token: tok, telegram_admin_id: adm})
    });
    if (r.ok) {
      toast('Bot settings saved & restarted');
    } else {
      toast('Failed to save settings', true);
    }
  } catch(e){toast('Error saving settings', true);}
}

async function loadStats(){
  try{
    const r=await fetch('/stats');
    if(r.status===401){showLogin();return;}
    if(!r.ok)throw new Error();
    sData=await r.json();
    $m('sv-traffic').innerHTML=(sData.total_traffic_mb||0)+'<span class="stat-unit"> MB</span>';
    $m('sv-links').textContent=sData.links_count||0;
    $m('sv-uptime').textContent=sData.uptime||'–';
    $m('sv-domain').textContent=sData.domain||'–';
    $m('nb').textContent=sData.links_count||0;
    $m('last-up').textContent='Updated '+new Date().toLocaleTimeString();
    if($m('t-tr'))$m('t-tr').textContent=(sData.total_traffic_mb||0)+' MB';
    if($m('t-rq'))$m('t-rq').textContent=(sData.total_requests||0).toLocaleString();
    if($m('t-up'))$m('t-up').textContent=sData.uptime||'–';
    if(sData.cpu_percent!==undefined){
      const c=sData.cpu_percent;
      const cc=c>80?'var(--red)':c>50?'var(--yellow)':'var(--gold)';
      $m('cpu-v').textContent=c.toFixed(1)+'%';
      $m('cpu-v').style.color=cc;
      $m('cpu-b').style.width=c+'%';
      $m('cpu-b').style.background=cc;
    }
    if(sData.memory_percent!==undefined){
      const m=sData.memory_percent;
      const mc=m>80?'var(--red)':m>50?'var(--yellow)':'var(--green)';
      $m('mem-v').textContent=m.toFixed(1)+'%';
      $m('mem-v').style.color=mc;
      $m('mem-b').style.width=m+'%';
      $m('mem-b').style.background=mc;
    }
    updChart();
  }catch(e){/* silent */}
}

async function loadLinks(){
  try{
    const r=await fetch('/api/links');
    if(r.status===401){showLogin();return;}
    if(!r.ok)throw new Error();
    const d=await r.json();
    allLinks=d.links||[];
    filterLinks();
  }catch(e){/* silent */}
}

async function chgPw(){
  const cur=$m('cpw').value;
  const nw=$m('npw').value;
  if(!cur||!nw){toast('Fill all fields',true);return;}
  if(nw.length<4){toast('Password must be at least 4 characters',true);return;}
  try{
    const r=await fetch('/api/change-password',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({current_password:cur,new_password:nw})
    });
    if(!r.ok){
      const d=await r.json().catch(()=>({}));
      throw new Error(d.detail||'Error changing password');
    }
    toast('Password updated successfully');
    $m('cpw').value='';$m('npw').value='';
  }catch(e){toast(e.message,true);}
}

// ── Chart ─────────────────────────────────────────────────────────────────────
function initChart(){
  const ctx=$m('tc');
  if(!ctx||tChart)return;
  tChart=new Chart(ctx,{
    type:'bar',
    data:{
      labels:[],
      datasets:[{label:'MB',data:[],backgroundColor:'rgba(255,215,0,0.55)',borderColor:'#FFD700',borderWidth:1,borderRadius:4}]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{grid:{display:false},ticks:{color:'rgba(255,215,0,0.3)',font:{size:10}}},
        y:{grid:{color:'rgba(255,255,255,0.04)'},ticks:{color:'rgba(255,215,0,0.3)',font:{size:10},callback:v=>v+' MB'},beginAtZero:true}
      }
    }
  });

  const ctx2=$m('inbound-chart');
  if(ctx2 && !iChart){
    iChart=new Chart(ctx2,{
      type:'doughnut',
      data:{
        labels:[],
        datasets:[{
          data:[],
          backgroundColor:['#FFD700','#a78bfa','#4ade80','#fbbf24','#f87171','#38bdf8','#ec4899','#f43f5e'],
          borderWidth:0
        }]
      },
      options:{
        responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:true,position:'right',labels:{color:'rgba(255,255,255,0.6)',font:{size:10}}}}
      }
    });
  }
  updChartColors();
}

function updChartColors(){
  if(!tChart)return;
  const col=theme==='light'?'rgba(0,0,0,0.5)':'rgba(255,215,0,0.4)';
  const gridCol=theme==='light'?'rgba(0,0,0,0.08)':'rgba(255,255,255,0.06)';
  tChart.options.scales.x.ticks.color=col;
  tChart.options.scales.y.ticks.color=col;
  tChart.options.scales.y.grid.color=gridCol;
  tChart.update();
}

function updChart(){
  if(!tChart||!sData.hourly_traffic)return;
  const entries=Object.entries(sData.hourly_traffic)
    .sort((a,b)=>a[0].localeCompare(b[0])).slice(-12);
  tChart.data.labels=entries.map(x=>x[0]);
  tChart.data.datasets[0].data=entries.map(x=>Math.round(x[1]/1048576));
  tChart.update();
}

// ── Addresses ─────────────────────────────────────────────────────────────────
async function loadAddrs(){
  try{
    const r=await fetch('/api/addresses');
    if(!r.ok)throw new Error();
    const d=await r.json();
    allAddrs=d.addresses||[];
    renderAddrs();
  }catch(e){/* silent */}
}

function renderAddrs(){
  const el=$m('addr-list');
  if(!el)return;
  if(!allAddrs||!allAddrs.length){
    el.innerHTML='<div style="color:var(--text3);font-size:12px">No addresses added</div>';
    return;
  }
  el.innerHTML=allAddrs.map((a,i)=>`<div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:var(--surface3);border:1px solid var(--border);border-radius:10px;margin-bottom:8px">
    <div style="display:flex;align-items:center;gap:10px">
      <span style="color:var(--gold);font-size:16px">🌐</span>
      <div><div style="font-size:14px;font-weight:600">${esc(a)}</div><div style="font-size:11px;color:var(--text3);margin-top:2px;">Address #${i+1}</div></div>
    </div>
    <button class="act-btn act-del" onclick="delAddr(${i})">${tr('del')}</button>
  </div>`).join('');
}

function showAddAddrMo(){$m('na').value='';$m('mo-addr').classList.add('show');}

async function addAddrs(){
  const lines=($m('na').value||'').trim().split('\n').map(l=>l.trim()).filter(l=>l);
  let ok=0,fail=0;
  for(const a of lines){
    if(!/^[a-zA-Z0-9\-_. ]+$/.test(a)){fail++;continue;}
    try{
      const r=await fetch('/api/addresses',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({address:a})
      });
      if(r.ok)ok++;else fail++;
    }catch(e){fail++;}
  }
  if(ok)toast('Added '+ok);
  if(fail)toast(fail+' failed',true);
  if(ok){$m('mo-addr').classList.remove('show');await loadAddrs();}
}

async function delAddr(i){
  if(!confirm('Delete this address?'))return;
  try{
    const r=await fetch('/api/addresses/'+i,{method:'DELETE'});
    if(!r.ok)throw new Error();
    toast('Deleted');
    await loadAddrs();
  }catch(e){toast('Error deleting',true);}
}

async function delAllAddrs(){
  if(!allAddrs||!allAddrs.length){toast('No addresses to delete',true);return;}
  if(!confirm('Delete ALL clean IP addresses? This cannot be undone.'))return;
  try{
    const r=await fetch('/api/addresses',{method:'DELETE'});
    if(!r.ok)throw new Error();
    toast('All addresses deleted');
    await loadAddrs();
  }catch(e){toast('Error deleting',true);}
}

// ── Init ──────────────────────────────────────────────────────────────────────
setTheme(theme);
setLang(lang);
checkAuth();
let statsInterval=null;
function startPolling(){
  if(statsInterval)clearInterval(statsInterval);
  statsInterval=setInterval(()=>{if(isAuthenticated){loadStats();loadLinks();}},12000);
}
startPolling();
</script>
</body>
</html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse(content=PANEL_HTML)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return HTMLResponse(content=PANEL_HTML)


@app.get("/panel", response_class=HTMLResponse)
async def panel_page(request: Request):
    return HTMLResponse(content=PANEL_HTML)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=CONFIG["port"])
