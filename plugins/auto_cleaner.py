# -*- coding: utf-8 -*-
# BIMBO v8.4 — Auto Cleaner (COMMAND CLEAN MODE - SCREENSHOT SAFE)
# ================================================
# Sirf bekar commands clean -> /start /help etc 10 sec me delete
# Media / Screenshots / Upload / Download progress -> KABHI delete nahi
# Private + Groups dono
#
import asyncio
import logging
import time
import re
from typing import Dict, Any

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from config import Config

logger = logging.getLogger(__name__)

# ---------------- Delays ----------------
AUTO_SEC = int(getattr(Config, "AUTO_DELETE_SECONDS", 10) or 10)
if AUTO_SEC < 1: AUTO_SEC = 10

USER_CMD_DELAY = AUTO_SEC
USER_LINK_DELAY = 60          # link ko 60 sec baad delete
BOT_TEXT_DELAY = AUTO_SEC
BOT_MENU_DELAY = AUTO_SEC
BOT_SUCCESS_DELAY = AUTO_SEC
BOT_ERROR_DELAY = AUTO_SEC
REAP_INTERVAL = 1.5

URL_RE = re.compile(r"((?:https?://|www\.)[^\s)>\]]+|t\.me/[^\s)>\]]+|magnet:\?xt=urn:btih:[a-zA-Z0-9]+)", re.IGNORECASE)
COMMAND_RE = re.compile(r"^/([a-zA-Z0-9_]+)(?:@[a-zA-Z0-9_]+bot)?(?:\s|$)", re.IGNORECASE)

# Command reply captions – inko delete karna hai, protect nahi
COMMAND_PHOTO_KEYWORDS = (
    "welcome", "start", "help", "about", "admin panel", "admin control",
    "choose an option", "bot statistics", "bot settings", "premium management",
    "channel management", "ban management", "bimbo", "command", "usage"
)

_TRACK: Dict[tuple, Dict[str, Any]] = {}
_TRACK_LOCK = asyncio.Lock()
_reaper_started = False

def _has_url(text: str) -> bool:
    return bool(text and URL_RE.search(text))

def _is_command(text: str) -> bool:
    return bool(text and COMMAND_RE.match(text.strip()))

def _is_progress(text: str) -> bool:
    if not text: return False
    if "█" in text or "░" in text: return True
    low = text.lower()
    ACTIVE = ("processing", "downloading", "uploading", "eta", "speed", "initializing", "fetching", "extracting", "splitting", "merging", "preparing", "queuing", "sending", "%")
    return any(k in low for k in ACTIVE) and re.search(r"\d+%", text)

def _has_keyboard(reply_markup) -> bool:
    if reply_markup is None: return False
    try:
        kb = getattr(reply_markup, "inline_keyboard", None)
        return bool(kb and len(kb) > 0)
    except Exception:
        return False

def _is_success(text: str) -> bool:
    return bool(text and "✅" in text[:80])

def _is_error(text: str) -> bool:
    return bool(text and (text.startswith("❌") or text.startswith("⚠️")))

def _is_command_photo(m: Message, text: str) -> bool:
    """Kya ye photo /start /help jaise command ka reply hai?"""
    low = (text or "").lower()
    return any(kw in low for kw in COMMAND_PHOTO_KEYWORDS)

def _is_media_message(m: Message) -> bool:
    """FINAL UPLOADED MEDIA — keep forever.
    Video / Document / Audio -> always protect
    Photo -> protect, EXCEPT command reply photos (/start, /help etc)
    """
    if not m.media:
        return False
    
    # Video / Doc / Audio -> always keep
    if getattr(m, "video", None) or getattr(m, "document", None) or getattr(m, "audio", None) or getattr(m, "voice", None) or getattr(m, "animation", None):
        return True
    
    # Photo handling – screenshot safe
    photo = getattr(m, "photo", None)
    if photo:
        # Media group (screenshots album) -> always keep
        if getattr(m, "media_group_id", None):
            return True
        text = getattr(m, "caption", "") or getattr(m, "text", "") or ""
        # Command reply photo? -> DO NOT protect, let it delete in 10 sec
        if _is_command_photo(m, text):
            return False
        # Keyboard wali photo (menus) -> delete
        if _has_keyboard(getattr(m, "reply_markup", None)):
            return False
        # Baki sab photos (screenshots etc) -> PROTECT
        return True
    
    # Fallback: media + no keyboard -> protect
    return not _has_keyboard(getattr(m, "reply_markup", None))

async def _track_msg(m: Message, delay: float, perm: bool = False, reason: str = ""):
    if not m or not getattr(m, "chat", None): return
    cid, mid = m.chat.id, m.id
    if not cid or not mid: return
    async with _TRACK_LOCK:
        _TRACK[(cid, mid)] = {"ts": time.time() + delay if not perm else float("inf"), "perm": perm, "reason": reason}

async def _mark_perm(m: Message):
    await _track_msg(m, 0, perm=True)

async def _invalidate(m: Message):
    if not m or not getattr(m, "chat", None): return
    async with _TRACK_LOCK:
        _TRACK.pop((m.chat.id, m.id), None)

# ============== USER messages ==============
def _user_filter(_, __, m: Message):
    if not m or getattr(m, "service", None): return False
    if m.media: return False
    if not getattr(m, "chat", None): return False
    from_user = getattr(m, "from_user", None)
    if from_user and from_user.is_bot: return False
    return bool(getattr(m, "text", None))

_user_filter = filters.create(_user_filter)

@Client.on_message(_user_filter, group=-5)
@Client.on_edited_message(_user_filter, group=-5)
async def track_user_message(client: Client, m: Message):
    try:
        text = (m.text or "").strip()
        if not text: return
        if _has_url(text):
            await _track_msg(m, USER_LINK_DELAY, reason="user_link"); return
        if _is_command(text):
            await _track_msg(m, USER_CMD_DELAY, reason="cmd"); return
        if m.chat.type == enums.ChatType.PRIVATE:
            try: await m.delete()
            except Exception: pass
    except Exception:
        pass

# ============== BOT messages ==============
def _bot_msg_filter(_, __, m: Message):
    if not m or getattr(m, "service", None): return False
    if getattr(m, "outgoing", False): return True
    from_user = getattr(m, "from_user", None)
    if from_user and getattr(from_user, "is_bot", False): return True
    if from_user is None: return True
    return False

_bot_msg_filter = filters.create(_bot_msg_filter)

@Client.on_message(_bot_msg_filter, group=-4)
@Client.on_edited_message(_bot_msg_filter, group=-4)
async def track_bot_message(client: Client, m: Message):
    try:
        text = getattr(m, "text", None) or getattr(m, "caption", "") or ""
        reply_markup = getattr(m, "reply_markup", None)

        # 1. Final media -> PERMANENT (screenshots included)
        if _is_media_message(m):
            await _mark_perm(m); return

        # 2. Active progress -> keep
        if _is_progress(text):
            await _invalidate(m); return

        # 3. Success / Error / Menu / Text -> 10 sec
        if _is_success(text):
            await _track_msg(m, BOT_SUCCESS_DELAY, reason="bot_success"); return
        if _is_error(text):
            await _track_msg(m, BOT_ERROR_DELAY, reason="bot_error"); return
        if _has_keyboard(reply_markup):
            await _track_msg(m, BOT_MENU_DELAY, reason="bot_menu"); return

        # 4. Plain bot text / command photos -> 10 sec
        await _track_msg(m, BOT_TEXT_DELAY, reason="bot_text")
    except Exception:
        pass

# ============== REAPER ==============
async def _cleanup_reaper(client: Client):
    await asyncio.sleep(2)
    logger.info(f"✅ cleaner reaper running - delete in {AUTO_SEC}s")
    while True:
        try:
            await asyncio.sleep(REAP_INTERVAL)
            now = time.time()
            to_delete = []
            async with _TRACK_LOCK:
                for key, info in list(_TRACK.items()):
                    if info.get("perm"): continue
                    if now >= info.get("ts", 0):
                        to_delete.append(key)
                        _TRACK.pop(key, None)
            if not to_delete: continue
            by_chat: Dict[int, list] = {}
            for cid, mid in to_delete:
                by_chat.setdefault(cid, []).append(mid)
            for cid, mids in by_chat.items():
                try:
                    await client.delete_messages(cid, mids)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)
                except Exception:
                    for mid in mids:
                        try: await client.delete_messages(cid, mid)
                        except Exception: pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(2)

async def start_cleaner(client: Client):
    global _reaper_started
    if _reaper_started: return
    _reaper_started = True
    asyncio.create_task(_cleanup_reaper(client))
    logger.info("🧹 Auto-cleaner started")

@Client.on_message(filters.all, group=-999)
async def _first_update_starter(client: Client, m: Message):
    await start_cleaner(client)

logger.info(f"✅ auto_cleaner v8.4 loaded | DELETE={AUTO_SEC}s | media=KEEP | screenshots=KEEP | progress=KEEP")
