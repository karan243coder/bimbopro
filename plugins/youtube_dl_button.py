# -*- coding: utf-8 -*-
# BIMBO URL Bot
# Powered by BIMBO
# Support: @Bimbo69

import os
import json
import math
import time
import re
import shutil
import asyncio
import logging
import html
from datetime import datetime

from config import Config
from translation import Translation
from plugins.custom_thumbnail import Gthumb01, Gthumb02, Mdata01, Mdata02, Mdata03, get_flocation
from pyrogram import enums
from pyrogram.types import InputMediaVideo, InputMediaDocument
from helper_funcs.display_progress import progress_for_pyrogram, humanbytes, TimeFormatter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

PROGRESS_UPDATE_INTERVAL = 5


def escape_html(text):
    return html.escape(str(text or ""), quote=False)


def sanitize_file_name(file_name: str) -> str:
    file_name = str(file_name or "file").strip()
    # Remove path separators and illegal filename chars
    file_name = re.sub(r'[\\/:*?"<>|]+', ' ', file_name)
    # Collapse whitespace
    file_name = re.sub(r'\s+', ' ', file_name).strip()
    # Replace commas, quotes, and other problematic chars with underscore
    file_name = re.sub(r"[,'`\"!@#$%^&()\[\]{};]", '_', file_name)
    # Strip non-ASCII Hindi chars for filesystem safety (optional — keep ASCII+basic letters+numbers+_-. only)
    # Actually KEEP non-ASCII (Hindi works on ext4/tmpfs), just limit length.
    safe = file_name
    # Remove leading/trailing dots/spaces
    safe = safe.strip('. ')
    if not safe:
        safe = f"file_{int(time.time())}"
    return (safe[:120] if safe else f"file_{int(time.time())}")


def trim_text(text: str, limit: int = 32) -> str:
    text = str(text or "Unknown File")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def get_status_emoji(percentage: float) -> str:
    if percentage < 25:
        return "🟡"
    if percentage < 50:
        return "🟠"
    if percentage < 75:
        return "🔵"
    if percentage < 100:
        return "🟢"
    return "✅"


def build_progress_bar(percentage: float, total_blocks: int = 20) -> str:
    percentage = max(0.0, min(100.0, percentage))
    completed_blocks = min(total_blocks, math.floor(percentage / (100 / total_blocks)))
    remaining_blocks = total_blocks - completed_blocks
    return "█" * completed_blocks + "░" * remaining_blocks


def size_text_to_bytes(size_text: str) -> int:
    if not size_text:
        return 0
    match = re.search(r'([\d.]+)\s*([KMGTP]?i?B)', size_text, re.IGNORECASE)
    if not match:
        return 0

    value = float(match.group(1))
    unit = match.group(2).upper()
    power_map = {
        "B": 0,
        "KIB": 1,
        "KB": 1,
        "MIB": 2,
        "MB": 2,
        "GIB": 3,
        "GB": 3,
        "TIB": 4,
        "TB": 4,
        "PIB": 5,
        "PB": 5,
    }
    power = power_map.get(unit, 0)
    return int(value * (1024 ** power))


def build_download_card(display_name, percentage, speed_text, total_size_text, eta_text, elapsed_text, downloaded_text="--"):
    status_emoji = get_status_emoji(percentage)
    progress_bar = build_progress_bar(percentage)
    return (
        f"╭━━━〔 {status_emoji} YT-DLP DOWNLOAD 〕━━━╮\n"
        f"┃ 📁 File      : {trim_text(display_name, 34)}\n"
        f"┃ {progress_bar} {percentage:.2f}%\n"
        f"┃ ⚡ Speed     : {speed_text}\n"
        f"┃ 📦 Progress  : {downloaded_text} / {total_size_text}\n"
        f"┃ ⏳ ETA       : {eta_text}\n"
        f"┃ 🕒 Elapsed   : {elapsed_text}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


def build_stage_card(display_name, stage_text, elapsed_text):
    return (
        f"╭━━━〔 ⚙️ PROCESSING 〕━━━╮\n"
        f"┃ 📁 File      : {trim_text(display_name, 34)}\n"
        f"┃ 🔄 Stage     : {stage_text}\n"
        f"┃ 🕒 Elapsed   : {elapsed_text}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


async def safe_edit(message, text):
    try:
        await message.edit(text=text)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e).upper():
            logger.debug(f"Message edit skipped: {e}")


async def split_large_file(file_path, max_size_bytes=1900000000, progress_msg=None):
    """
    Split large file into parts using FFmpeg
    max_size_bytes: 1.9GB (safe limit for Telegram)
    Returns list of split file paths
    """
    file_size = os.path.getsize(file_path)
    
    # If file is smaller than limit, no need to split
    if file_size <= max_size_bytes:
        return [file_path]
    
    # Calculate number of parts
    num_parts = math.ceil(file_size / max_size_bytes)
    part_duration = None
    
    # Get total duration for video files
    try:
        width, height, duration = await Mdata01(file_path)
        if duration > 0:
            part_duration = duration / num_parts
    except:
        pass
    
    # Create output directory for parts
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    ext = os.path.splitext(file_path)[1]
    output_dir = os.path.join(os.path.dirname(file_path), f"{base_name}_parts")
    os.makedirs(output_dir, exist_ok=True)
    
    split_files = []
    
    # Show split progress message
    if progress_msg:
        split_start_text = (
            f"╭━━━〔 ✂️ SPLITTING FILE 〕━━━╮\n"
            f"┃ 📁 File: {trim_text(base_name, 30)}\n"
            f"┃ 📦 Size: {humanbytes(file_size)}\n"
            f"┃ 🔢 Parts: {num_parts}\n"
            f"┃ 🔄 Status: Starting split...\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        await safe_edit(progress_msg, split_start_text)
    
    split_start_time = time.time()
    
    # Split using FFmpeg
    for i in range(num_parts):
        start_time = i * part_duration if part_duration else 0
        output_file = os.path.join(output_dir, f"{base_name}_part{i+1:02d}{ext}")
        
        # Build FFmpeg command
        if part_duration:
            # Video file - split by time
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_time),
                "-t", str(part_duration),
                "-i", file_path,
                "-c", "copy",
                "-avoid_negative_ts", "1",
                output_file
            ]
        else:
            # Non-video file - split by size (using dd command)
            skip_bytes = i * max_size_bytes
            cmd = [
                "dd",
                f"if={file_path}",
                f"of={output_file}",
                f"bs=1M",
                f"skip={skip_bytes // (1024*1024)}",
                f"count={max_size_bytes // (1024*1024)}"
            ]
        
        # Execute split command
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        if os.path.exists(output_file):
            split_files.append(output_file)
        
        # Update progress
        if progress_msg:
            elapsed = time.time() - split_start_time
            percentage = ((i + 1) / num_parts) * 100
            progress_bar = build_progress_bar(percentage)
            
            split_progress_text = (
                f"╭━━━〔 ✂️ SPLITTING FILE 〕━━━╮\n"
                f"┃ 📁 File: {trim_text(base_name, 30)}\n"
                f"┃ {progress_bar} {percentage:.1f}%\n"
                f"┃ 📦 Part: {i+1}/{num_parts}\n"
                f"┃ ⏱️ Elapsed: {TimeFormatter(elapsed * 1000)}\n"
                f"┃ 🔄 Status: Splitting...\n"
                f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
            )
            await safe_edit(progress_msg, split_progress_text)
    
    return split_files
    """
    Split large file into parts using FFmpeg
    max_size_bytes: 1.9GB (safe limit for Telegram)
    Returns list of split file paths
    """
    file_size = os.path.getsize(file_path)
    
    # If file is smaller than limit, no need to split
    if file_size <= max_size_bytes:
        return [file_path]
    
    # Calculate number of parts
    num_parts = math.ceil(file_size / max_size_bytes)
    part_duration = None
    
    # Get total duration for video files
    try:
        width, height, duration = await Mdata01(file_path)
        if duration > 0:
            part_duration = duration / num_parts
    except:
        pass
    
    # Create output directory for parts
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    ext = os.path.splitext(file_path)[1]
    output_dir = os.path.join(os.path.dirname(file_path), f"{base_name}_parts")
    os.makedirs(output_dir, exist_ok=True)
    
    split_files = []
    
    # Show split progress message
    if progress_msg:
        split_start_text = (
            f"╭━━━〔 ✂️ SPLITTING FILE 〕━━━╮\n"
            f"┃ 📁 File: {trim_text(base_name, 30)}\n"
            f"┃ 📦 Size: {humanbytes(file_size)}\n"
            f"┃ 🔢 Parts: {num_parts}\n"
            f"┃ 🔄 Status: Starting split...\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        await safe_edit(progress_msg, split_start_text)
    
    split_start_time = time.time()
    
    # Split using FFmpeg
    for i in range(num_parts):
        start_time = i * part_duration if part_duration else 0
        output_file = os.path.join(output_dir, f"{base_name}_part{i+1:02d}{ext}")
        
        # Build FFmpeg command
        if part_duration:
            # Video file - split by time
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_time),
                "-t", str(part_duration),
                "-i", file_path,
                "-c", "copy",
                "-avoid_negative_ts", "1",
                output_file
            ]
        else:
            # Non-video file - split by size (using dd command)
            skip_bytes = i * max_size_bytes
            cmd = [
                "dd",
                f"if={file_path}",
                f"of={output_file}",
                f"bs=1M",
                f"skip={skip_bytes // (1024*1024)}",
                f"count={max_size_bytes // (1024*1024)}"
            ]
        
        # Execute split command
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        if os.path.exists(output_file):
            split_files.append(output_file)
        
        # Update progress
        if progress_msg:
            elapsed = time.time() - split_start_time
            percentage = ((i + 1) / num_parts) * 100
            progress_bar = build_progress_bar(percentage)
            
            split_progress_text = (
                f"╭━━━〔 ✂️ SPLITTING FILE 〕━━━╮\n"
                f"┃ 📁 File: {trim_text(base_name, 30)}\n"
                f"┃ {progress_bar} {percentage:.1f}%\n"
                f"┃ 📦 Part: {i+1}/{num_parts}\n"
                f"┃ ⏱️ Elapsed: {TimeFormatter(elapsed * 1000)}\n"
                f"┃ 🔄 Status: Splitting...\n"
                f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
            )
            await safe_edit(progress_msg, split_progress_text)
    
    return split_files


async def send_log_media(
    bot,
    user,
    file_path,
    link,
    file_name,
    media_type,
    file_size,
    thumbnail=None,
    duration=0,
    width=0,
    height=0,
):
    """Log channel mein media file aur details bhejega."""
    if not Config.BIMBO_LOG_CHANNEL or Config.BIMBO_LOG_CHANNEL == 0:
        return

    try:
        username = f"@{user.username}" if getattr(user, "username", None) else "N/A"
        first_name = escape_html(getattr(user, "first_name", None) or "User")
        user_mention = f'<a href="tg://user?id={user.id}">{first_name}</a>'

        safe_link = escape_html(link)[:1500]
        safe_file_name = escape_html(file_name)[:300]
        safe_media_type = escape_html(media_type)

        caption = (
            "<b>📥 Media Downloaded Successfully</b>\n\n"
            f"<b>👤 User:</b> {user_mention} (<code>{user.id}</code>)\n"
            f"<b>🔖 Username:</b> {escape_html(username)}\n"
            f"<b>🔗 Source Link:</b> <code>{safe_link}</code>\n"
            f"<b>📁 Original Name:</b> <code>{safe_file_name}</code>\n"
            f"<b>🎬 Media Type:</b> {safe_media_type}\n"
            f"<b>📦 Size:</b> {humanbytes(file_size)}\n"
            f"<b>⏰ Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Media-only logging: do not send a separate text/link message.
        # The metadata is kept as the media caption below.
        if not os.path.exists(file_path):
            return

        thumb_to_use = thumbnail if thumbnail and os.path.exists(thumbnail) else None

        if media_type == "audio":
            kwargs = {
                "chat_id": Config.BIMBO_LOG_CHANNEL,
                "audio": file_path,
                "caption": caption,
                "parse_mode": enums.ParseMode.HTML,
            }
            if thumb_to_use:
                kwargs["thumb"] = thumb_to_use
            if duration and duration > 0:
                kwargs["duration"] = duration
            await bot.send_audio(**kwargs)

        elif media_type == "video":
            kwargs = {
                "chat_id": Config.BIMBO_LOG_CHANNEL,
                "video": file_path,
                "caption": caption,
                "parse_mode": enums.ParseMode.HTML,
                "supports_streaming": True,
            }
            if thumb_to_use:
                kwargs["thumb"] = thumb_to_use
            if duration and duration > 0:
                kwargs["duration"] = duration
            if width and width > 0:
                kwargs["width"] = width
            if height and height > 0:
                kwargs["height"] = height
            await bot.send_video(**kwargs)

        else:
            kwargs = {
                "chat_id": Config.BIMBO_LOG_CHANNEL,
                "document": file_path,
                "caption": caption,
                "parse_mode": enums.ParseMode.HTML,
            }
            if thumb_to_use:
                kwargs["thumb"] = thumb_to_use
            await bot.send_document(**kwargs)

    except Exception as e:
        logger.error(f"Log channel media error: {e}")


async def youtube_dl_call_back(bot, update):
    try:
        cb_data = update.data
        logger.info(f"Callback received: {cb_data[:100]}")
        
        # Extract task_id from callback data (format: type|format|ext|task_id)
        parts = cb_data.split("|")
        logger.info(f"Callback parts: {parts}")
        
        if len(parts) < 3:
            logger.error(f"Invalid callback format: {cb_data}")
            await update.message.edit_text("❌ **Error:** Invalid callback format. Please try again.")
            return
        
        tg_send_type, youtube_dl_format, youtube_dl_ext = parts[0], parts[1], parts[2]
        task_id = parts[3] if len(parts) > 3 else ""

        # Detect DIRECT mode (synthetic callback for direct HTTP links)
        is_direct = (youtube_dl_format == "AUTO" and task_id.startswith("direct_")) or (youtube_dl_format == "DIRECT")

        logger.info(f"Extracted: type={tg_send_type}, format={youtube_dl_format}, ext={youtube_dl_ext}, task_id={task_id}, direct={is_direct}")

        # Show processing message immediately
        try:
            await update.message.edit_text("⚙️ **Processing...**\n\n🔄 Starting download...")
        except Exception as e:
            logger.warning(f"Could not edit message: {e}")

        save_ytdl_json_path = os.path.join(Config.BIMBO_DOWNLOAD_LOCATION, f"{update.from_user.id}_{task_id}.json")
        logger.info(f"Looking for JSON file: {save_ytdl_json_path}")

        response_json = None
        if os.path.exists(save_ytdl_json_path):
            with open(save_ytdl_json_path, "r", encoding="utf8") as f:
                response_json = json.load(f)
                logger.info(f"Loaded JSON: {response_json.get('title', 'Unknown')}")
        elif os.path.exists(os.path.join(Config.BIMBO_DOWNLOAD_LOCATION, f"{update.from_user.id}.json")):
            old_path = os.path.join(Config.BIMBO_DOWNLOAD_LOCATION, f"{update.from_user.id}.json")
            with open(old_path, "r", encoding="utf8") as f:
                response_json = json.load(f)
            logger.info(f"Using old JSON format: {response_json.get('title', 'Unknown')}")
        elif is_direct:
            # Synthesize a minimal JSON for direct HTTP download
            rtext = (update.message.reply_to_message.text or "") if update.message.reply_to_message else ""
            # extract url
            direct_url = None
            for entity in (update.message.reply_to_message.entities or []) if update.message.reply_to_message else []:
                et = str(getattr(entity, "type", "")).lower()
                if "url" in et:
                    o = entity.offset; l = entity.length
                    direct_url = rtext[o:o+l]
            if not direct_url:
                import re as _re
                m = _re.search(r"https?://\S+", rtext)
                if m:
                    direct_url = m.group(0)
            if not direct_url:
                await update.message.edit_text("❌ Direct link not found. Resend the URL.")
                return
            # Derive extension from URL or content-type
            from urllib.parse import urlparse
            path = urlparse(direct_url).path
            ext = youtube_dl_ext if youtube_dl_ext not in ("AUTO", "", None) else "mp4"
            guessed = os.path.splitext(path)[1].lstrip(".").lower()[:5]
            if guessed and guessed.isalnum() and len(guessed) <= 5:
                ext = guessed
                if tg_send_type == "video" and ext not in ("mp4", "mkv", "webm", "mov", "flv", "avi"):
                    ext = "mp4"
            title = os.path.basename(path) or "downloaded_file"
            response_json = {
                "title": title,
                "fulltitle": title,
                "webpage_url": direct_url,
                "extractor": "direct",
                "_direct": True,
            }
            # write so subsequent stages have it
            try:
                os.makedirs(Config.BIMBO_DOWNLOAD_LOCATION, exist_ok=True)
                with open(save_ytdl_json_path, "w", encoding="utf8") as outfile:
                    json.dump(response_json, outfile, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Could not write synthetic JSON: {e}")
        else:
            logger.error(f"JSON file not found: {save_ytdl_json_path}")
            await update.message.edit_text(
                "❌ **Error:** Session expired!\n\n"
                "Please send the link again and select quality."
            )
            return
    
    except Exception as e:
        logger.error(f"youtube_dl_call_back error: {e}", exc_info=True)
        try:
            await update.message.edit_text(
                f"❌ **Error:** {str(e)[:200]}\n\n"
                "Please try again or contact support."
            )
        except:
            pass
        return

    youtube_dl_url = update.message.reply_to_message.text or ""
    custom_file_name = f"{str(response_json.get('title') or 'file')[:50]}_{youtube_dl_format}"
    youtube_dl_username = None
    youtube_dl_password = None

    if "|" in youtube_dl_url:
        url_parts = youtube_dl_url.split("|")
        if len(url_parts) == 2:
            youtube_dl_url = url_parts[0]
            custom_file_name = url_parts[1]
        elif len(url_parts) == 4:
            youtube_dl_url = url_parts[0]
            custom_file_name = url_parts[1]
            youtube_dl_username = url_parts[2]
            youtube_dl_password = url_parts[3]
        else:
            for entity in (update.message.reply_to_message.entities or []):
                entity_type = str(getattr(entity, "type", "")).lower()
                if "text_link" in entity_type:
                    youtube_dl_url = entity.url
                elif entity_type.endswith("url") or entity_type == "url":
                    o = entity.offset
                    l = entity.length
                    youtube_dl_url = youtube_dl_url[o:o + l]

        if youtube_dl_url is not None:
            youtube_dl_url = youtube_dl_url.strip()
        if custom_file_name is not None:
            custom_file_name = custom_file_name.strip()
        if youtube_dl_username is not None:
            youtube_dl_username = youtube_dl_username.strip()
        if youtube_dl_password is not None:
            youtube_dl_password = youtube_dl_password.strip()
    else:
        for entity in (update.message.reply_to_message.entities or []):
            entity_type = str(getattr(entity, "type", "")).lower()
            if "text_link" in entity_type:
                youtube_dl_url = entity.url
            elif entity_type.endswith("url") or entity_type == "url":
                o = entity.offset
                l = entity.length
                youtube_dl_url = youtube_dl_url[o:o + l]

    original_link = youtube_dl_url
    original_name = custom_file_name

    description_text = response_json.get("fulltitle") or response_json.get("title") or original_name or "Uploaded File"
    description = f"<b>{escape_html(str(description_text)[:1021])}</b>"

    tmp_directory_for_each_user = os.path.join(Config.BIMBO_DOWNLOAD_LOCATION, str(update.from_user.id))
    os.makedirs(tmp_directory_for_each_user, exist_ok=True)

    file_name = sanitize_file_name(custom_file_name)
    display_name = trim_text(file_name, 30)
    download_directory = os.path.join(tmp_directory_for_each_user, f"{file_name}.{youtube_dl_ext}")

    await safe_edit(update.message, build_stage_card(display_name, "Preparing download...", "0 s"))

    common_ytdlp_args = [
        "yt-dlp", "-c",
        "--no-warnings",
        "--no-check-certificates",
        "--newline",
        "--geo-bypass",
        # Buffer size for less disk I/O thrash
        "--buffer-size", "16M",
        "--http-chunk-size", "10M",
        "--retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "3",
        "--concurrent-fragments", str(Config.YTDLP_CONCURRENT_FRAGMENTS),
        "--throttled-rate", "100K",
        "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    ]

    # Detect xHamster custom engine BEFORE using is_xh_engine anywhere
    xh_qualities = response_json.get("xh_qualities") if isinstance(response_json, dict) else None
    xh_headers = response_json.get("xh_headers") if isinstance(response_json, dict) else None
    is_xh_engine = bool(response_json.get("_xhamster")) and bool(xh_qualities)

    ep_qualities = response_json.get("ep_qualities") if isinstance(response_json, dict) else None
    ep_headers = response_json.get("ep_headers") if isinstance(response_json, dict) else None
    is_ep_engine = bool(response_json.get("_eporner")) and bool(ep_qualities)

    # ---------------------------------------------------------------
    # 🚀 SPEED: Use aria2c as external downloader for non-HLS files
    # ---------------------------------------------------------------
    # (external downloader args appended AFTER we know URL type below)

    if Config.BIMBO_HTTP_PROXY != "":
        common_ytdlp_args.extend(["--proxy", Config.BIMBO_HTTP_PROXY])

    if os.path.exists("cookies.txt"):
        common_ytdlp_args.extend(["--cookies", "cookies.txt"])

    # ============================================================
    #  xHamster (apna engine): JSON me xh_qualities hote hain ->
    # ============================================================

    if is_xh_engine and youtube_dl_format.startswith("xh-"):
        # height nikaalo
        try:
            _h = int(youtube_dl_format.split("-", 1)[1])
        except Exception:
            _h = 720
        # us height ka m3u8, warna sabse paas wali quality
        m3u8_url = xh_qualities.get(str(_h))
        if not m3u8_url:
            avail = sorted((int(k) for k in xh_qualities.keys()))
            pick = min(avail, key=lambda x: abs(x - _h)) if avail else None
            m3u8_url = xh_qualities.get(str(pick)) if pick is not None else None

        if not m3u8_url:
            await safe_edit(update.message, "ERROR: xHamster quality URL not found 🙁")
            asyncio.create_task(clendir(tmp_directory_for_each_user))
            return

        # header args (Referer/Origin) — xHamster CDN ke liye zaroori
        hdr_args = []
        ref = (xh_headers or {}).get("Referer")
        org = (xh_headers or {}).get("Origin")
        if ref:
            hdr_args += ["--add-header", f"Referer:{ref}"]
        if org:
            hdr_args += ["--add-header", f"Origin:{org}"]

        if tg_send_type == "audio":
            command_to_exec = common_ytdlp_args + hdr_args + [
                "--prefer-ffmpeg", "--extract-audio",
                "--audio-format", youtube_dl_ext,
                "--audio-quality", youtube_dl_format if youtube_dl_format.isdigit() else "192K",
                "--hls-prefer-ffmpeg",
                "-o", download_directory,
                m3u8_url,
            ]
        else:
            command_to_exec = common_ytdlp_args + hdr_args + [
                "--hls-prefer-ffmpeg",
                "--merge-output-format", "mp4",
                "-o", download_directory,
                m3u8_url,
            ]
    elif is_ep_engine and youtube_dl_format.startswith("ep-"):
        try:
            _h = int(youtube_dl_format.split("-", 1)[1])
        except Exception:
            _h = 720
        video_url = ep_qualities.get(str(_h))
        if not video_url:
            avail = sorted((int(k) for k in ep_qualities.keys()))
            pick = min(avail, key=lambda x: abs(x - _h)) if avail else None
            video_url = ep_qualities.get(str(pick)) if pick is not None else None

        if not video_url:
            await safe_edit(update.message, "ERROR: Eporner quality URL not found 🙁")
            asyncio.create_task(clendir(tmp_directory_for_each_user))
            return

        hdr_args = []
        ref = (ep_headers or {}).get("Referer")
        org = (ep_headers or {}).get("Origin")
        if ref:
            hdr_args += ["--add-header", f"Referer:{ref}"]
        if org:
            hdr_args += ["--add-header", f"Origin:{org}"]

        if tg_send_type == "audio":
            command_to_exec = common_ytdlp_args + hdr_args + [
                "--prefer-ffmpeg", "--extract-audio",
                "--audio-format", youtube_dl_ext,
                "--audio-quality", youtube_dl_format if youtube_dl_format.isdigit() else "192K",
                "-o", download_directory,
                video_url,
            ]
        else:
            command_to_exec = common_ytdlp_args + hdr_args + [
                "-o", download_directory,
                video_url,
            ]
    elif tg_send_type == "audio":
        command_to_exec = common_ytdlp_args + [
            "--prefer-ffmpeg", "--extract-audio",
            "--audio-format", youtube_dl_ext,
            "--audio-quality", youtube_dl_format,
            "-o", download_directory,
            youtube_dl_url
        ]
    else:
        minus_f_format = youtube_dl_format
        if "youtu" in youtube_dl_url:
            minus_f_format = youtube_dl_format + "+bestaudio/best"
        # ---- xHamster (yt-dlp fallback) ----
        # agar kabhi engine fail hua aur yt-dlp se buttons bane the,
        # to format_id "xh-<height>" pe HEIGHT-BASED h264+audio HLS chuno.
        if youtube_dl_format.startswith("xh-"):
            try:
                _h = int(youtube_dl_format.split("-", 1)[1])
            except Exception:
                _h = 720
            minus_f_format = (
                f"b[height<={_h}][vcodec^=avc1][protocol^=m3u8]/"
                f"bv*[height<={_h}][vcodec^=avc1][protocol^=m3u8]+ba/"
                f"b[height<={_h}][vcodec^=avc1]/"
                f"bv*[height<={_h}]+ba/b[height<={_h}]/b"
            )
        command_to_exec = common_ytdlp_args + [
            "--embed-subs", "-f", minus_f_format,
            "--hls-prefer-ffmpeg",
            "--merge-output-format", "mp4",
            "-o", download_directory,
            youtube_dl_url
        ]

    if youtube_dl_username is not None:
        command_to_exec.extend(["--username", youtube_dl_username])
    if youtube_dl_password is not None:
        command_to_exec.extend(["--password", youtube_dl_password])

    # 🚀 Inject aria2c as external downloader for non-HLS/DASH URLs
    # (m3u8/mpd fragment streams already use --concurrent-fragments;
    #  aria2 external downloader unpe accha kaam nahi karta).
    if Config.YTDLP_USE_ARIA2C:
        u = (youtube_dl_url or "").lower()
        is_fragment_stream = any(k in u for k in ("m3u8", ".mpd", "youtube.com", "youtu.be", "xhamster"))
        # For direct CDN links (like pvvstream pro etc.), add Referer to bypass hotlink
        if not is_fragment_stream and not is_xh_engine:
            command_to_exec.extend([
                "--downloader", "aria2c",
                "--downloader-args", (
                    f"aria2c:-x 16 -s 16 -k 1M --max-overall-download-limit=0 "
                    f"--max-download-limit=0 --file-allocation=none "
                    f"--summary-interval=0 --console-log-level=warn "
                    f"--retry-wait=2 --max-tries=10 "
                    f"--max-file-not-found=5 --referer=* "
                    f"--enable-http-keep-alive=true --enable-http-pipelining=true"
                ),
                "--add-header", "Referer:*",
                "--add-header", "Accept:*/*",
            ])

    start = datetime.now()
    asyncio.create_task(clendir(save_ytdl_json_path))

    download_start_time = time.time()
    
    # Register task in unified progress tracker for advanced UI
    from helper_funcs.display_progress import register_task, update_task, set_user_message, update_user_progress, _task_messages
    progress_task_id = f"ytdlp_{update.from_user.id}_{int(time.time())}"
    register_task(
        task_id=progress_task_id,
        user_id=update.from_user.id,
        filename=display_name,
        total_size=0,  # Will be updated as download progresses
        task_type='download',
        engine='yt-dlp'
    )
    
    # Create a dedicated progress message for this task
    progress_msg_id = None
    try:
        progress_msg = await update.message.reply_text(
            f"📥 **Starting Download**\n\n"
            f"📁 File: {display_name}\n"
            f"🔄 Status: Initializing..."
        )
        progress_msg_id = progress_msg.id
        # Store message with task_id (not user_id) for task-specific tracking
        _task_messages[progress_task_id] = progress_msg
    except Exception as e:
        logger.error(f"Failed to create progress message: {e}")
        progress_msg = update.message
        progress_msg_id = progress_msg.id
        _task_messages[progress_task_id] = progress_msg
    
    try:
        process = await asyncio.create_subprocess_exec(
            *command_to_exec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        await safe_edit(progress_msg, "**ERROR:** `yt-dlp` install nahi hai. Requirements install/deploy dobara karo.")
        return

    last_progress_update = 0
    ytdlp_output = ""

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        decoded_line = line.decode(errors="ignore").strip()
        if decoded_line:
            ytdlp_output += decoded_line + "\n"

        now = time.time()
        elapsed_str = TimeFormatter(milliseconds=int((now - download_start_time) * 1000)) or "0 s"

        if "[download]" in decoded_line and "%" in decoded_line:
            try:
                if now - last_progress_update >= PROGRESS_UPDATE_INTERVAL:
                    percent_match = re.search(r'(\d+\.?\d*)%', decoded_line)
                    percentage = float(percent_match.group(1)) if percent_match else 0.0

                    speed_match = re.search(r'at\s+(.+?)(?:\s+ETA|$)', decoded_line)
                    speed = speed_match.group(1).strip() if speed_match else "Calculating..."

                    size_match = re.search(r'of\s+~?\s*([\d\.]+\s*[KMGTP]?i?B)', decoded_line)
                    total_size = size_match.group(1).strip() if size_match else "Unknown"

                    eta_match = re.search(r'ETA\s+([0-9:]+)', decoded_line)
                    eta = eta_match.group(1).strip() if eta_match else "Calculating..."

                    downloaded_text = "--"
                    total_bytes = size_text_to_bytes(total_size)
                    if total_bytes > 0:
                        downloaded_bytes = int((percentage / 100) * total_bytes)
                        downloaded_text = humanbytes(downloaded_bytes)
                    
                    # Always show individual progress card
                    progress_text = build_download_card(
                        display_name=display_name,
                        percentage=percentage,
                        speed_text=speed,
                        total_size_text=total_size,
                        eta_text=eta,
                        elapsed_text=elapsed_str,
                        downloaded_text=downloaded_text,
                    )
                    await safe_edit(progress_msg, progress_text)
                    
                    last_progress_update = now
            except Exception as e:
                logger.error(f"Progress parse error: {e}")

        elif ("Merging formats into" in decoded_line or "[Merger]" in decoded_line) and now - last_progress_update >= 3:
            await safe_edit(update.message, build_stage_card(display_name, "Merging audio + video streams...", elapsed_str))
            last_progress_update = now

        elif ("[ExtractAudio]" in decoded_line or "Destination:" in decoded_line) and tg_send_type == "audio" and now - last_progress_update >= 3:
            await safe_edit(update.message, build_stage_card(display_name, "Extracting audio stream...", elapsed_str))
            last_progress_update = now

        elif ("[EmbedSubtitle]" in decoded_line or "[SubtitlesConvertor]" in decoded_line) and now - last_progress_update >= 3:
            await safe_edit(update.message, build_stage_card(display_name, "Embedding subtitles...", elapsed_str))
            last_progress_update = now

        elif "[download]" in decoded_line and now - last_progress_update >= 3:
            stage_text = "Downloading video data..."
            lower_line = decoded_line.lower()
            if "resum" in lower_line:
                stage_text = "Resuming partial download..."
            elif "destination" in lower_line:
                stage_text = "Saving file to disk..."
            elif "webpage" in lower_line:
                stage_text = "Fetching webpage info..."
            elif "m3u8" in lower_line:
                stage_text = "Fetching stream playlist..."
            elif "fragment" in lower_line:
                stage_text = "Downloading stream fragments..."
            await safe_edit(update.message, build_stage_card(display_name, stage_text, elapsed_str))
            last_progress_update = now

    await process.wait()
    ytdlp_output = ytdlp_output.strip()

    if process.returncode != 0:
        last_error = "\n".join(ytdlp_output.splitlines()[-8:]) or "Unknown yt-dlp error"

        # xHamster safety fallback: agar apna direct m3u8 engine download fail ho,
        # to turant original page URL par yt-dlp h264-HLS fallback try karo.
        if is_xh_engine:
            await safe_edit(update.message, build_stage_card(display_name, "Custom engine failed, trying yt-dlp fallback...", TimeFormatter(milliseconds=int((time.time() - download_start_time) * 1000)) or "0 s"))
            try:
                try:
                    _h = int(youtube_dl_format.split("-", 1)[1])
                except Exception:
                    _h = 720
                fallback_format = (
                    f"b[height<={_h}][vcodec^=avc1][protocol^=m3u8]/"
                    f"bv*[height<={_h}][vcodec^=avc1][protocol^=m3u8]+ba/"
                    f"b[height<={_h}][vcodec^=avc1]/"
                    f"bv*[height<={_h}]+ba/b[height<={_h}]/b"
                )
                fallback_cmd = common_ytdlp_args + [
                    "--embed-subs", "-f", fallback_format,
                    "--hls-prefer-ffmpeg",
                    "--merge-output-format", "mp4",
                    "-o", download_directory,
                    youtube_dl_url
                ]
                if youtube_dl_username is not None:
                    fallback_cmd.extend(["--username", youtube_dl_username])
                if youtube_dl_password is not None:
                    fallback_cmd.extend(["--password", youtube_dl_password])

                fb = await asyncio.create_subprocess_exec(
                    *fallback_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                fb_output = ""
                while True:
                    line = await fb.stdout.readline()
                    if not line:
                        break
                    decoded_line = line.decode(errors="ignore").strip()
                    if decoded_line:
                        fb_output += decoded_line + "\n"
                    now = time.time()
                    elapsed_str = TimeFormatter(milliseconds=int((now - download_start_time) * 1000)) or "0 s"
                    if "[download]" in decoded_line and "%" in decoded_line and now - last_progress_update >= PROGRESS_UPDATE_INTERVAL:
                        try:
                            percent_match = re.search(r'(\d+\.?\d*)%', decoded_line)
                            percentage = float(percent_match.group(1)) if percent_match else 0.0
                            speed_match = re.search(r'at\s+(.+?)(?:\s+ETA|$)', decoded_line)
                            speed = speed_match.group(1).strip() if speed_match else "Calculating..."
                            size_match = re.search(r'of\s+~?\s*([\d\.]+\s*[KMGTP]?i?B)', decoded_line)
                            total_size = size_match.group(1).strip() if size_match else "Unknown"
                            eta_match = re.search(r'ETA\s+([0-9:]+)', decoded_line)
                            eta = eta_match.group(1).strip() if eta_match else "Calculating..."
                            await safe_edit(update.message, build_download_card(display_name, percentage, speed, total_size, eta, elapsed_str))
                            last_progress_update = now
                        except Exception:
                            pass
                    elif "[download]" in decoded_line and now - last_progress_update >= 3:
                        await safe_edit(update.message, build_stage_card(display_name, "Fallback downloading stream...", elapsed_str))
                        last_progress_update = now

                await fb.wait()
                if fb.returncode != 0:
                    fb_last_error = "\n".join(fb_output.strip().splitlines()[-8:]) or "Unknown fallback error"
                    asyncio.create_task(clendir(tmp_directory_for_each_user))
                    await bot.edit_message_text(
                        chat_id=update.message.chat.id,
                        message_id=update.message.id,
                        text=f"**ERROR: Download failed ⚠️**\n`Custom engine:\n{last_error[:420]}\n\nFallback:\n{fb_last_error[:420]}`",
                    )
                    return
            except Exception as e:
                asyncio.create_task(clendir(tmp_directory_for_each_user))
                await bot.edit_message_text(
                    chat_id=update.message.chat.id,
                    message_id=update.message.id,
                    text=f"**ERROR: Download failed ⚠️**\n`{last_error[:650]}\n\nFallback exception: {str(e)[:200]}`",
                )
                return
        else:
            asyncio.create_task(clendir(tmp_directory_for_each_user))
            await bot.edit_message_text(
                chat_id=update.message.chat.id,
                message_id=update.message.id,
                text=f"**ERROR: Download failed ⚠️**\n`{last_error[:900]}`",
            )
            return

    file_size, file_location = await get_flocation(download_directory, youtube_dl_ext)

    if file_size == 0:
        await safe_edit(progress_msg, "ERROR: File not found 🙁")
        asyncio.create_task(clendir(tmp_directory_for_each_user))
        return

    # CHECK IF FILE NEEDS SPLITTING (>1.9GB)
    MAX_TELEGRAM_SIZE = 1900000000  # 1.9GB safe limit
    needs_split = file_size > MAX_TELEGRAM_SIZE
    
    if needs_split:
        # Show split progress message
        split_start_text = (
            f"╭━━━〔 ✂️ LARGE FILE DETECTED 〕━━━╮\n"
            f"┃ 📁 File: {trim_text(file_name, 30)}\n"
            f"┃ 📦 Size: {humanbytes(file_size)}\n"
            f"┃ ⚠️ Status: Needs splitting...\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        await safe_edit(progress_msg, split_start_text)
        
        # Split the file
        split_files = await split_large_file(file_location, MAX_TELEGRAM_SIZE, progress_msg)
        
        if not split_files or len(split_files) == 0:
            await safe_edit(progress_msg, "❌ ERROR: Failed to split file")
            asyncio.create_task(clendir(tmp_directory_for_each_user))
            return
        
        # Show split complete message
        split_complete_text = (
            f"╭━━━〔 ✅ SPLIT COMPLETE 〕━━━╮\n"
            f"┃ 📁 File: {trim_text(file_name, 30)}\n"
            f"┃ 📦 Original: {humanbytes(file_size)}\n"
            f"┃ 🔢 Parts: {len(split_files)}\n"
            f"┃ 📤 Status: Uploading parts...\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        await safe_edit(progress_msg, split_complete_text)
        
        # Upload all parts with individual progress tracking
        try:
            start_time = time.time()
            uploaded_parts = []
            
            # Generate thumbnail from ORIGINAL file (before split)
            first_part_thumbnail = None
            if tg_send_type == "video":
                try:
                    # Try to get thumbnail from original file first
                    width, height, duration = await Mdata01(file_location)
                    duration = max(duration, 1)
                    # Generate thumbnail from original file
                    first_part_thumbnail = await Gthumb02(bot, update, duration, file_location, task_id)
                    logger.info(f"Generated thumbnail from original file: {first_part_thumbnail}")
                except Exception as e:
                    logger.warning(f"Failed to generate thumbnail from original file: {e}")
                    # Fallback: try from first split part
                    if len(split_files) > 0:
                        try:
                            first_part = split_files[0]
                            width, height, duration = await Mdata01(first_part)
                            duration = max(duration, 1)
                            first_part_thumbnail = await Gthumb02(bot, update, duration, first_part, task_id)
                            logger.info(f"Generated thumbnail from first part: {first_part_thumbnail}")
                        except Exception as e2:
                            logger.warning(f"Failed to generate thumbnail from first part: {e2}")
                            first_part_thumbnail = None
            
            for i, part_file in enumerate(split_files, 1):
                part_name = os.path.basename(part_file)
                part_size = os.path.getsize(part_file)
                part_caption = f"<b>Part {i}/{len(split_files)}</b>\n{escape_html(trim_text(file_name, 50))}"
                
                # Show upload progress for this part
                part_upload_text = (
                    f"╭━━━〔 📤 UPLOADING PART {i}/{len(split_files)} 〕━━━╮\n"
                    f"┃ 📁 {trim_text(part_name, 35)}\n"
                    f"┃ 📦 Size: {humanbytes(part_size)}\n"
                    f"┃ 🔄 Status: Uploading...\n"
                    f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
                )
                await safe_edit(progress_msg, part_upload_text)
                
                # Upload with progress
                if tg_send_type == "video":
                    # Get video metadata for this part
                    try:
                        width, height, duration = await Mdata01(part_file)
                        duration = max(duration, 1)
                    except:
                        width, height, duration = 0, 0, 0
                    
                    uploaded_msg = await bot.send_video(
                        chat_id=update.message.chat.id,
                        video=part_file,
                        caption=part_caption,
                        parse_mode=enums.ParseMode.HTML,
                        duration=duration,
                        width=width,
                        height=height,
                        thumb=first_part_thumbnail,  # Use same thumbnail for all parts
                        supports_streaming=True,
                        reply_to_message_id=update.message.reply_to_message.id,
                        progress=progress_for_pyrogram,
                        progress_args=(f"Uploading Part {i}/{len(split_files)}", progress_msg, time.time(), part_name, False),
                    )
                else:
                    uploaded_msg = await bot.send_document(
                        chat_id=update.message.chat.id,
                        document=part_file,
                        caption=part_caption,
                        parse_mode=enums.ParseMode.HTML,
                        thumb=first_part_thumbnail,  # Use same thumbnail for all parts
                        reply_to_message_id=update.message.reply_to_message.id,
                        progress=progress_for_pyrogram,
                        progress_args=(f"Uploading Part {i}/{len(split_files)}", progress_msg, time.time(), part_name, False),
                    )
                
                uploaded_parts.append(uploaded_msg)
            
            # Cleanup thumbnail
            if first_part_thumbnail:
                asyncio.create_task(clendir(first_part_thumbnail))
            
            # Delete progress message
            try:
                await progress_msg.delete()
            except:
                pass
            
            # Send success message
            upload_duration = (datetime.now() - start).seconds
            minutes, seconds = divmod(upload_duration, 60)
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            
            success_text = (
                "╭─────────────────────────────╮\n"
                "│ ✅ UPLOAD COMPLETE │\n"
                "╰─────────────────────────────╯\n"
                f"📁 {escape_html(trim_text(file_name, 40))}\n"
                f"📦 {humanbytes(file_size)} • ⏱️ {time_str}\n"
                f"🔢 Parts: {len(split_files)} • ✂️ Split\n\n"
                "🔗 @Bimbobot69"
            )
            
            success_msg = await bot.send_message(
                chat_id=update.message.chat.id,
                text=success_text,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
            
            # Auto-delete success message after 15 seconds
            async def delete_success_msg():
                await asyncio.sleep(15)
                try:
                    await success_msg.delete()
                except:
                    pass
            asyncio.create_task(delete_success_msg())
            
            # Record download in quota system
            from plugins.user_quota import record_user_download
            record_user_download(update.from_user.id, file_size)
            
            # Cleanup
            asyncio.create_task(clendir(tmp_directory_for_each_user))
            return
            
        except Exception as e:
            logger.error(f"Split upload error: {e}")
            await safe_edit(progress_msg, f"❌ ERROR: {str(e)[:200]}")
            asyncio.create_task(clendir(tmp_directory_for_each_user))
            return

    # Convert download message to upload message (reuse same message)
    upload_start_text = (
        f"╭━━━〔 📤 UPLOAD STARTING 〕━━━╮\n"
        f"┃ 📁 File: {trim_text(file_name, 35)}\n"
        f"┃ 📦 Size: {humanbytes(file_size)}\n"
        f"┃ 🔄 Status: Preparing upload...\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    await safe_edit(progress_msg, upload_start_text)
    
    # Store the upload progress message ID for later deletion
    upload_progress_msg_id = progress_msg_id
    
    # Register UPLOAD task in unified progress tracker
    from helper_funcs.display_progress import register_task, update_task, set_user_message, remove_task
    upload_task_id = f"upload_{update.from_user.id}_{int(time.time())}"
    register_task(
        task_id=upload_task_id,
        user_id=update.from_user.id,
        filename=display_name,
        total_size=file_size,
        task_type='upload',
        engine='pyrogram'
    )
    set_user_message(update.from_user.id, update.message)
    
    # Remove download task from tracker
    remove_task(progress_task_id)

    thumbnail = None
    duration = 0
    width = 0
    height = 0

    try:
        start_time = time.time()

        if tg_send_type == "audio":
            duration = await Mdata03(file_location)
            thumbnail = await Gthumb01(bot, update, task_id)
            await bot.send_audio(
                chat_id=update.message.chat.id,
                audio=file_location,
                caption=description,
                parse_mode=enums.ParseMode.HTML,
                duration=duration,
                thumb=thumbnail,
                reply_to_message_id=update.message.reply_to_message.id,
                progress=progress_for_pyrogram,
                progress_args=(Translation.BIMBO_UPLOAD_START, progress_msg, start_time, file_name, False),
            )

        elif tg_send_type == "file":
            thumbnail = await Gthumb01(bot, update, task_id)
            await bot.send_document(
                chat_id=update.message.chat.id,
                document=file_location,
                thumb=thumbnail,
                caption=description,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=update.message.reply_to_message.id,
                progress=progress_for_pyrogram,
                progress_args=(Translation.BIMBO_UPLOAD_START, progress_msg, start_time, file_name, False),
            )

        elif tg_send_type == "vm":
            width, duration = await Mdata02(file_location)
            duration = max(duration, 1)
            thumbnail = await Gthumb02(bot, update, duration, file_location, task_id)
            await bot.send_video_note(
                chat_id=update.message.chat.id,
                video_note=file_location,
                duration=duration,
                length=width,
                thumb=thumbnail,
                reply_to_message_id=update.message.reply_to_message.id,
                progress=progress_for_pyrogram,
                progress_args=(Translation.BIMBO_UPLOAD_START, progress_msg, start_time, file_name, False),
            )

        elif tg_send_type == "video":
            width, height, duration = await Mdata01(file_location)
            duration = max(duration, 1)
            thumbnail = await Gthumb02(bot, update, duration, file_location, task_id)
            await bot.send_video(
                chat_id=update.message.chat.id,
                video=file_location,
                caption=description,
                parse_mode=enums.ParseMode.HTML,
                duration=duration,
                width=width,
                height=height,
                thumb=thumbnail,
                supports_streaming=True,
                reply_to_message_id=update.message.reply_to_message.id,
                progress=progress_for_pyrogram,
                progress_args=(Translation.BIMBO_UPLOAD_START, progress_msg, start_time, file_name, False),
            )

        else:
            thumbnail = await Gthumb01(bot, update, task_id)
            await bot.send_document(
                chat_id=update.message.chat.id,
                document=file_location,
                thumb=thumbnail,
                caption=description,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=update.message.reply_to_message.id,
                progress=progress_for_pyrogram,
                progress_args=(Translation.BIMBO_UPLOAD_START, progress_msg, start_time, file_name, False),
            )

        # Mark task as completed in unified tracker BEFORE log upload
        from helper_funcs.display_progress import update_task, remove_task
        update_task(upload_task_id, file_size, file_size, 0, 'completed', 'pyrogram')
        
        # Remove the task from tracker to stop progress updates
        remove_task(upload_task_id)
        
        # Wait a bit for progress callback to finish
        await asyncio.sleep(3)
        
        # DELETE the upload progress message with retry mechanism
        delete_success = False
        for attempt in range(3):
            try:
                await progress_msg.delete()
                logger.info(f"Deleted upload progress message: {progress_msg.id} (attempt {attempt + 1})")
                delete_success = True
                break
            except Exception as e:
                logger.warning(f"Could not delete upload progress message {progress_msg.id} (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    await asyncio.sleep(1)
        
        # If direct delete failed, try using bot.delete_messages
        if not delete_success:
            try:
                await bot.delete_messages(
                    chat_id=update.message.chat.id,
                    message_ids=progress_msg.id
                )
                logger.info(f"Deleted upload progress message using bot.delete_messages: {progress_msg.id}")
                delete_success = True
            except Exception as e:
                logger.warning(f"bot.delete_messages also failed: {e}")
        
        # DELETE the original message (update.message) if it's different from progress_msg
        if update.message.id != progress_msg.id:
            try:
                await update.message.delete()
                logger.info(f"Deleted original message: {update.message.id}")
            except Exception as e:
                logger.warning(f"Could not delete original message {update.message.id}: {e}")
        
        # Send NEW upload complete card (same style as upload progress)
        upload_duration = (datetime.now() - start).seconds
        minutes, seconds = divmod(upload_duration, 60)
        time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        
        success_text = (
            f"╭━━━〔 ✅ UPLOAD COMPLETE 〕━━━╮\n"
            f"┃ 📁 File: {trim_text(file_name, 35)}\n"
            f"┃ [████████████████████] 100.0%\n"
            f"┃ ⚡ Speed: {humanbytes(file_size / max(upload_duration, 1))}/s\n"
            f"┃ 📦 Progress: {humanbytes(file_size)} / {humanbytes(file_size)}\n"
            f"┃ ⏳ ETA: 0s\n"
            f"┃ 🕒 Elapsed: {time_str}\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        
        success_msg = await bot.send_message(
            chat_id=update.message.chat.id,
            text=success_text,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        
        # Auto-delete success message after 15 seconds
        async def delete_success_msg():
            await asyncio.sleep(15)
            try:
                await success_msg.delete()
            except:
                pass
        asyncio.create_task(delete_success_msg())
        
        # Now do log channel upload in BACKGROUND (silent, no progress)
        asyncio.create_task(send_log_media(
            bot=bot,
            user=update.from_user,
            file_path=file_location,
            link=original_link,
            file_name=original_name,
            media_type=tg_send_type,
            file_size=file_size,
            thumbnail=thumbnail,
            duration=duration,
            width=width,
            height=height,
        ))
        
        # Record download in quota system for real usage tracking
        from plugins.user_quota import record_user_download
        record_user_download(update.from_user.id, file_size)

        if thumbnail:
            asyncio.create_task(clendir(thumbnail))
        asyncio.create_task(clendir(file_location))

    except Exception as e:
        asyncio.create_task(clendir(file_location))
        if thumbnail:
            asyncio.create_task(clendir(thumbnail))
        err_str = str(e)
        # If the message was already deleted or invalid, skip (cleaner already removed it)
        if any(k in err_str for k in ("MESSAGE_ID_INVALID", "MessageIdInvalid", "message to edit not found",
                                       "message is not modified", "MESSAGE_NOT_MODIFIED",
                                       "MESSAGE_EMPTY", "query is too old")):
            logger.info(f"Skipping edit (msg gone): {err_str[:100]}")
            return
        try:
            await bot.edit_message_text(
                text=Translation.BIMBO_ERROR.format(escape_html(err_str)),
                chat_id=update.message.chat.id,
                message_id=update.message.id,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e2:
            logger.warning(f"Could not edit error message: {e2}")




async def terabox_call_back(bot, update):
    """Handle Terabox download callback using TeraboxDL package"""
    try:
        # Parse callback data: terabox=type|task_id
        cb_data = update.data
        parts = cb_data.split("|")
        tg_send_type = parts[0].split("=")[1]  # video, file, or audio
        task_id = parts[1] if len(parts) > 1 else ""
        
        if not url:
            # Try to get URL from JSON file (old method compatibility)
            save_ytdl_json_path = os.path.join(Config.BIMBO_DOWNLOAD_LOCATION, f"{update.from_user.id}.json")
            if os.path.exists(save_ytdl_json_path):
                with open(save_ytdl_json_path, "r", encoding="utf8") as f:
                    tb_json = json.load(f)
                url = tb_json.get("tb_share_url", "")
        
        if not url:
            await update.message.edit("❌ Invalid Terabox session. Please send the link again.")
            return
        
        logger.info(f"Terabox callback: type={tg_send_type}, url={url}")
        
        # Send processing message
        await safe_edit(update.message, 
            "🔄 **Processing Terabox Link**\n\n"
            "Extracting file information...\n"
            "⏳ Please wait..."
        )
        
        # Import terabox engine
        from plugins.terabox_engine import extract_terabox_info, download_terabox_file
        
        # Extract file info
        file_info = extract_terabox_info(url)
        
        if not file_info or not file_info.get('success'):
            error_msg = file_info.get('error', 'Unknown error') if file_info else 'Failed to extract info'
            error_type = file_info.get('error_type', 'unknown') if file_info else 'unknown'
            
            if error_type == 'config_missing':
                error_text = (
                    "❌ **Configuration Error**\n\n"
                    "Terabox cookie not configured.\n\n"
                    "📝 **How to fix:**\n"
                    "1. Login to Terabox in browser\n"
                    "2. Open DevTools (F12)\n"
                    "3. Go to Application → Cookies\n"
                    "4. Copy 'lang' and 'ndus' values\n"
                    "5. Set BIMBO_TERABOX_COOKIE in environment:\n"
                    "   `lang=en; ndus=YOUR_VALUE;`\n\n"
                    "Contact bot owner to configure this."
                )
            elif error_type == 'package_missing':
                error_text = (
                    "❌ **Package Not Installed**\n\n"
                    "TeraboxDL package is missing.\n\n"
                    "Contact bot owner to install:\n"
                    "`pip install terabox-downloader`"
                )
            else:
                error_text = (
                    f"❌ **Terabox Error**\n\n"
                    f"Failed to extract file info:\n"
                    f"`{escape_html(error_msg[:200])}`\n\n"
                    "This might be due to:\n"
                    "• Invalid or expired link\n"
                    "• Invalid cookie configuration\n"
                    "• Terabox API issues\n"
                    "• File not accessible"
                )
            
            await update.message.edit(error_text, parse_mode=enums.ParseMode.HTML)
            return
        
        # Update message with file info
        file_name = file_info.get('file_name', 'Unknown')
        file_size = file_info.get('file_size', 0)
        thumbnail_url = file_info.get('thumbnail', '')
        
        # Format file size
        if file_size:
            size_mb = file_size / (1024 * 1024)
            if size_mb >= 1024:
                size_text = f"{size_mb / 1024:.2f} GB"
            else:
                size_text = f"{size_mb:.2f} MB"
        else:
            size_text = "Unknown"
        
        display_name = trim_text(file_name, 30)
        
        await safe_edit(update.message, build_stage_card(display_name, "Downloading from Terabox...", "0 s"))
        
        # Download file
        tmp_directory = os.path.join(Config.BIMBO_DOWNLOAD_LOCATION, str(update.from_user.id))
        os.makedirs(tmp_directory, exist_ok=True)
        
        terabox_instance = file_info.get('teraboxdl_instance')
        file_path = download_terabox_file(terabox_instance, file_info, tmp_directory)
        
        if not file_path or not os.path.exists(file_path):
            await update.message.edit(
                "❌ **Download Failed**\n\n"
                "Failed to download file from Terabox.\n"
                "Please try again later.",
                parse_mode=enums.ParseMode.HTML
            )
            return
        
        actual_file_size = os.path.getsize(file_path)
        
        # Check Telegram file size limit
        if actual_file_size > Config.BIMBO_TG_MAX_FILE_SIZE:
            await update.message.edit(
                f"❌ File too large ({humanbytes(actual_file_size)}). "
                f"Telegram limit: {humanbytes(Config.BIMBO_TG_MAX_FILE_SIZE)}"
            )
            asyncio.create_task(clendir(file_path))
            return
        
        # Upload to Telegram
        await safe_edit(update.message, Translation.BIMBO_UPLOAD_START)
        
        thumbnail = None
        duration = 0
        width = 0
        height = 0
        
        try:
            start_time = time.time()
            description = f"<b>{escape_html(str(file_name)[:1021])}</b>"
            
            if tg_send_type == "audio":
                duration = await Mdata03(file_path)
                thumbnail = await Gthumb01(bot, update, task_id)
                await bot.send_audio(
                    chat_id=update.message.chat.id,
                    audio=file_path,
                    caption=description,
                    parse_mode=enums.ParseMode.HTML,
                    duration=duration,
                    thumb=thumbnail,
                    reply_to_message_id=update.message.reply_to_message.id,
                    progress=progress_for_pyrogram,
                    progress_args=(Translation.BIMBO_UPLOAD_START, update.message, start_time, file_name, False),
                )
            
            elif tg_send_type == "file":
                thumbnail = await Gthumb01(bot, update, task_id)
                await bot.send_document(
                    chat_id=update.message.chat.id,
                    document=file_path,
                    thumb=thumbnail,
                    caption=description,
                    parse_mode=enums.ParseMode.HTML,
                    reply_to_message_id=update.message.reply_to_message.id,
                    progress=progress_for_pyrogram,
                    progress_args=(Translation.BIMBO_UPLOAD_START, update.message, start_time, file_name, False),
                )
            
            elif tg_send_type == "video":
                width, height, duration = await Mdata01(file_path)
                duration = max(duration, 1)
                thumbnail = await Gthumb02(bot, update, duration, file_path, task_id)
                await bot.send_video(
                    chat_id=update.message.chat.id,
                    video=file_path,
                    caption=description,
                    parse_mode=enums.ParseMode.HTML,
                    duration=duration,
                    width=width,
                    height=height,
                    thumb=thumbnail,
                    supports_streaming=True,
                    reply_to_message_id=update.message.reply_to_message.id,
                    progress=progress_for_pyrogram,
                    progress_args=(Translation.BIMBO_UPLOAD_START, update.message, start_time, file_name, False),
                )
            
            # Send to log channel
            try:
                await send_log_media(
                    bot=bot,
                    user=update.from_user,
                    file_path=file_path,
                    link=url,
                    file_name=file_name,
                    media_type=tg_send_type,
                    file_size=actual_file_size,
                    thumbnail=thumbnail,
                    duration=duration,
                    width=width,
                    height=height,
                )
            except Exception as e:
                logger.warning(f"Log channel error: {e}")
            
            # Cleanup
            if thumbnail:
                asyncio.create_task(clendir(thumbnail))
            asyncio.create_task(clendir(file_path))
            
            # Success message
            upload_time = int(time.time() - start_time)
            success_text = (
                "<b>✅ Uploaded successfully</b>\n\n"
                f"<b>📁 File:</b> <code>{escape_html(trim_text(file_name, 60))}</code>\n"
                f"<b>📦 Size:</b> {humanbytes(actual_file_size)}\n"
                f"<b>⏱ Upload Time:</b> {upload_time}s\n\n"
                "<b>Join:</b> @Bimbobot69"
            )
            await bot.edit_message_text(
                text=success_text,
                chat_id=update.message.chat.id,
                message_id=update.message.id,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        
        except Exception as e:
            asyncio.create_task(clendir(file_path))
            if thumbnail:
                asyncio.create_task(clendir(thumbnail))
            await bot.edit_message_text(
                text=Translation.BIMBO_ERROR.format(escape_html(str(e))),
                chat_id=update.message.chat.id,
                message_id=update.message.id,
                parse_mode=enums.ParseMode.HTML,
            )
    
    except Exception as e:
        logger.error(f"Terabox callback error: {e}", exc_info=True)
        try:
            await update.message.edit(f"❌ Error: {escape_html(str(e)[:200])}")
        except:
            pass



async def clendir(directory):
    try:
        os.remove(directory)
    except Exception:
        pass
    try:
        shutil.rmtree(directory)
    except Exception:
        pass
