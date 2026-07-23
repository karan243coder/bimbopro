# ============================================================
# BIMBO Bot v3.0 - ULTIMATE ADVANCED PROGRESS DISPLAY
# Powered by BIMBO | Support: @Bimbo69
# ============================================================
# Features:
# - Per-task engine display (⚡Aria2, 🎬yt-dlp, 🔷Pyrogram)
# - Multi-task in single message
# - Real-time system stats per task
# - Beautiful animated progress bars
# - Live speed graph (text-based)
# - Download/Upload separation
# - Cancel handler per task
# ============================================================

import logging
import math
import re
import time
import psutil
import os
import asyncio
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===================== GLOBAL TASK TRACKER =====================
# Ek centralized store jisme saare active tasks ka data rahega
_task_store = {}         # task_id -> task_data
_user_tasks = {}         # user_id -> [task_ids]
_task_messages = {}      # user_id -> message object

# Speed tracking for smoothing
speed_history = {}
last_edit_time = {}
last_progress_text = {}

PROGRESS_UPDATE_INTERVAL = 2
SPEED_HISTORY_LIMIT = 15


# ===================== ENGINE DETECTION =====================
ENGINE_ICONS = {
    'aria2': '⚡Aria2',
    'yt-dlp': '🎬yt-dlp', 
    'pyrogram': '🔷Pyro',
    'libtorrent': '🧲Torrent',
    'ffmpeg': '🎞️FFmpeg',
    'requests': '🌐HTTP',
    'direct': '📁Direct',
    'terabox': '📦Terabox',
    'xhamster': '🔞XHams',
    'unknown': '❓Unknown'
}

ENGINE_COLORS = {
    'aria2': '🟢',
    'yt-dlp': '🔵',
    'pyrogram': '🟣',
    'libtorrent': '🟠',
    'ffmpeg': '🔴',
}


# ===================== TASK MANAGEMENT =====================

def register_task(task_id, user_id, filename="Unknown", total_size=0, 
                  task_type="download", engine="pyrogram", source_url=""):
    """Naya task register karo tracker mein"""
    task = {
        'id': task_id,
        'user_id': user_id,
        'filename': filename,
        'total_size': total_size,
        'downloaded': 0,
        'speed': 0,
        'avg_speed': 0,
        'percentage': 0,
        'status': 'queued',      # queued, downloading, uploading, completed, failed, cancelled
        'task_type': task_type,   # download, upload
        'engine': engine,
        'source_url': source_url,
        'start_time': time.time(),
        'eta': 0,
        'elapsed': 0,
        'error': None,
        'completed': False,
        'speed_samples': [],
        'last_update': time.time(),
        'cancel_flag': False,
    }
    
    _task_store[task_id] = task
    
    if user_id not in _user_tasks:
        _user_tasks[user_id] = []
    if task_id not in _user_tasks[user_id]:
        _user_tasks[user_id].append(task_id)
    
    return task


def update_task(task_id, downloaded, total_size=0, speed=0, status=None, engine=None):
    """Task ka progress update karo"""
    task = _task_store.get(task_id)
    if not task:
        return None
    
    now = time.time()
    task['downloaded'] = downloaded
    if total_size > 0:
        task['total_size'] = total_size
    if speed > 0:
        task['speed'] = speed
    
    # Smooth speed (moving average)
    task['speed_samples'].append(speed if speed > 0 else task['speed'])
    if len(task['speed_samples']) > 10:
        task['speed_samples'].pop(0)
    task['avg_speed'] = sum(task['speed_samples']) / len(task['speed_samples']) if task['speed_samples'] else 0
    
    if task['total_size'] > 0:
        task['percentage'] = (task['downloaded'] / task['total_size']) * 100
        remaining = task['total_size'] - task['downloaded']
        task['eta'] = remaining / task['avg_speed'] if task['avg_speed'] > 0 else 0
    else:
        task['percentage'] = 0
        task['eta'] = 0
    
    task['elapsed'] = now - task['start_time']
    task['last_update'] = now
    
    if engine:
        task['engine'] = engine
    if status:
        task['status'] = status
        if status in ['completed', 'failed', 'cancelled']:
            task['completed'] = True
    
    return task


def get_task(task_id):
    return _task_store.get(task_id)


def remove_task(task_id):
    task = _task_store.pop(task_id, None)
    if task:
        uid = task['user_id']
        if uid in _user_tasks and task_id in _user_tasks[uid]:
            _user_tasks[uid].remove(task_id)
    return task


def get_user_active_tasks(user_id):
    """Ek user ke saare active tasks lao"""
    active = []
    for tid in _user_tasks.get(user_id, []):
        t = _task_store.get(tid)
        if t and not t.get('completed'):
            active.append(t)
    return active


def get_user_all_tasks(user_id):
    """User ke saare tasks (completed bhi)"""
    all_t = []
    for tid in _user_tasks.get(user_id, []):
        t = _task_store.get(tid)
        if t:
            all_t.append(t)
    return all_t


def set_user_message(user_id, message):
    _task_messages[user_id] = message


def get_user_message(user_id):
    return _task_messages.get(user_id)


def cancel_task(task_id):
    """Task cancel karo"""
    task = _task_store.get(task_id)
    if task:
        task['cancel_flag'] = True
        task['status'] = 'cancelled'
        task['completed'] = True
        return True
    return False


# ===================== UI HELPERS =====================

def trim_text(text, limit=35):
    text = str(text or "Unknown File").strip()
    text = re.sub(r'\s+', ' ', text)
    if len(text) <= limit:
        return text
    return text[:limit-3] + "..."


def humanbytes(size):
    if size is None or size <= 0:
        return "0B"
    labels = ['B', 'KB', 'MB', 'GB', 'TB']
    for i, label in enumerate(labels):
        if size < 1024 or i == len(labels)-1:
            return f"{size:.2f}{label}" if i > 0 else f"{int(size)}{label}"
        size /= 1024


def format_speed(bytes_per_sec):
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "0B/s"
    return f"{humanbytes(bytes_per_sec)}/s"


def format_time(seconds=None, milliseconds=None):
    """Unified time formatter - accepts seconds (positional) or milliseconds (keyword)"""
    # Support: format_time(seconds_value) and TimeFormatter(milliseconds=X)
    if seconds is not None:
        ms = seconds * 1000
    elif milliseconds is not None:
        ms = milliseconds
    else:
        return "0s"
    
    if ms is None or ms < 0:
        return "∞"
    
    ms = int(ms)
    seconds = ms // 1000
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def build_advanced_bar(percentage, width=14):
    """
    Super advanced progress bar:
    [████████░░░░] 67.3%
    """
    percentage = max(0, min(100, percentage))
    filled = int(width * percentage / 100)
    empty = width - filled
    
    if percentage >= 100:
        bar = "█" * width
    elif percentage > 80:
        bar = "█" * filled + "░" * empty
    elif percentage > 50:
        bar = "▓" * filled + "░" * empty
    elif percentage > 20:
        bar = "▒" * filled + "░" * empty
    else:
        bar = "░" * width
    
    return f"`[{bar}]`"


def get_speed_indicator(speed_bytes):
    """Speed ke hisaab se emoji indicator"""
    mbps = speed_bytes / (1024 * 1024)
    if mbps > 10:
        return "🚀"  # Very fast
    elif mbps > 5:
        return "⚡"   # Fast
    elif mbps > 1:
        return "🔸"   # Medium
    elif mbps > 0.1:
        return "🐢"   # Slow
    else:
        return "⏸️"   # Stalled


def get_status_emoji(status):
    emojis = {
        'queued': '⏳',
        'downloading': '📥',
        'uploading': '📤', 
        'completed': '✅',
        'failed': '❌',
        'cancelled': '🚫',
        'starting': '🔄',
    }
    return emojis.get(status, '❓')


def get_system_stats_advanced():
    """Advanced system statistics"""
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_seconds = time.time() - psutil.boot_time()
        uptime = format_time(seconds=boot_seconds)
        
        # CPU bar
        cpu_bar = "█" * int(cpu/10) + "░" * (10 - int(cpu/10))
        
        # Memory bar
        mem_bar = "█" * int(mem.percent/10) + "░" * (10 - int(mem.percent/10))
        
        # Active tasks count
        active_count = sum(1 for t in _task_store.values() if not t['completed'])
        total_count = len(_task_store)
        
        # Total speeds
        total_dl = sum(t.get('avg_speed', 0) for t in _task_store.values() 
                      if not t['completed'] and t['task_type'] == 'download')
        total_ul = sum(t.get('avg_speed', 0) for t in _task_store.values()
                      if not t['completed'] and t['task_type'] == 'upload')
        
        return {
            'cpu': cpu,
            'cpu_bar': cpu_bar,
            'ram': mem.percent,
            'ram_bar': mem_bar,
            'ram_used': mem.used,
            'ram_total': mem.total,
            'disk_free': disk.free,
            'disk_total': disk.total,
            'disk_percent': disk.percent,
            'uptime': uptime,
            'active_tasks': active_count,
            'total_tasks': total_count,
            'total_dl_speed': total_dl,
            'total_ul_speed': total_ul,
        }
    except:
        return {'cpu': 0, 'cpu_bar': '░'*10, 'ram': 0, 'ram_bar': '░'*10,
                'disk_free': 0, 'disk_percent': 0, 'uptime': '0s',
                'active_tasks': 0, 'total_tasks': 0,
                'total_dl_speed': 0, 'total_ul_speed': 0}


# ===================== MAIN PROGRESS BUILDER =====================

async def build_advanced_progress_text(user_id):
    """
    **THE ULTIMATE PROGRESS UI** 🔥
    
    Multiple tasks ke saath ek hi message mein dikhega:
    
    ╔══════════════════════════════════╗
    ║        BIMBO PROGRESS            ║
    ╚══════════════════════════════════╝
    
    📥 TASK 1: video.mp4
    ┌─────────────────────────────┐
    │ [████████░░░░░░] 67.3%     │
    │ ⚡ Speed : 2.4MB/s         │
    │ 📦 Done  : 156.2MB/256MB   │
    │ ⏱️ ETA   : 42s             │
    │ 🎬 Engine: yt-dlp          │
    │ 📊 Status: ✅ Downloading  │
    └─────────────────────────────┘
    
    📤 TASK 2: file.zip
    ┌─────────────────────────────┐
    │ [████████████] 100%        │
    │ ⚡ Speed : 5.1MB/s         │
    │ 📦 Done  : 1.2GB/1.2GB    │
    │ 🎬 Engine: ⚡Aria2          │
    │ 📊 Status: ✅ Completed    │
    └─────────────────────────────┘
    
    ═══════════════════════════════════
    🖥️ SYSTEM STATS
    ├ 🧠 CPU : [████░░░░░░] 45.2%
    ├ 💾 RAM : [███░░░░░░░] 35.1%
    ├ 💿 Disk: Free: 16.1GB (78%)
    └ ⏱️ Uptime: 12d 15h 32m
    
    📊 NETWORK
    ├ ⬇️ DL : 2.4MB/s
    └ ⬆️ UL : 733KB/s
    ═══════════════════════════════════
    """
    
    tasks = get_user_active_tasks(user_id)
    all_tasks = get_user_all_tasks(user_id)
    
    if not all_tasks:
        return None
    
    # ===== HEADER =====
    text = "╔════════════════════════════════════╗\n"
    text += "║        📊 **BIMBO PROGRESS**        ║\n"
    text += "╚════════════════════════════════════╝\n\n"
    
    # ===== TASKS =====
    for i, task in enumerate(tasks, 1):
        name = trim_text(task['filename'], 30)
        pct = task['percentage']
        bar = build_advanced_bar(pct)
        transferred = humanbytes(task['downloaded'])
        total = humanbytes(task['total_size']) if task['total_size'] > 0 else "?"
        spd = format_speed(task['avg_speed'])
        eta = format_time(task['eta'])
        elapsed = format_time(task['elapsed'])
        engine_icon = ENGINE_ICONS.get(task['engine'], task['engine'])
        status_emoji = get_status_emoji(task['status'])
        speed_indicator = get_speed_indicator(task['avg_speed'])
        task_type_emoji = "📥" if task['task_type'] == 'download' else "📤"
        task_type_name = "DOWNLOAD" if task['task_type'] == 'download' else "UPLOAD"
        
        # Task box
        text += f"**{task_type_emoji} TASK {i}: `{name}`**\n"
        text += "┌─────────────────────────────┐\n"
        text += f"│ {bar} {pct:.1f}%\n"
        text += f"│ {speed_indicator} **Speed:** {spd}\n"
        text += f"│ 📦 **Done:** {transferred} / {total}\n"
        text += f"│ ⏱️ **ETA:** {eta} | **Elapsed:** {elapsed}\n"
        text += f"│ 🎬 **Engine:** {engine_icon}\n"
        text += f"│ 📊 **Status:** {status_emoji} {status_text(task)}\n"
        text += "└─────────────────────────────┘\n\n"
    
    # ===== COMPLETED TASKS SUMMARY =====
    completed = [t for t in all_tasks if t['completed']]
    if completed:
        text += "**✅ Completed Tasks:**\n"
        for t in completed[-3:]:  # Last 3
            text += f"├ {trim_text(t['filename'], 25)} - {humanbytes(t['total_size'])}\n"
        text += "└─────────────────────────────\n\n"
    
    # ===== NO TASKS =====
    if not tasks and not completed:
        text += "ℹ️ **No active tasks.**\nSend a link to start!\n\n"
    
    # ===== SYSTEM STATS =====
    stats = get_system_stats_advanced()
    
    text += "═" * 32 + "\n"
    text += f"🖥️ **SYSTEM** │ ⏱️ {stats['uptime']}\n"
    text += f"├ 🧠 **CPU:** `[{stats['cpu_bar']}]` {stats['cpu']:.1f}%\n"
    text += f"├ 💾 **RAM:** `[{stats['ram_bar']}]` {stats['ram']:.1f}%\n"
    text += f"│ └ {humanbytes(stats['ram_used'])} / {humanbytes(stats['ram_total'])}\n"
    text += f"├ 💿 **Disk:** Free {humanbytes(stats['disk_free'])} ({100-stats['disk_percent']:.0f}%)\n"
    text += f"├ 📊 **Tasks:** {stats['active_tasks']} active / {stats['total_tasks']} total\n"
    text += f"├ ⬇️ **DL:** {format_speed(stats['total_dl_speed'])}\n"
    text += f"└ ⬆️ **UL:** {format_speed(stats['total_ul_speed'])}\n"
    text += "═" * 32
    
    return text


def status_text(task):
    """Human readable status"""
    s = task['status']
    if s == 'downloading':
        return "⬇️ Downloading..."
    elif s == 'uploading':
        return "⬆️ Uploading..."
    elif s == 'queued':
        return "⏳ Queued..."
    elif s == 'completed':
        return "✅ Completed"
    elif s == 'failed':
        return f"❌ Failed: {task.get('error', 'Unknown')}"
    elif s == 'cancelled':
        return "🚫 Cancelled"
    return s.capitalize()


_last_progress_update = {}  # user_id -> timestamp

async def update_user_progress(client, user_id):
    """User ka progress message update karo - with FloodWait protection"""
    from pyrogram.errors import FloodWait
    
    now = time.time()
    last = _last_progress_update.get(user_id, 0)
    
    # Sirf har 3 sec mein update karo (FloodWait se bachne ke liye)
    if now - last < 3:
        return
    
    message = get_user_message(user_id)
    if not message:
        return
    
    text = await build_advanced_progress_text(user_id)
    if not text:
        try:
            await message.edit_text("✅ **All tasks completed!** 🎉")
            _task_messages.pop(user_id, None)
        except:
            pass
        return
    
    try:
        _last_progress_update[user_id] = now
        await message.edit_text(text)
    except FloodWait as e:
        logger.warning(f"⏳ FloodWait: {e.value}s - progress update paused")
        _last_progress_update[user_id] = now + e.value
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e) and "same content" not in str(e).lower():
            logger.error(f"Progress update error: {e}")


# ===================== LEGACY SUPPORT =====================
# Purane progress_for_pyrogram function ko bhi support karo

def cleanup_progress_state(msg_id):
    speed_history.pop(msg_id, None)
    last_edit_time.pop(msg_id, None)
    last_progress_text.pop(msg_id, None)


async def progress_for_pyrogram(current, total, ud_type, message, start,
                                file_name="", is_download=False):
    """
    Individual progress callback for uploads/downloads.
    Shows detailed progress card for each task separately.
    """
    if not message or total == 0:
        return
    
    try:
        msg_id = message.id
        chat_id = message.chat.id if hasattr(message, 'chat') else 0
    except:
        return
    
    # CRITICAL: Ignore log channel uploads completely
    from config import Config
    if hasattr(Config, 'BIMBO_LOG_CHANNEL') and Config.BIMBO_LOG_CHANNEL:
        # Convert to int for comparison
        try:
            log_channel_id = int(str(Config.BIMBO_LOG_CHANNEL).replace('-100', ''))
            chat_id_clean = int(str(chat_id).replace('-100', ''))
            if chat_id_clean == log_channel_id or chat_id == Config.BIMBO_LOG_CHANNEL:
                # This is a log channel upload, completely ignore it
                return
        except:
            pass
    
    now = time.time()
    diff = max(now - start, 0.001)
    last_time = last_edit_time.get(msg_id, 0)
    
    if (now - last_time < PROGRESS_UPDATE_INTERVAL) and current not in (0, total) and current != total:
        return
    
    # Smooth speed
    instant_speed = current / diff if diff > 0 else 0
    history = speed_history.setdefault(msg_id, [])
    history.append(instant_speed)
    if len(history) > SPEED_HISTORY_LIMIT:
        history.pop(0)
    avg_speed = sum(history) / len(history) if history else instant_speed
    
    percentage = (current * 100) / total
    percentage = min(max(percentage, 0), 100)
    
    # Build individual progress card
    task_type = "UPLOAD" if not is_download else "DOWNLOAD"
    status_emoji = "📤" if not is_download else "📥"
    
    # Format sizes
    current_size = humanbytes(current)
    total_size = humanbytes(total)
    speed_text = f"{humanbytes(avg_speed)}/s"
    
    # Calculate ETA
    if avg_speed > 0 and current < total:
        eta_seconds = (total - current) / avg_speed
        eta_text = format_time(eta_seconds)
    else:
        eta_text = "0s"
    
    # Elapsed time
    elapsed_text = format_time(diff)
    
    # Progress bar
    bar_length = 20
    filled = int(bar_length * percentage / 100)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    # Build progress card
    display_name = trim_text(file_name or ud_type or "File", 35)
    
    progress_text = (
        f"╭━━━〔 {status_emoji} {task_type} 〕━━━╮\n"
        f"┃ 📁 File: {display_name}\n"
        f"┃ [{bar}] {percentage:.1f}%\n"
        f"┃ ⚡ Speed: {speed_text}\n"
        f"┃ 📦 Progress: {current_size} / {total_size}\n"
        f"┃ ⏳ ETA: {eta_text}\n"
        f"┃ 🕒 Elapsed: {elapsed_text}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    
    # Update message
    try:
        await message.edit_text(progress_text)
        last_edit_time[msg_id] = now
        last_progress_text[msg_id] = progress_text
        
        # Cleanup when complete
        if current == total:
            cleanup_progress_state(msg_id)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e).upper():
            logger.debug(f"Progress edit error: {e}")
        if current == total:
            cleanup_progress_state(msg_id)


def cleanup_all_progress():
    _task_store.clear()
    _user_tasks.clear()
    _task_messages.clear()
    speed_history.clear()
    last_edit_time.clear()
    last_progress_text.clear()

# ===================== LEGACY ALIASES (for backward compatibility) =====================
# Purane code mein TimeFormatter use hota hai
TimeFormatter = format_time
