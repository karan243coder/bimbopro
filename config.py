# BIMBO URL Bot v4.0 - ULTIMATE EDITION
# Powered by BIMBO | Support: @Bimbo69
# Optimized for Koyeb / Heroku / VPS

import os
import re
from os import environ

id_pattern = re.compile(r'^\d+$')


def is_enabled(value, default):
    if not value:
        return default
    if value.lower() in ["true", "yes", "1", "enable", "y", "on"]:
        return True
    if value.lower() in ["false", "no", "0", "disable", "n", "off"]:
        return False
    return default


def _int(name, default):
    v = environ.get(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _str(name, default=""):
    v = environ.get(name)
    return v if v else default


class Config(object):
    # ==================================================================
    # BOT BASICS
    # ==================================================================
    BIMBO_BOT_TOKEN = _str("BIMBO_BOT_TOKEN")
    BIMBO_BOT_USERNAME = _str("BIMBO_BOT_USERNAME", "")
    BIMBO_API_ID = _int("BIMBO_API_ID", 0)
    BIMBO_API_HASH = _str("BIMBO_API_HASH")
    BIMBO_SESSION_NAME = _str("BIMBO_SESSION_NAME", "BIMBO_BOT")
    BIMBO_OWNER_ID = _int("BIMBO_OWNER_ID", 0)

    # Admin IDs (space-separated, owner auto-admin)
    _admins = _str("BIMBO_ADMIN_IDS", "")
    BIMBO_ADMIN_IDS = set()
    if _admins:
        for _x in _admins.split():
            try:
                BIMBO_ADMIN_IDS.add(int(_x))
            except ValueError:
                pass
    try:
        if BIMBO_OWNER_ID:
            BIMBO_ADMIN_IDS.add(BIMBO_OWNER_ID)
    except Exception:
        pass

    # ==================================================================
    # CHANNELS & DATABASE
    # ==================================================================
    BIMBO_DATABASE_URL = _str("BIMBO_DATABASE_URL")
    BIMBO_LOG_CHANNEL = _int("BIMBO_LOG_CHANNEL", 0)
    _up = _str("BIMBO_UPDATES_CHANNEL", "")
    BIMBO_UPDATES_CHANNEL = int(_up) if _up and id_pattern.search(_up) else None
    BIMBO_FORCE_SUB_INVITE = _str("BIMBO_FORCE_SUB_INVITE", "")

    # ==================================================================
    # FILE SIZE LIMITS
    # ==================================================================
    BIMBO_TG_MAX_FILE_SIZE = _int("BIMBO_TG_MAX_FILE_SIZE", 2000 * 1024 * 1024)  # 2GB default safe
    BIMBO_FREE_USER_MAX_FILE_SIZE = _int("BIMBO_FREE_USER_MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024)
    BIMBO_SPLIT_SIZE = _int("BIMBO_SPLIT_SIZE", 1900 * 1024 * 1024)
    BIMBO_CHUNK_SIZE = _int("BIMBO_CHUNK_SIZE", 1024 * 1024)

    BIMBO_MAX_MESSAGE_LENGTH = 4096
    BIMBO_PROCESS_MAX_TIMEOUT = _int("BIMBO_PROCESS_MAX_TIMEOUT", 7200)
    BIMBO_HTTP_PROXY = _str("BIMBO_HTTP_PROXY", "")

    # ==================================================================
    # DOWNLOAD LOCATION
    # ==================================================================
    BIMBO_USE_TMP = is_enabled(_str("BIMBO_USE_TMP", "true"), True)
    _custom_dl = _str("BIMBO_DOWNLOAD_LOCATION", "")
    if _custom_dl:
        BIMBO_DOWNLOAD_LOCATION = _custom_dl
    else:
        BIMBO_DOWNLOAD_LOCATION = "/tmp/bimbo_downloads" if BIMBO_USE_TMP else "./DOWNLOADS"

    BIMBO_AUTO_CLEANUP_HOURS = _int("BIMBO_AUTO_CLEANUP_HOURS", 2)
    BIMBO_THUMB_CACHE_MB = _int("BIMBO_THUMB_CACHE_MB", 200)

    # ==================================================================
    # SPEED TUNING
    # ==================================================================
    BIMBO_WORKERS = _int("BIMBO_WORKERS", 8)
    BIMBO_MAX_CONCURRENT_TASKS = _int("BIMBO_MAX_CONCURRENT_TASKS", 2)
    BIMBO_PROGRESS_UPDATE_INTERVAL = _int("BIMBO_PROGRESS_UPDATE_INTERVAL", 3)
    YTDLP_CONCURRENT_FRAGMENTS = _int("YTDLP_CONCURRENT_FRAGMENTS", 10)
    YTDLP_USE_ARIA2C = is_enabled(_str("YTDLP_USE_ARIA2C", "true"), True)
    ARIA2_BUFFER_SIZE = _str("ARIA2_BUFFER_SIZE", "4M")

    # ==================================================================
    # TOKEN / SHORTENER (existing premium system)
    # ==================================================================
    BIMBO = is_enabled(_str("BIMBO", "False"), False)
    BIMBO_URL = _str("BIMBO_URL", "modijiurl.com")
    BIMBO_API = _str("BIMBO_API", "")
    BIMBO_TUTORIAL = _str("BIMBO_TUTORIAL", "https://t.me/How_To_Open_Linkl")

    # ==================================================================
    # NEW: FEATURE FLAGS (v4.0)
    # ==================================================================
    # Instagram
    BIMBO_INSTAGRAM_COOKIE = _str("BIMBO_INSTAGRAM_COOKIE", "")

    # Google Drive upload
    BIMBO_GDRIVE_FOLDER_ID = _str("BIMBO_GDRIVE_FOLDER_ID", "")
    BIMBO_GDRIVE_CREDENTIALS = _str("BIMBO_GDRIVE_CREDENTIALS", "")  # path to service account json
    GDRIVE_ENABLED = bool(BIMBO_GDRIVE_CREDENTIALS and os.path.exists(BIMBO_GDRIVE_CREDENTIALS))

    # Mega
    MEGA_EMAIL = _str("MEGA_EMAIL", "")
    MEGA_PASSWORD = _str("MEGA_PASSWORD", "")
    MEGA_ENABLED = bool(MEGA_EMAIL and MEGA_PASSWORD)

    # Gofile.io (no creds required, API key optional)
    GOFILE_TOKEN = _str("GOFILE_TOKEN", "")

    # Anti-Spam: rate limit per user (seconds between downloads for free users)
    RATE_LIMIT_SECONDS = _int("RATE_LIMIT_SECONDS", 15)

    # Watermark defaults
    BIMBO_WATERMARK_TEXT = _str("BIMBO_WATERMARK_TEXT", "")
    BIMBO_WATERMARK_FONT = _str("BIMBO_WATERMARK_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    BIMBO_WATERMARK_POSITION = _str("BIMBO_WATERMARK_POSITION", "bottom-right")

    # Torrent Search (1337x/YTS mirror fallback)
    TORRENT_SEARCH_ENABLED = is_enabled(_str("TORRENT_SEARCH_ENABLED", "true"), True)

    # Maintenance mode
    MAINTENANCE_MODE = is_enabled(_str("MAINTENANCE_MODE", "false"), False)

    # Custom start media
    BIMBO_START_PIC = _str("BIMBO_START_PIC", "")  # telegra.ph image link or file_id
    BIMBO_START_MSG = _str("BIMBO_START_MSG", "")  # override default start text

    # Auto-delete user messages (bot responses after N seconds in private)
    AUTO_DELETE_SECONDS = _int("AUTO_DELETE_SECONDS", 10)  # 10 sec clean mode

    # Logging
    BIMBO_VERBOSE_LOG = is_enabled(_str("BIMBO_VERBOSE_LOG", "false"), False)


# ==================================================================
# MODULE-LEVEL ALIASES (legacy imports in existing code)
# ==================================================================
BIMBO_OWNER_ID = Config.BIMBO_OWNER_ID
BIMBO_DATABASE_URL = Config.BIMBO_DATABASE_URL
BIMBO_DOWNLOAD_LOCATION = Config.BIMBO_DOWNLOAD_LOCATION
BIMBO_DOWNLOAD_DIR = _str("BIMBO_DOWNLOAD_DIR", Config.BIMBO_DOWNLOAD_LOCATION)
BIMBO_TERABOX_COOKIE = _str("BIMBO_TERABOX_COOKIE", "")
BIMBO_WATERMARK_TEXT = Config.BIMBO_WATERMARK_TEXT
