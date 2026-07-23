import os
import re
import asyncio
import logging
from urllib.parse import urlparse
try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    lt = None
    LIBTORRENT_AVAILABLE = False
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import BIMBO_DOWNLOAD_LOCATION
from database.adduser import AddUser

logger = logging.getLogger(__name__)

# Torrent session
torrent_session = None

def get_torrent_session():
    """Get or create libtorrent session"""
    global torrent_session
    if torrent_session is None:
        settings = {
            'user_agent': 'BIMBO-Bot/1.0',
            'listen_interfaces': '0.0.0.0:6881',
            'download_rate_limit': 0,  # Unlimited
            'upload_rate_limit': 0,    # Unlimited
            'connections_limit': 200,
            'alert_mask': lt.alert.category_t.status_notification | lt.alert.category_t.error_notification,
        }
        torrent_session = lt.session(settings)
    return torrent_session

def is_torrent_link(url: str) -> bool:
    """Check if URL is a torrent or magnet link"""
    url = url.strip()
    
    # Magnet link
    if url.startswith('magnet:'):
        return True
    
    # .torrent file URL
    if url.endswith('.torrent'):
        return True
    
    # Torrent hosting sites
    torrent_domains = [
        'thepiratebay', '1337x', 'rarbg', 'yts', 'eztv',
        'torrentz', 'limetorrents', 'torlock', 'demonoid'
    ]
    
    try:
        domain = urlparse(url).netloc.lower()
        return any(t in domain for t in torrent_domains)
    except:
        return False

async def download_torrent(url: str, download_path: str, progress_callback=None):
    """Download torrent using libtorrent"""
    try:
        session = get_torrent_session()
        
        # Parse magnet or torrent
        if url.startswith('magnet:'):
            params = lt.parse_magnet_uri(url)
            params.save_path = download_path
            handle = session.add_torrent(params)
        else:
            # Download .torrent file first
            import requests
            response = requests.get(url, timeout=30)
            torrent_data = lt.bdecode(response.content)
            
            info = lt.torrent_info(torrent_data)
            handle = session.add_torrent({
                'ti': info,
                'save_path': download_path
            })
        
        # Wait for metadata
        logger.info(f"Torrent: Waiting for metadata...")
        timeout = 60
        while not handle.has_metadata() and timeout > 0:
            await asyncio.sleep(1)
            timeout -= 1
        
        if not handle.has_metadata():
            raise Exception("Timeout: Could not get torrent metadata")
        
        # Get torrent info
        torrent_info = handle.torrent_file()
        file_name = torrent_info.name()
        total_size = torrent_info.total_size()
        
        logger.info(f"Torrent: {file_name} ({total_size / (1024*1024):.2f} MB)")
        
        # Download with progress
        while not handle.is_seed():
            status = handle.status()
            
            if progress_callback:
                await progress_callback(
                    downloaded=status.total_done,
                    total=total_size,
                    speed=status.download_rate,
                    progress=status.progress * 100,
                    file_name=file_name
                )
            
            await asyncio.sleep(1)
        
        # Download complete
        file_path = os.path.join(download_path, file_name)
        return {
            'success': True,
            'file_path': file_path,
            'file_name': file_name,
            'size': total_size
        }
        
    except Exception as e:
        logger.error(f"Torrent download error: {e}")
        return {
            'success': False,
            'error': str(e)
        }

async def torrent_progress_update(message, downloaded, total, speed, progress, file_name):
    """Update progress message"""
    try:
        downloaded_mb = downloaded / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        speed_mb = speed / (1024 * 1024)
        
        # Progress bar
        bar_length = 20
        filled = int(bar_length * progress / 100)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        text = (
            f"📥 **Downloading Torrent**\n\n"
            f"📁 **File:** `{file_name[:50]}{'...' if len(file_name) > 50 else ''}`\n\n"
            f"📊 **Progress:** {bar} {progress:.1f}%\n"
            f"💾 **Size:** {downloaded_mb:.2f} MB / {total_mb:.2f} MB\n"
            f"⚡ **Speed:** {speed_mb:.2f} MB/s\n"
            f"🔄 **Status:** Downloading..."
        )
        
        await message.edit_text(text)
        
    except Exception as e:
        logger.error(f"Progress update error: {e}")

@Client.on_message(filters.private & filters.regex(r'^magnet:|\.torrent$'))
async def handle_torrent(client: Client, message: Message):
    """Handle torrent/magnet links"""
    await AddUser(client, message)

    url = message.text.strip()

    if not is_torrent_link(url):
        return

    if not LIBTORRENT_AVAILABLE:
        await message.reply_text(
            "⚠️ **Torrent support is not available on this deployment.**\n\n"
            "libtorrent is missing in this environment (common on Koyeb/Heroku "
            "free tiers). Torrent/magnet downloads won't work here, but direct "
            "HTTP, YouTube, Terabox, xHamster sab fast kaam karenge."
        )
        return
    
    # Send initial message
    progress_msg = await message.reply_text(
        "🔄 **Processing Torrent**\n\n"
        "Fetching metadata...\n"
        "⏳ Please wait..."
    )
    
    # Create download directory
    download_path = os.path.join(BIMBO_DOWNLOAD_LOCATION, str(message.from_user.id))
    os.makedirs(download_path, exist_ok=True)
    
    # Download torrent
    result = await download_torrent(
        url=url,
        download_path=download_path,
        progress_callback=lambda **kwargs: torrent_progress_update(progress_msg, **kwargs)
    )
    
    if not result['success']:
        await progress_msg.edit_text(
            f"❌ **Torrent Download Failed**\n\n"
            f"Error: `{result['error']}`"
        )
        return
    
    # Update message
    await progress_msg.edit_text(
        f"✅ **Download Complete**\n\n"
        f"📁 **File:** `{result['file_name']}`\n"
        f"💾 **Size:** {result['size'] / (1024*1024):.2f} MB\n\n"
        f"📤 Uploading to Telegram..."
    )
    
    # Upload to Telegram
    try:
        file_path = result['file_path']
        
        # Check if it's a directory or file
        if os.path.isdir(file_path):
            # Upload all files in directory
            for root, dirs, files in os.walk(file_path):
                for file in files:
                    file_full_path = os.path.join(root, file)
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=file_full_path,
                        caption=f"📁 {file}\n\n✅ Downloaded by BIMBO Bot"
                    )
        else:
            # Upload single file
            await client.send_document(
                chat_id=message.chat.id,
                document=file_path,
                caption=f"📁 {result['file_name']}\n\n✅ Downloaded by BIMBO Bot"
            )
        
        # Delete progress message
        await progress_msg.delete()
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await progress_msg.edit_text(
            f"❌ **Upload Failed**\n\n"
            f"Downloaded but upload failed: `{str(e)}`"
        )
    
    finally:
        # Cleanup
        try:
            import shutil
            if os.path.exists(download_path):
                shutil.rmtree(download_path)
        except:
            pass
