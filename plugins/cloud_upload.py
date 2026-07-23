# BIMBO v4.0 — Cloud Upload Plugin
# /gofile  reply → upload to gofile.io & give stream link
# /mega    reply → upload to Mega.nz (needs MEGA_EMAIL/PASSWORD)
# /gdrive  reply → upload to Google Drive (needs service account json)
import os
import time
import asyncio
import logging
import json

import aiohttp
from pyrogram import Client, filters

from config import Config
from translation import Translation
from utils import (
    is_media, user_download_dir, cleanup_dir, run_cmd, humanbytes, safe_filename,
)

logger = logging.getLogger(__name__)


# ----------------------- GOFILE (no creds required) -----------------------
async def _gofile_upload(file_path: str):
    """Upload file to gofile.io, returns direct URL or None."""
    # Step 1: get best server
    try:
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get("https://api.gofile.io/getServer", ssl=False) as r:
                j = await r.json()
            if j.get("status") != "ok":
                return None, "gofile getServer failed"
            srv = j["data"]["server"]
            url = f"https://{srv}.gofile.io/uploadFile"
            form = aiohttp.FormData()
            form.add_field("file", open(file_path, "rb"),
                           filename=os.path.basename(file_path))
            headers = {}
            if Config.GOFILE_TOKEN:
                headers["Authorization"] = f"Bearer {Config.GOFILE_TOKEN}"
            async with s.post(url, data=form, headers=headers, ssl=False) as r:
                j = await r.json()
            if j.get("status") != "ok":
                return None, str(j)
            d = j["data"]
            return d.get("downloadPage"), d.get("code")
    except Exception as e:
        logger.exception("gofile")
        return None, str(e)



# ============== Cross-version safe command filter ==============
def _cmd(*names):
    names = [n.lower().lstrip("/") for n in names]
    def f(_flt, _client, m):
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


@Client.on_message(filters.private & _cmd('gofile', 'stream'))
async def cmd_gofile(client, message):
    if not message.reply_to_message or not is_media(message.reply_to_message):
        return await message.reply_text("❌ Reply to a file/video to upload to Gofile.")
    uid = message.from_user.id
    work = user_download_dir(uid) + f"/gf_{int(time.time())}"
    os.makedirs(work, exist_ok=True)
    msg = await message.reply_text(Translation.PROCESSING)
    try:
        def _name(m):
            for t in ("video", "document", "audio", "animation"):
                x = getattr(m, t, None)
                if x and getattr(x, "file_name", None):
                    return x.file_name
            return f"file_{int(time.time())}.bin"
        fn = safe_filename(_name(message.reply_to_message))
        path = os.path.join(work, fn)
        await msg.edit_text("📥 Downloading from Telegram...")
        path = await message.reply_to_message.download(file_name=path)
        sz = os.path.getsize(path)
        await msg.edit_text(f"☁️ Uploading {humanbytes(sz)} to Gofile...")
        link, code = await _gofile_upload(path)
        if not link:
            return await msg.edit_text(f"❌ Upload failed: <code>{code}</code>")
        await msg.edit_text(
            f"✅ **Uploaded to Gofile!**\n\n"
            f"📁 **File:** `{fn}`\n"
            f"📦 **Size:** {humanbytes(sz)}\n"
            f"🔗 **Link:** {link}\n"
            f"🆔 Code: `{code}`\n\n"
            f"💡 Direct browser stream/download ho jayega."
        )
    except Exception as e:
        logger.exception("gofile cmd")
        await msg.edit_text(f"❌ Error: <code>{e}</code>")
    finally:
        asyncio.create_task(cleanup_dir(work))


# ----------------------- MEGA.NZ -----------------------
@Client.on_message(filters.private & _cmd('mega'))
async def cmd_mega(client, message):
    if not Config.MEGA_ENABLED:
        return await message.reply_text(Translation.MEGA_404)
    if not message.reply_to_message or not is_media(message.reply_to_message):
        return await message.reply_text("❌ Reply to a file/video to upload to Mega.")
    uid = message.from_user.id
    work = user_download_dir(uid) + f"/mega_{int(time.time())}"
    os.makedirs(work, exist_ok=True)
    msg = await message.reply_text(Translation.PROCESSING)
    try:
        fn = "file.bin"
        for t in ("video", "document", "audio", "animation"):
            x = getattr(message.reply_to_message, t, None)
            if x and getattr(x, "file_name", None):
                fn = x.file_name; break
        fn = safe_filename(fn)
        path = os.path.join(work, fn)
        await msg.edit_text("📥 Downloading...")
        path = await message.reply_to_message.download(file_name=path)
        await msg.edit_text("☁️ Uploading to Mega...")
        try:
            from mega import Mega
            m = Mega()
            m.login(Config.MEGA_EMAIL, Config.MEGA_PASSWORD)
            link = m.upload(path)
            pub = m.get_upload_link(link)
            await msg.edit_text(f"✅ **Uploaded to Mega.nz**\n\n🔗 {pub}")
        except ImportError:
            await msg.edit_text("❌ mega.py not installed. Add `mega.py==1.0.8` in requirements and redeploy.")
        except Exception as e:
            await msg.edit_text(f"❌ Mega upload failed: <code>{e}</code>")
    except Exception as e:
        logger.exception("mega cmd")
        await msg.edit_text(f"❌ Error: <code>{e}</code>")
    finally:
        asyncio.create_task(cleanup_dir(work))


# ----------------------- GOOGLE DRIVE (service account) -----------------------
@Client.on_message(filters.private & _cmd('gdrive', 'gDrive', 'gd'))
async def cmd_gdrive(client, message):
    if not Config.GDRIVE_ENABLED:
        return await message.reply_text(Translation.GDRIVE_404)
    if not message.reply_to_message or not is_media(message.reply_to_message):
        return await message.reply_text("❌ Reply to a file/video to upload to GDrive.")
    uid = message.from_user.id
    work = user_download_dir(uid) + f"/gd_{int(time.time())}"
    os.makedirs(work, exist_ok=True)
    msg = await message.reply_text(Translation.PROCESSING)
    try:
        fn = "file.bin"
        for t in ("video", "document", "audio", "animation"):
            x = getattr(message.reply_to_message, t, None)
            if x and getattr(x, "file_name", None):
                fn = x.file_name; break
        fn = safe_filename(fn)
        path = os.path.join(work, fn)
        await msg.edit_text("📥 Downloading...")
        path = await message.reply_to_message.download(file_name=path)
        await msg.edit_text("☁️ Uploading to Google Drive...")
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            creds = service_account.Credentials.from_service_account_file(
                Config.BIMBO_GDRIVE_CREDENTIALS,
                scopes=["https://www.googleapis.com/auth/drive"]
            )
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            metadata = {"name": os.path.basename(path)}
            if Config.BIMBO_GDRIVE_FOLDER_ID:
                metadata["parents"] = [Config.BIMBO_GDRIVE_FOLDER_ID]
            media = MediaFileUpload(path, resumable=True, chunksize=10*1024*1024)
            req = service.files().create(body=metadata, media_body=media, fields="id,webViewLink")
            res = None
            while res is None:
                status, res = req.next_chunk()
            file_id = res.get("id")
            link = res.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
            # Make public
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"},
                    fields="id"
                ).execute()
            except Exception:
                pass
            await msg.edit_text(f"✅ **Uploaded to Google Drive!**\n\n🔗 {link}")
        except ImportError as e:
            await msg.edit_text(f"❌ Google libs missing: {e}")
        except Exception as e:
            await msg.edit_text(f"❌ GDrive upload failed: <code>{e}</code>")
    except Exception as e:
        logger.exception("gdrive cmd")
        await msg.edit_text(f"❌ Error: <code>{e}</code>")
    finally:
        asyncio.create_task(cleanup_dir(work))
