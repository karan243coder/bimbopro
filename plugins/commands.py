# BIMBO v4.0 - Core Commands (status, help, cancel, maintenance ban checks)
import os
import time
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from pyrogram.errors import FloodWait

from config import Config
from translation import Translation
from database.adduser import AddUser
from database.access import bimbo
from database.users_chats_db import db
from utils import is_admin, humanbytes
from plugins.forcesub import handle_force_sub
from plugins.premium_plans import track_referral_if_any

import psutil

logger = logging.getLogger(__name__)


# ============== Cross-version safe command filter (avoids tuple/list Pyrogram bug) ==============
def _cmd(*names):
    names = [n.lower().lstrip("/") for n in names]
    def f(_flt, _client, m: Message):
        if not m or not getattr(m, "text", None):
            return False
        if m.media:
            return False
        t = (m.text or "").strip()
        if not t.startswith("/"):
            return False
        first = t.split()[0][1:].split("@")[0].lower()
        return first in names
    return filters.create(f)


_CMD_START_HELP_CANCEL = _cmd("start", "help", "cancel")


# =================== GLOBAL MESSAGE FILTERS (ban + maintenance) ===================
@Client.on_message(filters.private & ~_CMD_START_HELP_CANCEL, group=-1)
async def gatekeeper(client: Client, m: Message):
    """Pre-process every private message: ban/maintenance/fsub/running user checks."""
    uid = m.from_user.id if m.from_user else None
    if not uid:
        return
    # Ban check
    if await db.is_banned(uid):
        try:
            await m.reply_text("🚫 You are banned from using this bot. Contact @Bimbo69")
        except Exception:
            pass
        m.stop_propagation()
        return
    # Maintenance
    if Config.MAINTENANCE_MODE and not is_admin(uid):
        try:
            await m.reply_text(Translation.MAINTENANCE_MSG)
        except Exception:
            pass
        m.stop_propagation()
        return


# =================== HELP (unified) ===================
@Client.on_message(filters.private & _cmd("help"))
async def help_cmd(client: Client, m: Message):
    await AddUser(client, m)
    await m.reply_text(
        Translation.BIMBO_HELP_TEXT,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Home", callback_data="home"),
             InlineKeyboardButton("✖️ Close", callback_data="close")]
        ])
    )


# =================== START (unified, supports referrals and verify) ===================
@Client.on_message(filters.private & _cmd("start"))
async def start_cmd(client: Client, m: Message):
    await AddUser(client, m)

    # Force sub
    if Config.BIMBO_UPDATES_CHANNEL is not None:
        back = await handle_force_sub(client, m)
        if back == 400:
            return

    uid = m.from_user.id
    # Parse payload manually (m.command is None with custom filter)
    parts = (m.text or "").strip().split()
    payload = parts[1] if len(parts) > 1 else ""

    # Referral tracking
    if payload.startswith("ref"):
        await track_referral_if_any(client, payload, uid)

    # Verify flow (from URL shortener)
    if payload.startswith("verify-"):
        parts = payload.split("-")
        if len(parts) >= 3:
            userid = parts[1]
            token = parts[2] if len(parts) == 3 else "-".join(parts[2:])
            if str(uid) != str(userid):
                return await m.reply_text("<b>⚠️ Invalid / expired link.</b>")
            from utils import check_token, verify_user, check_verification
            valid = await check_token(client, userid, token)
            if valid:
                await verify_user(client, userid, token)
                await m.reply_text(
                    f"<b>✅ Hello {m.from_user.mention}!</b>\n\n"
                    f"Verification successful! You can now download files "
                    f"for today without limits."
                )
                return
            else:
                return await m.reply_text("<b>⚠️ Link already used or expired.</b>")

    # Custom start pic?
    start_text = Config.BIMBO_START_MSG or Translation.BIMBO_START_TEXT.format(m.from_user.mention)

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Update Channel", url="https://t.me/Bimbobot69"),
            InlineKeyboardButton("👨‍💻 Owner", url="https://t.me/Bimbo69"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="help"),
            InlineKeyboardButton("💎 Plans", callback_data="plans"),
            InlineKeyboardButton("📊 Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🎬 Tools Menu", callback_data="tools_menu"),
            InlineKeyboardButton("☁️ Cloud", callback_data="cloud_menu"),
        ],
    ])

    if Config.BIMBO_START_PIC:
        pic = Config.BIMBO_START_PIC
        try:
            await client.send_photo(m.chat.id, pic, caption=start_text,
                                    parse_mode=enums.ParseMode.HTML,
                                    reply_markup=buttons,
                                    reply_to_message_id=m.id)
            return
        except Exception:
            pass
    await m.reply_text(start_text, parse_mode=enums.ParseMode.HTML,
                       reply_markup=buttons, disable_web_page_preview=True,
                       reply_to_message_id=m.id)


# =================== CANCEL ===================
@Client.on_message(filters.private & _cmd("cancel"))
async def cancel_cmd(client: Client, m: Message):
    await m.reply_text(Translation.BIMBO_CANCEL_STR)


# =================== CALLBACKS for start/help/about/tools/cloud menus ===================
@Client.on_callback_query(filters.regex(r"^(home|help|about|close|status|plans|tools_menu|cloud_menu)$"))
async def menu_cbs(client: Client, c: CallbackQuery):
    d = c.data
    if d == "close":
        try:
            await c.message.delete()
        except Exception:
            pass
        return
    if d == "home":
        txt = Translation.BIMBO_START_TEXT.format(c.from_user.mention)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Channel", url="https://t.me/Bimbobot69"),
             InlineKeyboardButton("👨‍💻 Owner", url="https://t.me/Bimbo69")],
            [InlineKeyboardButton("❓ Help", callback_data="help"),
             InlineKeyboardButton("💎 Plans", callback_data="plans"),
             InlineKeyboardButton("📊 Status", callback_data="status")],
            [InlineKeyboardButton("🎬 Tools", callback_data="tools_menu"),
             InlineKeyboardButton("☁️ Cloud", callback_data="cloud_menu")],
        ])
    elif d == "help":
        txt = Translation.BIMBO_HELP_TEXT
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Home", callback_data="home"),
             InlineKeyboardButton("✖️ Close", callback_data="close")]
        ])
    elif d == "about":
        txt = Translation.BIMBO_ABOUT_TEXT
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Home", callback_data="home"),
             InlineKeyboardButton("✖️ Close", callback_data="close")]
        ])
    elif d == "plans":
        from plugins.premium_plans import PLANS
        p = await db.get_premium(c.from_user.id)
        s = ("✅ Premium Active" if p else "🆓 Free User")
        txt = (f"💎 **Premium Plans**\n\nStatus: {s}\n\n"
               f"Weekly - {PLANS['1w']['price']}\n"
               f"Monthly - {PLANS['1m']['price']}\n"
               f"3 Months - {PLANS['3m']['price']}\n\n"
               f"Use /plan for details. Contact @Bimbo69 to buy.")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨‍💻 Buy", url="https://t.me/Bimbo69")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")],
        ])
    elif d == "status":
        disk = psutil.disk_usage(Config.BIMBO_DOWNLOAD_LOCATION)
        ram = psutil.virtual_memory()
        txt = (f"📊 **Bot Status**\n\n"
               f"CPU: `{psutil.cpu_percent()}%`\n"
               f"RAM: `{ram.percent}%` ({humanbytes(ram.used)}/{humanbytes(ram.total)})\n"
               f"Disk free: `{humanbytes(disk.free)}`\n"
               f"Workers: `{Config.BIMBO_WORKERS}` | Conc: `{Config.BIMBO_MAX_CONCURRENT_TASKS}`\n"
               f"Maintenance: `{'ON' if Config.MAINTENANCE_MODE else 'OFF'}`")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]])
    elif d == "tools_menu":
        txt = ("🎬 **Media Tools**\n\n"
               "Reply to a video/file with any of these:\n\n"
               "• /ss [n] — screenshots\n"
               "• /sample [secs] — sample video\n"
               "• /trim start end — cut video\n"
               "• /compress [low|med|high]\n"
               "• /wm text [pos] — watermark\n"
               "• /mp3 — extract audio\n"
               "• /unzip — extract zip\n"
               "• /rename new_name — rename\n\n"
               "Direct site commands:\n"
               "/ig /tt /fb /tw /m3u8 /pd")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]])
    elif d == "cloud_menu":
        txt = ("☁️ **Cloud Upload**\n\n"
               "Reply to a file with:\n"
               "• /gofile — free stream link\n"
               "• /mega — upload to Mega (requires creds)\n"
               "• /gdrive — upload to Google Drive (SA creds)\n")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]])
    try:
        await c.message.edit_text(txt, parse_mode=enums.ParseMode.HTML,
                                  reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass
    try:
        await c.answer()
    except Exception:
        pass
