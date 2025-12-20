"""
TrackUI - Social Media Archiver & Tracker PWA
Main Flask Application
"""

import os
import json
import sqlite3
import subprocess
import threading
import time
import shutil
import zipfile
import re
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from io import BytesIO

from flask import (
    Flask, render_template, request, jsonify, send_file,
    send_from_directory, redirect, url_for, Response, session
)
import hashlib

# Optional Telegram Bot
try:
    import telebot
    TELEBOT_AVAILABLE = True
except ImportError:
    TELEBOT_AVAILABLE = False

# =============================================================================
# Configuration
# =============================================================================

BASE_DIR = Path(__file__).parent.absolute()
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "trackui.db"
DOWNLOADS_DIR = DATA_DIR / "downloads"
COOKIES_DIR = DATA_DIR / "cookies"
AVATARS_DIR = DATA_DIR / "avatars"

# Avatar download settings
TIMEOUT_THRESHOLD = 60
RATELIMIT_BYPASS = True
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]
user_agent_index = 0

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)
COOKIES_DIR.mkdir(exist_ok=True)
AVATARS_DIR.mkdir(exist_ok=True)
(COOKIES_DIR / "instagram").mkdir(exist_ok=True)

# Flask App
app = Flask(__name__)
app.secret_key = os.urandom(24)

# =============================================================================
# Global State
# =============================================================================

# Download Queue: list of dicts with keys:
# id, username, platform, status (queued/active/completed/failed/paused), 
# progress, message, process, started_at, completed_at
download_queue = []
queue_lock = threading.Lock()
queue_id_counter = 0

# Scheduler state
scheduler_thread = None
scheduler_running = False

# Telegram Bot
telegram_bot = None
telegram_thread = None

# =============================================================================
# Authentication
# =============================================================================

def login_required(f):
    """Decorator to require login for protected routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if password protection is enabled
        password_hash = get_setting('app_password_hash', '')
        if password_hash and not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def hash_password(password):
    """Hash password with SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, stored_hash):
    """Verify password against stored hash"""
    return hash_password(password) == stored_hash

# =============================================================================
# Database Functions
# =============================================================================

def get_db():
    """Get database connection with row factory"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Initialize database schema"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            platform TEXT NOT NULL,
            display_name TEXT,
            profile_picture TEXT,
            stats_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_sync TIMESTAMP,
            UNIQUE(username, platform)
        )
    ''')
    
    # Tags table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL DEFAULT '#3b82f6'
        )
    ''')
    
    # User-Tags relationship
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_tags (
            user_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (user_id, tag_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    ''')
    
    # Settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Likes/Favorites table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_filename TEXT NOT NULL UNIQUE,
            liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default settings
    default_settings = {
        'scheduler_enabled': 'false',
        'scheduler_time': '03:00',
        'scheduler_interval': 'daily',
        'max_concurrent_downloads': '2',
        'telegram_bot_token': '',
        'telegram_chat_id': '',
        'default_instagram_cookie': ''
    }
    
    for key, value in default_settings.items():
        cursor.execute('''
            INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
        ''', (key, value))
    
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    """Get a setting value"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key, value):
    """Set a setting value"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
    ''', (key, str(value)))
    conn.commit()
    conn.close()

# =============================================================================
# Download Engine
# =============================================================================

def get_next_queue_id():
    """Get next unique queue ID"""
    global queue_id_counter
    with queue_lock:
        queue_id_counter += 1
        return queue_id_counter

def add_to_queue(username, platform, url=None, folder=None):
    """Add a download job to the queue"""
    job = {
        'id': get_next_queue_id(),
        'username': username,
        'platform': platform,
        'url': url,
        'folder': folder,
        'status': 'queued',
        'progress': 0,
        'message': 'Waiting in queue...',
        'process': None,
        'started_at': None,
        'completed_at': None,
        'files_downloaded': 0
    }
    
    with queue_lock:
        download_queue.append(job)
    
    # Start queue processor if not running
    threading.Thread(target=process_queue, daemon=True).start()
    
    return job['id']

def process_queue():
    """Process download queue"""
    max_concurrent = int(get_setting('max_concurrent_downloads', '2'))
    
    while True:
        with queue_lock:
            # Count active downloads
            active_count = sum(1 for j in download_queue if j['status'] == 'active')
            
            # Find next queued job
            next_job = None
            for job in download_queue:
                if job['status'] == 'queued':
                    next_job = job
                    break
            
            if active_count >= max_concurrent or next_job is None:
                # Check if any jobs still queued
                queued_count = sum(1 for j in download_queue if j['status'] == 'queued')
                if queued_count == 0:
                    return
                time.sleep(1)
                continue
            
            next_job['status'] = 'active'
            next_job['started_at'] = datetime.now().isoformat()
            next_job['message'] = 'Starting download...'
        
        # Run download in separate thread
        threading.Thread(
            target=run_download,
            args=(next_job,),
            daemon=True
        ).start()
        
        time.sleep(0.5)

def run_download(job):
    """Execute gallery-dl download for a job"""
    username = job['username']
    platform = job['platform']
    
    # Build output directory
    if job.get('folder'):
        output_dir = DOWNLOADS_DIR / job['folder']
    else:
        output_dir = DOWNLOADS_DIR / platform / username
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Build gallery-dl command
    cmd = ['gallery-dl']
    
    # Archive file to skip duplicates
    archive_file = output_dir / '.archive.txt'
    cmd.extend(['--download-archive', str(archive_file)])
    
    # Output directory
    cmd.extend(['-d', str(DOWNLOADS_DIR)])
    
    # Platform-specific options
    if platform == 'instagram':
        cookie_file = get_default_instagram_cookie()
        if cookie_file:
            cmd.extend(['--cookies', cookie_file])
        
        # Use config file
        config_file = BASE_DIR / 'gallery-dl.conf'
        if config_file.exists():
            cmd.extend(['-c', str(config_file)])
        
        # Explicitly include all content types including highlights
        cmd.extend(['-o', 'extractor.instagram.include=posts,stories,highlights,reels'])
        
        # URL format
        if job.get('url'):
            url = job['url']
        else:
            url = f'https://www.instagram.com/{username}/'
    
    elif platform == 'tiktok':
        if job.get('url'):
            url = job['url']
        else:
            url = f'https://www.tiktok.com/@{username}'
    
    elif platform == 'coomer':
        if job.get('url'):
            # Replace old domain with new one
            url = job['url'].replace('coomer.su', 'coomer.st')
        else:
            # Parse service/username format (e.g., "onlyfans/creatorname")
            if '/' in username:
                service, actual_username = username.split('/', 1)
            else:
                service = 'onlyfans'  # Default service
                actual_username = username
            
            # Build proper Coomer URL: https://coomer.st/{service}/user/{username}
            url = f'https://coomer.st/{service}/user/{actual_username}'
            job['message'] = f'Downloading from {service.upper()}...'
    
    else:
        # Generic URL download
        url = job.get('url', '')
    
    cmd.append(url)
    
    # Add verbose output for progress parsing
    cmd.extend(['-v'])
    
    try:
        job['message'] = 'Initializing gallery-dl...'
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(BASE_DIR)
        )
        
        job['process'] = process
        files_count = 0
        last_activity = time.time()
        timeout_minutes = 10
        
        for line in iter(process.stdout.readline, ''):
            if job['status'] == 'paused':
                process.terminate()
                job['message'] = 'Download paused'
                return
            
            last_activity = time.time()
            line = line.strip()
            
            # Parse progress from gallery-dl output
            if line:
                # Check for file download
                if 'Downloading' in line or '.jpg' in line or '.mp4' in line or '.png' in line:
                    files_count += 1
                    job['files_downloaded'] = files_count
                    job['message'] = f'Downloaded {files_count} files...'
                elif 'Skipping' in line:
                    job['message'] = line[:80]
                elif 'error' in line.lower() or 'Error' in line:
                    job['message'] = f'Error: {line[:60]}'
                else:
                    # Show last meaningful line
                    if len(line) > 10:
                        job['message'] = line[:80]
            
            # Check timeout
            if time.time() - last_activity > timeout_minutes * 60:
                process.terminate()
                job['status'] = 'failed'
                job['message'] = f'Timeout after {timeout_minutes} minutes of inactivity'
                job['completed_at'] = datetime.now().isoformat()
                return
        
        process.wait()
        
        if process.returncode == 0:
            job['status'] = 'completed'
            job['message'] = f'Completed! Downloaded {files_count} files'
            
            # Update user's last_sync
            update_user_last_sync(username, platform)
        else:
            job['status'] = 'failed'
            job['message'] = f'Failed with exit code {process.returncode}'
        
    except Exception as e:
        job['status'] = 'failed'
        job['message'] = f'Error: {str(e)}'
    
    finally:
        job['completed_at'] = datetime.now().isoformat()
        job['process'] = None
        
        # Send Telegram notification if enabled
        emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
        
        # Clean display name for Coomer
        display_username = username
        if platform == 'coomer' and '/' in username:
            display_username = username.split('/')[1]
        
        if job['status'] == 'completed':
            if files_count > 0:
                # New content downloaded!
                send_telegram_notification(
                    f"✅ *{emoji} {display_username}*\n"
                    f"📥 Downloaded {files_count} new files!"
                )
            else:
                send_telegram_notification(
                    f"✅ {emoji} {display_username}: Up to date (no new content)"
                )
        else:
            send_telegram_notification(
                f"❌ {emoji} {display_username}: {job['message']}"
            )

def update_user_last_sync(username, platform):
    """Update user's last_sync timestamp"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET last_sync = CURRENT_TIMESTAMP
        WHERE username = ? AND platform = ?
    ''', (username, platform))
    conn.commit()
    conn.close()

def get_default_instagram_cookie():
    """Get path to default Instagram cookie file"""
    default_cookie = get_setting('default_instagram_cookie', '')
    if default_cookie:
        cookie_path = COOKIES_DIR / 'instagram' / default_cookie
        if cookie_path.exists():
            return str(cookie_path)
    
    # Fallback: find first cookie file
    cookie_dir = COOKIES_DIR / 'instagram'
    for f in cookie_dir.glob('*.txt'):
        return str(f)
    
    return None

# =============================================================================
# Avatar Download
# =============================================================================

def download_avatar_with_gallery_dl(username, platform='instagram'):
    """Download user's avatar using gallery-dl directly."""
    global user_agent_index
    
    try:
        AVATARS_DIR.mkdir(exist_ok=True)
        
        # Check if avatar already exists
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
            existing = AVATARS_DIR / f"{platform}_{username}{ext}"
            if existing.exists():
                return str(existing)
        
        # Set correct URL based on platform
        if platform == 'instagram':
            url = f"https://www.instagram.com/{username}/avatar/"
        elif platform == 'tiktok':
            url = f"https://www.tiktok.com/@{username}"
        else:
            # Coomer doesn't have a standard avatar endpoint
            return None
        
        # Use gallery-dl to get avatar information
        cmd = ['gallery-dl', '--dump-json', '--no-download']
        
        # Add Instagram cookies if available
        if platform == 'instagram':
            cookie_file = get_default_instagram_cookie()
            if cookie_file:
                cmd.extend(['--cookies', cookie_file])
        
        # Add rate limiting bypass
        if RATELIMIT_BYPASS:
            user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
            cmd.extend(['--option', f'extractor.user-agent={user_agent}'])
            user_agent_index += 1
        
        cmd.append(url)
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_THRESHOLD)
        
        if result.returncode != 0:
            print(f"Failed to get avatar info for {username} ({platform}): {result.stderr}")
            return None
        
        if not result.stdout.strip():
            print(f"No output from gallery-dl for {username}")
            return None
        
        # Parse JSON to find avatar URL
        avatar_url = None
        out = result.stdout.strip()
        
        try:
            # Try parsing as single JSON first
            try:
                data = json.loads(out)
                if isinstance(data, list):
                    metadata_list = data
                else:
                    metadata_list = [data]
            except json.JSONDecodeError:
                # Parse line by line
                metadata_list = []
                for line in out.split('\n'):
                    line = line.strip()
                    if line:
                        try:
                            metadata_list.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            
            # Find avatar URL in metadata
            for item in metadata_list:
                metadata_dict = None
                
                if isinstance(item, list) and len(item) >= 2:
                    # Handle gallery-dl's array format [type, data, metadata]
                    if platform == 'instagram' and len(item) >= 3:
                        if isinstance(item[1], str) and item[1].startswith('http'):
                            avatar_url = item[1]
                            break
                        if isinstance(item[2], dict):
                            metadata_dict = item[2]
                    elif len(item) >= 3 and isinstance(item[2], dict):
                        metadata_dict = item[2]
                    elif isinstance(item[1], dict):
                        metadata_dict = item[1]
                    else:
                        continue
                elif isinstance(item, dict):
                    metadata_dict = item
                else:
                    continue
                
                if avatar_url:
                    break
                
                # Look for avatar URLs in metadata fields
                if metadata_dict:
                    if platform == 'instagram':
                        avatar_url = (metadata_dict.get('display_url') or
                                    metadata_dict.get('uploader_profile_image') or
                                    metadata_dict.get('uploader_avatar') or
                                    metadata_dict.get('avatar_url') or
                                    metadata_dict.get('profile_pic_url') or
                                    metadata_dict.get('profile_pic_url_hd') or
                                    metadata_dict.get('avatar'))
                        
                        if not avatar_url:
                            for nested_key in ['user', 'owner', 'uploader_info']:
                                if nested_key in metadata_dict and isinstance(metadata_dict[nested_key], dict):
                                    nested_data = metadata_dict[nested_key]
                                    avatar_url = (nested_data.get('profile_pic_url_hd') or
                                                nested_data.get('profile_pic_url') or
                                                nested_data.get('avatar') or
                                                nested_data.get('profile_picture'))
                                    if avatar_url:
                                        break
                    else:
                        # TikTok
                        avatar_url = (metadata_dict.get('avatarLarger') or
                                    metadata_dict.get('avatarMedium') or
                                    metadata_dict.get('avatarThumb') or
                                    metadata_dict.get('uploader_avatar') or
                                    metadata_dict.get('avatar_url') or
                                    metadata_dict.get('avatar'))
                        
                        if not avatar_url and 'author' in metadata_dict:
                            author = metadata_dict['author']
                            if isinstance(author, dict):
                                avatar_url = (author.get('avatarLarger') or
                                            author.get('avatarMedium') or
                                            author.get('avatarThumb') or
                                            author.get('avatar'))
                    
                    if avatar_url:
                        break
            
            if not avatar_url:
                print(f"No avatar URL found for {username}")
                return None
            
            # Download the avatar
            import urllib.request
            import urllib.parse
            
            # Determine file extension
            parsed_url = urllib.parse.urlparse(avatar_url)
            ext = '.jpg'
            if parsed_url.path:
                path_ext = os.path.splitext(parsed_url.path)[1].lower()
                if path_ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                    ext = path_ext
            
            local_path = AVATARS_DIR / f"{platform}_{username}{ext}"
            
            # Download with user agent
            user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
            req = urllib.request.Request(avatar_url, headers={'User-Agent': user_agent})
            with urllib.request.urlopen(req, timeout=60) as response:
                with open(local_path, 'wb') as f:
                    f.write(response.read())
            
            print(f"Avatar cached for {username}: {local_path}")
            return str(local_path)
            
        except Exception as e:
            print(f"Error parsing avatar data for {username}: {e}")
            return None
        
    except subprocess.TimeoutExpired:
        print(f"Avatar download timeout for {username}")
        return None
    except Exception as e:
        print(f"Error downloading avatar for {username}: {e}")
        return None

def get_avatar_url(username, platform):
    """Get avatar URL for a user - returns local path if cached, or downloads it."""
    # Check for existing avatar
    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
        avatar_path = AVATARS_DIR / f"{platform}_{username}{ext}"
        if avatar_path.exists():
            return f'/avatars/{platform}_{username}{ext}'
    
    # Try to download avatar in background
    def download_bg():
        download_avatar_with_gallery_dl(username, platform)
    
    threading.Thread(target=download_bg, daemon=True).start()
    
    return None

# =============================================================================
# Scheduler
# =============================================================================

def start_scheduler():
    """Start the background scheduler"""
    global scheduler_thread, scheduler_running
    
    if scheduler_thread and scheduler_thread.is_alive():
        return
    
    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

def stop_scheduler():
    """Stop the scheduler"""
    global scheduler_running
    scheduler_running = False

def scheduler_loop():
    """Main scheduler loop"""
    global scheduler_running
    
    while scheduler_running:
        try:
            if get_setting('scheduler_enabled', 'false') == 'true':
                schedule_time = get_setting('scheduler_time', '03:00')
                interval = get_setting('scheduler_interval', 'daily')
                
                now = datetime.now()
                target_hour, target_minute = map(int, schedule_time.split(':'))
                
                # Check if it's time to run
                if now.hour == target_hour and now.minute == target_minute:
                    # Check interval
                    should_run = False
                    
                    if interval == 'daily':
                        should_run = True
                    elif interval == 'weekly' and now.weekday() == 0:  # Monday
                        should_run = True
                    
                    if should_run:
                        sync_all_users()
                        # Sleep for 61 seconds to avoid re-triggering
                        time.sleep(61)
                        continue
            
            # Check every 30 seconds
            time.sleep(30)
            
        except Exception as e:
            print(f"Scheduler error: {e}")
            time.sleep(60)

def sync_all_users():
    """Sync all tracked users"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT username, platform FROM users')
    users = cursor.fetchall()
    conn.close()
    
    send_telegram_notification(f"🔄 Starting bulk sync for {len(users)} users...")
    
    for user in users:
        add_to_queue(user['username'], user['platform'])
    
    return len(users)

# =============================================================================
# Telegram Bot
# =============================================================================

def init_telegram_bot():
    """Initialize Telegram bot"""
    global telegram_bot, telegram_thread
    
    if not TELEBOT_AVAILABLE:
        return False
    
    token = get_setting('telegram_bot_token', '')
    if not token:
        return False
    
    try:
        telegram_bot = telebot.TeleBot(token, threaded=True)
        
        # Import inline keyboard types
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        def get_main_menu():
            """Create main menu inline keyboard"""
            markup = InlineKeyboardMarkup(row_width=2)
            markup.row(
                InlineKeyboardButton("👥 List Users", callback_data="menu_list"),
                InlineKeyboardButton("📊 Stats", callback_data="menu_stats")
            )
            markup.row(
                InlineKeyboardButton("📥 Queue Status", callback_data="menu_status"),
                InlineKeyboardButton("🔄 Sync All", callback_data="menu_sync_all")
            )
            markup.row(
                InlineKeyboardButton("🔍 Sync User", callback_data="menu_sync_user")
            )
            return markup
        
        @telegram_bot.message_handler(commands=['start', 'menu'])
        def cmd_start(message):
            telegram_bot.send_message(
                message.chat.id,
                "🎬 *TrackUI Bot*\n\n"
                "Select an option from the menu below:",
                reply_markup=get_main_menu(),
                parse_mode='Markdown'
            )
        
        @telegram_bot.callback_query_handler(func=lambda call: call.data.startswith('menu_'))
        def callback_menu(call):
            """Handle main menu button clicks"""
            action = call.data.replace('menu_', '')
            chat_id = call.message.chat.id
            
            telegram_bot.answer_callback_query(call.id)
            
            if action == 'list':
                # Show user list with inline keyboard
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, username, platform, display_name FROM users
                    ORDER BY platform, username
                ''')
                users = cursor.fetchall()
                conn.close()
                
                if not users:
                    telegram_bot.send_message(chat_id, "📭 No users tracked yet")
                    return
                
                markup = InlineKeyboardMarkup(row_width=2)
                buttons = []
                for user in users:
                    platform = user['platform']
                    emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
                    display = user['username']
                    if platform == 'coomer' and '/' in display:
                        display = display.split('/')[1]
                    buttons.append(InlineKeyboardButton(f"{emoji} {display}", callback_data=f"user_{user['id']}"))
                
                for i in range(0, len(buttons), 2):
                    if i + 1 < len(buttons):
                        markup.row(buttons[i], buttons[i + 1])
                    else:
                        markup.row(buttons[i])
                
                markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_back"))
                
                telegram_bot.send_message(
                    chat_id,
                    f"📋 *Tracked Users ({len(users)})*\n\nTap a user to view their profile:",
                    reply_markup=markup,
                    parse_mode='Markdown'
                )
            
            elif action == 'stats':
                # Calculate and show stats
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('SELECT platform, COUNT(*) as count FROM users GROUP BY platform')
                platform_counts = cursor.fetchall()
                cursor.execute('SELECT COUNT(*) FROM users')
                total_users = cursor.fetchone()[0]
                conn.close()
                
                total_files = 0
                total_size = 0
                if DOWNLOADS_DIR.exists():
                    for root, dirs, files in os.walk(DOWNLOADS_DIR):
                        for f in files:
                            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm', '.mov')):
                                total_files += 1
                                try:
                                    total_size += (Path(root) / f).stat().st_size
                                except:
                                    pass
                
                if total_size >= 1024 * 1024 * 1024:
                    size_str = f"{total_size / (1024*1024*1024):.2f} GB"
                elif total_size >= 1024 * 1024:
                    size_str = f"{total_size / (1024*1024):.2f} MB"
                else:
                    size_str = f"{total_size / 1024:.2f} KB"
                
                stats_text = "📊 *TrackUI Statistics*\n\n"
                stats_text += f"👥 *Total Users:* {total_users}\n"
                for p in platform_counts:
                    emoji = '📸' if p['platform'] == 'instagram' else ('🎵' if p['platform'] == 'tiktok' else '💖')
                    stats_text += f"  {emoji} {p['platform'].title()}: {p['count']}\n"
                stats_text += f"\n📁 *Total Files:* {total_files:,}\n"
                stats_text += f"💾 *Storage Used:* {size_str}"
                
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_back"))
                
                telegram_bot.send_message(chat_id, stats_text, reply_markup=markup, parse_mode='Markdown')
            
            elif action == 'status':
                # Show queue status
                with queue_lock:
                    active = [j for j in download_queue if j['status'] == 'active']
                    queued = [j for j in download_queue if j['status'] == 'queued']
                
                status_text = "📥 *Download Queue*\n\n"
                status_text += f"🔄 Active: {len(active)}\n"
                status_text += f"⏳ Queued: {len(queued)}\n"
                
                if active:
                    status_text += "\n*Active Downloads:*\n"
                    for job in active:
                        display = job['username']
                        if job['platform'] == 'coomer' and '/' in display:
                            display = display.split('/')[1]
                        status_text += f"• {display}: {job['message'][:30]}\n"
                
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_back"))
                
                telegram_bot.send_message(chat_id, status_text, reply_markup=markup, parse_mode='Markdown')
            
            elif action == 'sync_all':
                count = sync_all_users()
                telegram_bot.send_message(chat_id, f"🔄 Started sync for {count} users")
            
            elif action == 'sync_user':
                # Show user list for sync selection
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('SELECT id, username, platform FROM users ORDER BY platform, username')
                users = cursor.fetchall()
                conn.close()
                
                if not users:
                    telegram_bot.send_message(chat_id, "📭 No users to sync")
                    return
                
                markup = InlineKeyboardMarkup(row_width=2)
                buttons = []
                for user in users:
                    platform = user['platform']
                    emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
                    display = user['username']
                    if platform == 'coomer' and '/' in display:
                        display = display.split('/')[1]
                    buttons.append(InlineKeyboardButton(f"{emoji} {display}", callback_data=f"sync_{user['id']}"))
                
                for i in range(0, len(buttons), 2):
                    if i + 1 < len(buttons):
                        markup.row(buttons[i], buttons[i + 1])
                    else:
                        markup.row(buttons[i])
                
                markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_back"))
                
                telegram_bot.send_message(
                    chat_id,
                    "🔍 *Select User to Sync*\n\nTap a user to start syncing:",
                    reply_markup=markup,
                    parse_mode='Markdown'
                )
            
            elif action == 'back':
                # Return to main menu
                telegram_bot.send_message(
                    chat_id,
                    "🎬 *TrackUI Bot*\n\nSelect an option:",
                    reply_markup=get_main_menu(),
                    parse_mode='Markdown'
                )
        
        @telegram_bot.callback_query_handler(func=lambda call: call.data.startswith('sync_'))
        def callback_sync_user(call):
            """Handle sync user button clicks"""
            user_id = int(call.data.replace('sync_', ''))
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('SELECT username, platform FROM users WHERE id = ?', (user_id,))
            user = cursor.fetchone()
            conn.close()
            
            if not user:
                telegram_bot.answer_callback_query(call.id, "User not found")
                return
            
            username = user['username']
            platform = user['platform']
            
            # Queue sync
            job = {
                'id': f"{platform}_{username}_{int(time.time())}",
                'username': username,
                'platform': platform,
                'status': 'queued',
                'message': 'Waiting to start...',
                'files_downloaded': 0,
                'started_at': None,
                'completed_at': None,
                'process': None,
                'folder': None,
                'url': None
            }
            
            with queue_lock:
                download_queue.append(job)
            
            display = username
            if platform == 'coomer' and '/' in username:
                display = username.split('/')[1]
            
            emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
            telegram_bot.answer_callback_query(call.id, f"🔄 Syncing {display}...", show_alert=False)
            telegram_bot.send_message(call.message.chat.id, f"🔄 Started sync for {emoji} *{display}*", parse_mode='Markdown')
        
        @telegram_bot.message_handler(commands=['status'])
        def cmd_status(message):
            with queue_lock:
                active = [j for j in download_queue if j['status'] == 'active']
                queued = [j for j in download_queue if j['status'] == 'queued']
                completed = [j for j in download_queue if j['status'] == 'completed'][-5:]
            
            status_text = f"📊 *Queue Status*\n\n"
            status_text += f"🔄 Active: {len(active)}\n"
            status_text += f"⏳ Queued: {len(queued)}\n"
            status_text += f"✅ Completed: {len(completed)}\n\n"
            
            if active:
                status_text += "*Active Downloads:*\n"
                for job in active:
                    status_text += f"• {job['platform']}/{job['username']}: {job['message'][:30]}\n"
            
            telegram_bot.reply_to(message, status_text, parse_mode='Markdown')
        
        @telegram_bot.message_handler(commands=['add'])
        def cmd_add(message):
            parts = message.text.split()
            if len(parts) < 3:
                telegram_bot.reply_to(message, "Usage: /add <username> <platform>")
                return
            
            username = parts[1]
            platform = parts[2].lower()
            
            if platform not in ['instagram', 'tiktok', 'coomer']:
                telegram_bot.reply_to(message, "Platform must be: instagram, tiktok, or coomer")
                return
            
            conn = get_db()
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO users (username, platform) VALUES (?, ?)
                ''', (username, platform))
                conn.commit()
                telegram_bot.reply_to(message, f"✅ Added {platform}/{username}")
            except sqlite3.IntegrityError:
                telegram_bot.reply_to(message, f"⚠️ User already exists")
            finally:
                conn.close()
        
        @telegram_bot.message_handler(commands=['delete'])
        def cmd_delete(message):
            parts = message.text.split()
            if len(parts) < 2:
                telegram_bot.reply_to(message, "Usage: /delete <username>")
                return
            
            username = parts[1]
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM users WHERE username = ?', (username,))
            if cursor.rowcount > 0:
                telegram_bot.reply_to(message, f"✅ Deleted {username}")
            else:
                telegram_bot.reply_to(message, f"⚠️ User not found")
            conn.commit()
            conn.close()
        
        @telegram_bot.message_handler(commands=['search'])
        def cmd_search(message):
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                telegram_bot.reply_to(message, "Usage: /search <query>")
                return
            
            query = f"%{parts[1]}%"
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT username, platform FROM users 
                WHERE username LIKE ? OR display_name LIKE ?
                LIMIT 10
            ''', (query, query))
            users = cursor.fetchall()
            conn.close()
            
            if users:
                result = "🔍 *Search Results:*\n\n"
                for user in users:
                    result += f"• {user['platform']}/{user['username']}\n"
                telegram_bot.reply_to(message, result, parse_mode='Markdown')
            else:
                telegram_bot.reply_to(message, "No users found")
        
        @telegram_bot.message_handler(commands=['stats'])
        def cmd_stats(message):
            """Show statistics about tracked users and downloads"""
            conn = get_db()
            cursor = conn.cursor()
            
            # Get user counts by platform
            cursor.execute('''
                SELECT platform, COUNT(*) as count FROM users 
                GROUP BY platform
            ''')
            platform_counts = cursor.fetchall()
            
            # Get total users
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            
            conn.close()
            
            # Count files and calculate storage
            total_files = 0
            total_size = 0
            
            if DOWNLOADS_DIR.exists():
                for root, dirs, files in os.walk(DOWNLOADS_DIR):
                    for f in files:
                        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm', '.mov')):
                            total_files += 1
                            try:
                                total_size += (Path(root) / f).stat().st_size
                            except:
                                pass
            
            # Format size
            if total_size >= 1024 * 1024 * 1024:
                size_str = f"{total_size / (1024*1024*1024):.2f} GB"
            elif total_size >= 1024 * 1024:
                size_str = f"{total_size / (1024*1024):.2f} MB"
            else:
                size_str = f"{total_size / 1024:.2f} KB"
            
            # Build message
            stats_text = "📊 *TrackUI Statistics*\n\n"
            stats_text += f"👥 *Total Users:* {total_users}\n"
            
            for p in platform_counts:
                emoji = '📸' if p['platform'] == 'instagram' else ('🎵' if p['platform'] == 'tiktok' else '💖')
                stats_text += f"  {emoji} {p['platform'].title()}: {p['count']}\n"
            
            stats_text += f"\n📁 *Total Files:* {total_files:,}\n"
            stats_text += f"💾 *Storage Used:* {size_str}\n"
            
            # Queue status
            with queue_lock:
                active = len([j for j in download_queue if j['status'] == 'active'])
                queued = len([j for j in download_queue if j['status'] == 'queued'])
            
            stats_text += f"\n📥 *Download Queue:*\n"
            stats_text += f"  🔄 Active: {active}\n"
            stats_text += f"  ⏳ Queued: {queued}"
            
            telegram_bot.reply_to(message, stats_text, parse_mode='Markdown')
        
        @telegram_bot.message_handler(commands=['syncuser'])
        def cmd_syncuser(message):
            """Sync a specific user by username"""
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                telegram_bot.reply_to(message, "Usage: /syncuser <username>")
                return
            
            query = parts[1].strip().lower()
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, platform FROM users 
                WHERE LOWER(username) LIKE ? OR LOWER(display_name) LIKE ?
                LIMIT 1
            ''', (f'%{query}%', f'%{query}%'))
            user = cursor.fetchone()
            conn.close()
            
            if not user:
                telegram_bot.reply_to(message, f"❌ No user found matching '{query}'")
                return
            
            # Queue sync for this user
            username = user['username']
            platform = user['platform']
            
            job = {
                'id': f"{platform}_{username}_{int(time.time())}",
                'username': username,
                'platform': platform,
                'status': 'queued',
                'message': 'Waiting to start...',
                'files_downloaded': 0,
                'started_at': None,
                'completed_at': None,
                'process': None,
                'folder': None,
                'url': None
            }
            
            with queue_lock:
                download_queue.append(job)
            
            # Clean display name for Coomer
            display = username
            if platform == 'coomer' and '/' in username:
                display = username.split('/')[1]
            
            emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
            telegram_bot.reply_to(message, f"🔄 Started sync for {emoji} {display}")
        
        @telegram_bot.message_handler(commands=['sync'])
        def cmd_sync(message):
            count = sync_all_users()
            telegram_bot.reply_to(message, f"🔄 Started sync for {count} users")
        
        @telegram_bot.message_handler(commands=['list'])
        def cmd_list(message):
            """List all tracked users with inline keyboard to view profile pics"""
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, platform, display_name FROM users
                ORDER BY platform, username
            ''')
            users = cursor.fetchall()
            conn.close()
            
            if not users:
                telegram_bot.reply_to(message, "📭 No users tracked yet")
                return
            
            # Create inline keyboard with user buttons
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup = InlineKeyboardMarkup(row_width=2)
            
            # Group by platform
            platforms = {}
            for user in users:
                platform = user['platform']
                if platform not in platforms:
                    platforms[platform] = []
                platforms[platform].append(user)
            
            # Add buttons for each user
            buttons = []
            for platform, platform_users in platforms.items():
                emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
                for user in platform_users:
                    # For coomer, show clean username
                    display = user['username']
                    if platform == 'coomer' and '/' in display:
                        display = display.split('/')[1]
                    
                    btn_text = f"{emoji} {display}"
                    callback_data = f"user_{user['id']}"
                    buttons.append(InlineKeyboardButton(btn_text, callback_data=callback_data))
            
            # Add buttons in rows of 2
            for i in range(0, len(buttons), 2):
                if i + 1 < len(buttons):
                    markup.row(buttons[i], buttons[i + 1])
                else:
                    markup.row(buttons[i])
            
            telegram_bot.reply_to(
                message,
                f"📋 *Tracked Users ({len(users)})*\n\nSelect a user to view their profile picture:",
                reply_markup=markup,
                parse_mode='Markdown'
            )
        
        @telegram_bot.callback_query_handler(func=lambda call: call.data.startswith('user_'))
        def callback_user_profile(call):
            """Handle user selection from /list - show profile picture"""
            user_id = int(call.data.replace('user_', ''))
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT username, platform, display_name, profile_picture FROM users
                WHERE id = ?
            ''', (user_id,))
            user = cursor.fetchone()
            conn.close()
            
            if not user:
                telegram_bot.answer_callback_query(call.id, "User not found")
                return
            
            username = user['username']
            platform = user['platform']
            display_name = user['display_name'] or username
            
            # For coomer, show clean username
            if platform == 'coomer' and '/' in username:
                clean_username = username.split('/')[1]
            else:
                clean_username = username
            
            # Try to get profile picture
            profile_pic = user['profile_picture']
            
            # If no stored profile pic, try to get avatar from cache
            if not profile_pic:
                avatar_url = get_avatar_url(username, platform)
                if avatar_url:
                    profile_pic = avatar_url
            
            emoji = '📸' if platform == 'instagram' else ('🎵' if platform == 'tiktok' else '💖')
            caption = f"{emoji} *{display_name}*\n@{clean_username}\nPlatform: {platform.title()}"
            
            # Find local profile picture - check multiple sources
            local_pic = None
            
            # 1. First check if there's a cached avatar in AVATARS_DIR
            for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                avatar_path = AVATARS_DIR / f"{platform}_{username}{ext}"
                if avatar_path.exists():
                    local_pic = avatar_path
                    break
            
            # 2. If no cached avatar, look for any image in the media folder
            if not local_pic:
                media_dir = DOWNLOADS_DIR / platform / username
                if media_dir.exists():
                    # Look for first image in posts
                    for root, dirs, files in os.walk(media_dir):
                        for f in files:
                            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                                local_pic = Path(root) / f
                                break
                        if local_pic:
                            break
            
            try:
                if local_pic and local_pic.exists():
                    # Send local file
                    with open(local_pic, 'rb') as photo:
                        telegram_bot.send_photo(
                            call.message.chat.id,
                            photo,
                            caption=caption,
                            parse_mode='Markdown'
                        )
                elif profile_pic and profile_pic.startswith('http'):
                    # Send from URL
                    telegram_bot.send_photo(
                        call.message.chat.id,
                        profile_pic,
                        caption=caption,
                        parse_mode='Markdown'
                    )
                elif profile_pic and profile_pic.startswith('/avatars/'):
                    # Local avatar path - read from file
                    avatar_file = BASE_DIR / 'data' / profile_pic.lstrip('/')
                    if avatar_file.exists():
                        with open(avatar_file, 'rb') as photo:
                            telegram_bot.send_photo(
                                call.message.chat.id,
                                photo,
                                caption=caption,
                                parse_mode='Markdown'
                            )
                    else:
                        telegram_bot.send_message(
                            call.message.chat.id,
                            f"{caption}\n\n📷 No profile picture available",
                            parse_mode='Markdown'
                        )
                else:
                    # No picture available
                    telegram_bot.send_message(
                        call.message.chat.id,
                        f"{caption}\n\n📷 No profile picture available",
                        parse_mode='Markdown'
                    )
                
                telegram_bot.answer_callback_query(call.id)
            except Exception as e:
                print(f"[Telegram] Error sending photo: {e}")
                telegram_bot.send_message(
                    call.message.chat.id,
                    f"{caption}\n\n⚠️ Could not load profile picture",
                    parse_mode='Markdown'
                )
                telegram_bot.answer_callback_query(call.id)
        
        # Start polling in background thread
        telegram_thread = threading.Thread(
            target=lambda: telegram_bot.infinity_polling(timeout=60),
            daemon=True
        )
        telegram_thread.start()
        
        return True
        
    except Exception as e:
        print(f"Telegram bot error: {e}")
        return False

def send_telegram_notification(message):
    """Send a Telegram notification"""
    if not telegram_bot:
        return
    
    chat_id = get_setting('telegram_chat_id', '')
    if not chat_id:
        return
    
    try:
        telegram_bot.send_message(chat_id, message)
    except Exception as e:
        print(f"Telegram send error: {e}")

# =============================================================================
# Flask Routes - Pages
# =============================================================================

@app.route('/')
@login_required
def index():
    """Main dashboard"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all users with their tags
    cursor.execute('''
        SELECT u.*, GROUP_CONCAT(t.id || ':' || t.name || ':' || t.color) as tags
        FROM users u
        LEFT JOIN user_tags ut ON u.id = ut.user_id
        LEFT JOIN tags t ON ut.tag_id = t.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    ''')
    users = [dict(row) for row in cursor.fetchall()]
    
    # Parse tags
    for user in users:
        if user['tags']:
            user['tags'] = [
                {'id': int(t.split(':')[0]), 'name': t.split(':')[1], 'color': t.split(':')[2]}
                for t in user['tags'].split(',')
            ]
        else:
            user['tags'] = []
        
        # Count actual downloaded files
        user_dir = DOWNLOADS_DIR / user['platform'] / user['username']
        posts_count = 0
        videos_count = 0
        total_files = 0
        total_size = 0
        
        if user_dir.exists():
            for root, dirs, files in os.walk(user_dir):
                for f in files:
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm', '.mov')):
                        total_files += 1
                        file_path = Path(root) / f
                        try:
                            total_size += file_path.stat().st_size
                        except:
                            pass
                        if f.lower().endswith(('.mp4', '.webm', '.mov')):
                            videos_count += 1
                        else:
                            posts_count += 1
        
        # Format size
        if total_size >= 1024 * 1024 * 1024:
            size_str = f"{total_size / (1024*1024*1024):.1f} GB"
        elif total_size >= 1024 * 1024:
            size_str = f"{total_size / (1024*1024):.1f} MB"
        elif total_size >= 1024:
            size_str = f"{total_size / 1024:.0f} KB"
        else:
            size_str = f"{total_size} B"
        
        user['stats'] = {
            'posts': posts_count,
            'videos': videos_count,
            'files': total_files,
            'size': size_str
        }
        
        # Get avatar URL if not set
        if not user.get('profile_picture'):
            user['profile_picture'] = get_avatar_url(user['username'], user['platform'])
    
    # Get all tags for filter
    cursor.execute('SELECT * FROM tags ORDER BY name')
    all_tags = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return render_template('index.html', users=users, tags=all_tags)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    password_hash = get_setting('app_password_hash', '')
    
    # If no password set, redirect to home
    if not password_hash:
        return redirect(url_for('index'))
    
    # Already logged in
    if session.get('authenticated'):
        return redirect(url_for('index'))
    
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if verify_password(password, password_hash):
            session['authenticated'] = True
            session.permanent = True
            return redirect(url_for('index'))
        else:
            error = 'Incorrect password'
    
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/browse')
@app.route('/browse/<path:subpath>')
@login_required
def browse_files(subpath=''):
    """Browse downloaded files folder structure"""
    # Security: prevent directory traversal
    if '..' in subpath:
        return "Invalid path", 400
    
    browse_dir = DOWNLOADS_DIR / subpath
    
    if not browse_dir.exists() or not browse_dir.is_dir():
        return render_template('browse.html', 
            breadcrumbs=[], folders=[], files=[], parent_path=None)
    
    # Build breadcrumbs
    breadcrumbs = []
    if subpath:
        parts = subpath.split('/')
        current_path = ''
        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            breadcrumbs.append({'name': part, 'path': current_path})
    
    # Get parent path
    parent_path = '/'.join(subpath.split('/')[:-1]) if subpath else None
    
    # List contents
    folders = []
    files = []
    
    for item in sorted(browse_dir.iterdir()):
        if item.name.startswith('.'):
            continue
            
        rel_path = f"{subpath}/{item.name}" if subpath else item.name
        
        if item.is_dir():
            # Count items in folder
            try:
                count = len([f for f in item.iterdir() if not f.name.startswith('.')])
            except:
                count = 0
            folders.append({
                'name': item.name,
                'path': rel_path,
                'count': count
            })
        else:
            # Get file info
            ext = item.suffix.lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                file_type = 'image'
            elif ext in ['.mp4', '.webm', '.mov', '.avi']:
                file_type = 'video'
            else:
                file_type = 'other'
            
            # Format size
            try:
                size = item.stat().st_size
                if size >= 1024 * 1024:
                    size_str = f"{size / (1024*1024):.1f} MB"
                elif size >= 1024:
                    size_str = f"{size / 1024:.0f} KB"
                else:
                    size_str = f"{size} B"
            except:
                size_str = "?"
            
            files.append({
                'name': item.name,
                'path': rel_path,
                'type': file_type,
                'size': size_str
            })
    
    return render_template('browse.html',
        breadcrumbs=breadcrumbs,
        folders=folders,
        files=files,
        parent_path=parent_path
    )

@app.route('/user/<platform>/<path:username>')
@login_required
def user_profile(platform, username):
    """User profile and media viewer"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Get user info
    cursor.execute('''
        SELECT u.*, GROUP_CONCAT(t.id || ':' || t.name || ':' || t.color) as tags
        FROM users u
        LEFT JOIN user_tags ut ON u.id = ut.user_id
        LEFT JOIN tags t ON ut.tag_id = t.id
        WHERE u.username = ? AND u.platform = ?
        GROUP BY u.id
    ''', (username, platform))
    user = cursor.fetchone()
    
    if not user:
        return "User not found", 404
    
    user = dict(user)
    
    # Parse tags
    if user['tags']:
        user['tags'] = [
            {'id': int(t.split(':')[0]), 'name': t.split(':')[1], 'color': t.split(':')[2]}
            for t in user['tags'].split(',')
        ]
    else:
        user['tags'] = []
    
    conn.close()
    
    # Get media files
    media_dir = DOWNLOADS_DIR / platform / username
    media = {'posts': [], 'stories': [], 'highlights': {}}
    
    if media_dir.exists():
        import urllib.parse
        
        # Collect all media files first (faster than os.walk with stat on each)
        all_files = []
        for root, dirs, files in os.walk(media_dir):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm', '.mov')):
                    all_files.append((root, f))
        
        # Process files (limit stat calls for performance)
        for root, f in all_files:
            file_path = Path(root) / f
            rel_path = Path(root).relative_to(media_dir)
            
            # URL-encode the filename and path
            encoded_filename = urllib.parse.quote(f, safe='')
            raw_path = Path(root).relative_to(DOWNLOADS_DIR).as_posix()
            encoded_path = '/'.join(urllib.parse.quote(part, safe='') for part in raw_path.split('/'))
            
            # Get modified time (single stat call, skip size for performance)
            try:
                modified = file_path.stat().st_mtime
            except:
                modified = 0
            
            file_info = {
                'filename': encoded_filename,
                'display_name': f,
                'path': encoded_path,
                'type': 'video' if f.lower().endswith(('.mp4', '.webm', '.mov')) else 'image',
                'modified': modified
            }
            
            # Categorize based on path
            path_str = str(rel_path).lower()
            if 'stories' in path_str:
                media['stories'].append(file_info)
            elif 'highlights' in path_str:
                parts = rel_path.parts
                if len(parts) >= 2:
                    highlight_name = parts[1] if parts[0] == 'highlights' else parts[0]
                else:
                    highlight_name = 'General'
                
                if highlight_name not in media['highlights']:
                    media['highlights'][highlight_name] = []
                media['highlights'][highlight_name].append(file_info)
            else:
                media['posts'].append(file_info)
        
        # Sort by modified date
        media['posts'].sort(key=lambda x: x['modified'], reverse=True)
        media['stories'].sort(key=lambda x: x['modified'], reverse=True)
        for key in media['highlights']:
            media['highlights'][key].sort(key=lambda x: x['filename'])
    
    # Auto-detect profile picture if not set  
    if not user.get('profile_picture'):
        # Try to get avatar via gallery-dl (cached or download in background)
        avatar_url = get_avatar_url(username, platform)
        if avatar_url:
            user['profile_picture'] = avatar_url
        # Fallback: use first post image as profile picture
        elif media['posts']:
            first_image = next((p for p in media['posts'] if p['type'] == 'image'), None)
            if first_image:
                user['profile_picture'] = f'/media/{first_image["path"]}/{first_image["filename"]}'
    
    # For Coomer, extract just the username (not service/username) for display
    if platform == 'coomer' and '/' in username:
        display_username = username.split('/', 1)[1]  # Get part after service/
    else:
        display_username = username
    
    return render_template('user.html', user=user, media=media, platform=platform, username=username, display_username=display_username)

@app.route('/api/users/random')
@login_required
def api_random_user():
    """Get a random user"""
    import random
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, platform, display_name FROM users')
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        return jsonify({'error': 'No users tracked yet'}), 404
    
    user = random.choice(users)
    return jsonify({
        'id': user['id'],
        'username': user['username'],
        'platform': user['platform'],
        'display_name': user['display_name']
    })

@app.route('/api/users/<int:user_id>/download-zip')
def api_download_user_zip(user_id):
    """Download all user's media as a ZIP file"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT username, platform FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    username = user['username']
    platform = user['platform']
    
    # Get user's media directory
    user_dir = DOWNLOADS_DIR / platform / username
    
    if not user_dir.exists():
        return jsonify({'error': 'No downloaded content found'}), 404
    
    # Create ZIP in memory
    memory_file = BytesIO()
    
    # Clean filename for ZIP
    clean_name = username.replace('/', '_') if '/' in username else username
    
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(user_dir):
            for file in files:
                file_path = Path(root) / file
                # Create relative path within ZIP
                arc_name = file_path.relative_to(user_dir)
                zf.write(file_path, arc_name)
    
    memory_file.seek(0)
    
    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{platform}_{clean_name}.zip'
    )

# =============================================================================
# Flask Routes - API
# =============================================================================

@app.route('/api/instagram/following')
def api_instagram_following():
    """Fetch Instagram following list for logged-in user using cookies"""
    import http.cookiejar
    import urllib.request
    import urllib.error
    
    # Get Instagram cookie file
    cookie_file = get_default_instagram_cookie()
    if not cookie_file:
        return jsonify({'error': 'No Instagram cookies found. Please upload cookies first.'}), 400
    
    print(f"[Following Import] Using cookie file: {cookie_file}")
    
    try:
        # Load cookies - try multiple methods
        cookie_jar = http.cookiejar.MozillaCookieJar()
        
        try:
            cookie_jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
        except Exception as cookie_err:
            print(f"[Following Import] Cookie load error: {cookie_err}")
            with open(cookie_file, 'r', encoding='utf-8') as f:
                content = f.read()
            if not content.startswith('# Netscape') and not content.startswith('# HTTP Cookie'):
                return jsonify({'error': 'Cookie file format not recognized. Please use Netscape/Mozilla format.'}), 400
            raise
        
        print(f"[Following Import] Loaded {len(cookie_jar)} cookies")
        
        # Get user ID from the ds_user_id cookie
        user_id = None
        for cookie in cookie_jar:
            if cookie.name == 'ds_user_id':
                user_id = cookie.value
                break
        
        if not user_id:
            return jsonify({'error': 'Could not find Instagram user ID in cookies. Please re-login and re-upload cookies.'}), 400
        
        print(f"[Following Import] Got user ID from cookies: {user_id}")
        
        # Build opener with cookies
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        opener.addheaders = [
            ('User-Agent', USER_AGENTS[0]),
            ('X-IG-App-ID', '936619743392459'),
            ('X-Requested-With', 'XMLHttpRequest'),
            ('Accept', '*/*'),
            ('Accept-Language', 'en-US,en;q=0.9'),
            ('Referer', 'https://www.instagram.com/'),
        ]
        
        # Fetch following list
        following = []
        end_cursor = None
        max_pages = 20  # Limit to avoid rate limiting
        
        for page in range(max_pages):
            if end_cursor:
                following_url = f'https://www.instagram.com/api/v1/friendships/{user_id}/following/?count=50&max_id={end_cursor}'
            else:
                following_url = f'https://www.instagram.com/api/v1/friendships/{user_id}/following/?count=50'
            
            print(f"[Following Import] Fetching page {page + 1}...")
            response = opener.open(following_url, timeout=30)
            data = json.loads(response.read().decode('utf-8'))
            
            users = data.get('users', [])
            for user in users:
                following.append({
                    'username': user.get('username'),
                    'full_name': user.get('full_name', ''),
                    'profile_pic': user.get('profile_pic_url', '')
                })
            
            print(f"[Following Import] Found {len(users)} users on this page, total: {len(following)}")
            
            # Check for next page
            if not data.get('next_max_id'):
                break
            end_cursor = data.get('next_max_id')
            
            # Rate limiting delay
            time.sleep(0.5)
        
        print(f"[Following Import] Complete! Found {len(following)} accounts")
        return jsonify({'following': following, 'count': len(following)})
        
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore') if e.fp else ''
        print(f"[Following Import] HTTP Error {e.code}: {error_body[:200]}")
        if e.code == 401 or e.code == 403:
            return jsonify({'error': 'Instagram cookies expired or invalid. Please re-upload cookies.'}), 401
        elif e.code == 404:
            return jsonify({'error': 'Could not access following list.'}), 404
        return jsonify({'error': f'Instagram API error: {e.code}'}), 500
    except Exception as e:
        import traceback
        print(f"[Following Import] Error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/proxy-image')
def proxy_image():
    """Proxy external images to bypass CORS restrictions"""
    import urllib.request
    import urllib.error
    from flask import Response
    
    url = request.args.get('url', '')
    if not url:
        return '', 400
    
    # Only allow Instagram CDN URLs
    if not url.startswith('https://instagram.') and not url.startswith('https://scontent'):
        return '', 403
    
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', USER_AGENTS[0])
        req.add_header('Referer', 'https://www.instagram.com/')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
            return Response(
                content,
                mimetype=content_type,
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'Access-Control-Allow-Origin': '*'
                }
            )
    except Exception as e:
        print(f"[Image Proxy] Error: {e}")
        return '', 404

@app.route('/api/users', methods=['GET', 'POST'])
def api_users():
    """User CRUD"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        username = data.get('username', '').strip()
        platform = data.get('platform', '').lower()
        coomer_service = data.get('coomer_service', 'onlyfans')  # Default to onlyfans
        
        if not username or not platform:
            return jsonify({'error': 'Username and platform required'}), 400
        
        if platform not in ['instagram', 'tiktok', 'coomer']:
            return jsonify({'error': 'Invalid platform'}), 400
        
        # For coomer, store as service/username format
        if platform == 'coomer':
            username_to_store = f"{coomer_service}/{username}"
        else:
            username_to_store = username
        
        try:
            cursor.execute('''
                INSERT INTO users (username, platform) VALUES (?, ?)
            ''', (username_to_store, platform))
            conn.commit()
            user_id = cursor.lastrowid
            conn.close()
            return jsonify({'id': user_id, 'username': username_to_store, 'platform': platform})
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({'error': 'User already exists'}), 409
    
    # GET - list users
    cursor.execute('''
        SELECT u.*, GROUP_CONCAT(t.id || ':' || t.name || ':' || t.color) as tags
        FROM users u
        LEFT JOIN user_tags ut ON u.id = ut.user_id
        LEFT JOIN tags t ON ut.tag_id = t.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    ''')
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Parse tags
    for user in users:
        if user['tags']:
            user['tags'] = [
                {'id': int(t.split(':')[0]), 'name': t.split(':')[1], 'color': t.split(':')[2]}
                for t in user['tags'].split(',')
            ]
        else:
            user['tags'] = []
    
    return jsonify(users)

@app.route('/api/refresh-avatars', methods=['POST'])
def api_refresh_avatars():
    """Refresh all user avatars by re-downloading them"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, platform FROM users')
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        return jsonify({'message': 'No users to refresh', 'count': 0})
    
    # Clear existing avatars
    cleared = 0
    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
        for avatar_file in AVATARS_DIR.glob(f'*{ext}'):
            try:
                avatar_file.unlink()
                cleared += 1
            except:
                pass
    
    print(f"[Avatar Refresh] Cleared {cleared} existing avatars")
    
    # Download avatars in background
    def refresh_all_bg():
        for user in users:
            username = user['username']
            platform = user['platform']
            try:
                print(f"[Avatar Refresh] Downloading avatar for {platform}/{username}")
                download_avatar_with_gallery_dl(username, platform)
            except Exception as e:
                print(f"[Avatar Refresh] Error for {username}: {e}")
    
    threading.Thread(target=refresh_all_bg, daemon=True).start()
    
    return jsonify({
        'message': f'Refreshing avatars for {len(users)} users in background',
        'count': len(users),
        'cleared': cleared
    })

@app.route('/api/users/<int:user_id>', methods=['GET', 'DELETE', 'PATCH'])
def api_user(user_id):
    """Single user operations"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'DELETE':
        # Get user info first for folder deletion option
        cursor.execute('SELECT username, platform FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if user:
            cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
            conn.commit()
            
            # Optionally delete files
            if request.args.get('delete_files') == 'true':
                user_dir = DOWNLOADS_DIR / user['platform'] / user['username']
                if user_dir.exists():
                    shutil.rmtree(user_dir)
        
        conn.close()
        return jsonify({'success': True})
    
    elif request.method == 'PATCH':
        data = request.json
        if 'display_name' in data:
            cursor.execute('UPDATE users SET display_name = ? WHERE id = ?', 
                          (data['display_name'], user_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    
    # GET
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return jsonify(dict(user))
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/users/<int:user_id>/sync', methods=['POST'])
def api_user_sync(user_id):
    """Trigger sync for a user"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT username, platform FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    queue_id = add_to_queue(user['username'], user['platform'])
    return jsonify({'queue_id': queue_id, 'message': 'Added to download queue'})

@app.route('/api/sync-all', methods=['POST'])
def api_sync_all():
    """Sync all users"""
    count = sync_all_users()
    return jsonify({'message': f'Started sync for {count} users', 'count': count})

# Tags API
@app.route('/api/tags', methods=['GET', 'POST'])
def api_tags():
    """Tag CRUD"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        name = data.get('name', '').strip()
        color = data.get('color', '#3b82f6')
        
        if not name:
            return jsonify({'error': 'Name required'}), 400
        
        try:
            cursor.execute('INSERT INTO tags (name, color) VALUES (?, ?)', (name, color))
            conn.commit()
            tag_id = cursor.lastrowid
            conn.close()
            return jsonify({'id': tag_id, 'name': name, 'color': color})
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({'error': 'Tag already exists'}), 409
    
    cursor.execute('SELECT * FROM tags ORDER BY name')
    tags = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(tags)

@app.route('/api/tags/<int:tag_id>', methods=['DELETE', 'PATCH'])
def api_tag(tag_id):
    """Single tag operations"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'DELETE':
        cursor.execute('DELETE FROM tags WHERE id = ?', (tag_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    
    elif request.method == 'PATCH':
        data = request.json
        if 'name' in data:
            cursor.execute('UPDATE tags SET name = ? WHERE id = ?', (data['name'], tag_id))
        if 'color' in data:
            cursor.execute('UPDATE tags SET color = ? WHERE id = ?', (data['color'], tag_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    
    return jsonify({'error': 'Method not allowed'}), 405

@app.route('/api/users/<int:user_id>/tags', methods=['POST', 'DELETE'])
def api_user_tags(user_id):
    """Assign/remove tags from user"""
    conn = get_db()
    cursor = conn.cursor()
    data = request.json
    tag_id = data.get('tag_id')
    
    if not tag_id:
        return jsonify({'error': 'tag_id required'}), 400
    
    if request.method == 'POST':
        try:
            cursor.execute('INSERT INTO user_tags (user_id, tag_id) VALUES (?, ?)', 
                          (user_id, tag_id))
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # Already assigned
    else:
        cursor.execute('DELETE FROM user_tags WHERE user_id = ? AND tag_id = ?', 
                      (user_id, tag_id))
        conn.commit()
    
    conn.close()
    return jsonify({'success': True})

# Download Queue API
@app.route('/api/queue', methods=['GET'])
def api_queue():
    """Get download queue status"""
    with queue_lock:
        queue_data = []
        for job in download_queue:
            queue_data.append({
                'id': job['id'],
                'username': job['username'],
                'platform': job['platform'],
                'status': job['status'],
                'progress': job['progress'],
                'message': job['message'],
                'files_downloaded': job.get('files_downloaded', 0),
                'started_at': job['started_at'],
                'completed_at': job['completed_at']
            })
    
    return jsonify(queue_data)

@app.route('/api/queue/<int:queue_id>/pause', methods=['POST'])
def api_queue_pause(queue_id):
    """Pause a download"""
    with queue_lock:
        for job in download_queue:
            if job['id'] == queue_id and job['status'] == 'active':
                job['status'] = 'paused'
                if job['process']:
                    job['process'].terminate()
                return jsonify({'success': True})
    
    return jsonify({'error': 'Job not found or not active'}), 404

@app.route('/api/queue/<int:queue_id>/resume', methods=['POST'])
def api_queue_resume(queue_id):
    """Resume a paused download"""
    with queue_lock:
        for job in download_queue:
            if job['id'] == queue_id and job['status'] == 'paused':
                job['status'] = 'queued'
                job['message'] = 'Resuming...'
                threading.Thread(target=process_queue, daemon=True).start()
                return jsonify({'success': True})
    
    return jsonify({'error': 'Job not found or not paused'}), 404

@app.route('/api/queue/<int:queue_id>', methods=['DELETE'])
def api_queue_delete(queue_id):
    """Remove a job from queue"""
    with queue_lock:
        for i, job in enumerate(download_queue):
            if job['id'] == queue_id:
                if job['status'] == 'active' and job['process']:
                    job['process'].terminate()
                download_queue.pop(i)
                return jsonify({'success': True})
    
    return jsonify({'error': 'Job not found'}), 404

@app.route('/api/queue/clear', methods=['POST'])
def api_queue_clear():
    """Clear completed/failed jobs"""
    with queue_lock:
        i = 0
        while i < len(download_queue):
            if download_queue[i]['status'] in ['completed', 'failed']:
                download_queue.pop(i)
            else:
                i += 1
    
    return jsonify({'success': True})

# External Download API
@app.route('/api/download', methods=['POST'])
def api_download():
    """Download from external URL"""
    data = request.json
    url = data.get('url', '').strip()
    folder = data.get('folder', 'external').strip()
    
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    queue_id = add_to_queue('external', 'external', url=url, folder=folder)
    return jsonify({'queue_id': queue_id, 'message': 'Added to download queue'})

# Cookie Management API
@app.route('/api/cookies', methods=['GET'])
def api_cookies_list():
    """List Instagram cookie files"""
    cookie_dir = COOKIES_DIR / 'instagram'
    cookies = []
    
    default_cookie = get_setting('default_instagram_cookie', '')
    
    for f in cookie_dir.glob('*.txt'):
        cookies.append({
            'filename': f.name,
            'size': f.stat().st_size,
            'modified': f.stat().st_mtime,
            'is_default': f.name == default_cookie
        })
    
    return jsonify(cookies)

@app.route('/api/cookies/upload', methods=['POST'])
def api_cookies_upload():
    """Upload a cookie file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    
    # Sanitize filename
    filename = file.filename
    if not filename.endswith('.txt'):
        filename += '.txt'
    
    # Save file
    cookie_path = COOKIES_DIR / 'instagram' / filename
    file.save(str(cookie_path))
    
    # Set as default if first cookie
    if not get_setting('default_instagram_cookie'):
        set_setting('default_instagram_cookie', filename)
    
    return jsonify({'filename': filename, 'message': 'Cookie uploaded successfully'})

@app.route('/api/cookies/<filename>', methods=['DELETE'])
def api_cookies_delete(filename):
    """Delete a cookie file"""
    cookie_path = COOKIES_DIR / 'instagram' / filename
    
    if cookie_path.exists():
        cookie_path.unlink()
        
        # Clear default if deleted
        if get_setting('default_instagram_cookie') == filename:
            set_setting('default_instagram_cookie', '')
        
        return jsonify({'success': True})
    
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/cookies/<filename>/rename', methods=['POST'])
def api_cookies_rename(filename):
    """Rename a cookie file"""
    data = request.json
    new_name = data.get('new_name', '').strip()
    
    if not new_name:
        return jsonify({'error': 'New name required'}), 400
    
    if not new_name.endswith('.txt'):
        new_name += '.txt'
    
    old_path = COOKIES_DIR / 'instagram' / filename
    new_path = COOKIES_DIR / 'instagram' / new_name
    
    if not old_path.exists():
        return jsonify({'error': 'File not found'}), 404
    
    if new_path.exists():
        return jsonify({'error': 'A file with that name already exists'}), 409
    
    old_path.rename(new_path)
    
    # Update default if renamed
    if get_setting('default_instagram_cookie') == filename:
        set_setting('default_instagram_cookie', new_name)
    
    return jsonify({'success': True, 'filename': new_name})

@app.route('/api/cookies/default', methods=['POST'])
def api_cookies_set_default():
    """Set default cookie file"""
    data = request.json
    filename = data.get('filename', '').strip()
    
    cookie_path = COOKIES_DIR / 'instagram' / filename
    if not cookie_path.exists():
        return jsonify({'error': 'File not found'}), 404
    
    set_setting('default_instagram_cookie', filename)
    return jsonify({'success': True})

# Settings API
@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """Settings CRUD"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        for key, value in data.items():
            set_setting(key, value)
        
        # Reinitialize Telegram bot if token changed
        if 'telegram_bot_token' in data:
            init_telegram_bot()
        
        # Restart scheduler if settings changed
        if 'scheduler_enabled' in data or 'scheduler_time' in data:
            stop_scheduler()
            if get_setting('scheduler_enabled') == 'true':
                start_scheduler()
        
        return jsonify({'success': True})
    
    cursor.execute('SELECT * FROM settings')
    settings = {row['key']: row['value'] for row in cursor.fetchall()}
    conn.close()
    
    return jsonify(settings)

# Password API
@app.route('/api/password/status')
def api_password_status():
    """Check if password protection is enabled"""
    password_hash = get_setting('app_password_hash', '')
    return jsonify({'enabled': bool(password_hash)})

@app.route('/api/password/set', methods=['POST'])
def api_password_set():
    """Set app password"""
    data = request.json
    password = data.get('password', '')
    
    if len(password) < 4:
        return jsonify({'error': 'Password must be at least 4 characters'}), 400
    
    password_hash = hash_password(password)
    set_setting('app_password_hash', password_hash)
    
    # Auto-authenticate current session
    session['authenticated'] = True
    session.permanent = True
    
    return jsonify({'success': True})

@app.route('/api/password/remove', methods=['POST'])
def api_password_remove():
    """Remove password protection"""
    set_setting('app_password_hash', '')
    session.pop('authenticated', None)
    return jsonify({'success': True})

# Setup Wizard API
@app.route('/api/setup/status')
def api_setup_status():
    """Check if first-time setup has been completed"""
    completed = get_setting('setup_completed', 'false')
    return jsonify({'completed': completed == 'true'})

@app.route('/api/setup/complete', methods=['POST'])
def api_setup_complete():
    """Mark first-time setup as completed"""
    set_setting('setup_completed', 'true')
    return jsonify({'success': True})

# Backup/Restore API
@app.route('/api/backup', methods=['GET'])
def api_backup():
    """Download backup ZIP"""
    # Create temporary ZIP
    zip_buffer = BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add database (contains password hash, telegram settings, all user data)
        if DB_PATH.exists():
            zf.write(DB_PATH, 'trackui.db')
        
        # Add all cookies from all subdirectories
        if COOKIES_DIR.exists():
            for platform_dir in COOKIES_DIR.iterdir():
                if platform_dir.is_dir():
                    for f in platform_dir.glob('*.txt'):
                        zf.write(f, f'cookies/{platform_dir.name}/{f.name}')
    
    zip_buffer.seek(0)
    
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'trackui_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
    )

@app.route('/api/restore', methods=['POST'])
def api_restore():
    """Restore from backup ZIP"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    try:
        with zipfile.ZipFile(file, 'r') as zf:
            # Extract database
            if 'trackui.db' in zf.namelist():
                zf.extract('trackui.db', str(DATA_DIR))
            
            # Extract cookies
            for name in zf.namelist():
                if name.startswith('cookies/'):
                    zf.extract(name, str(DATA_DIR))
        
        return jsonify({'success': True, 'message': 'Backup restored successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/factory-reset', methods=['POST'])
def api_factory_reset():
    """Factory reset - delete database and optionally files"""
    data = request.json
    delete_files = data.get('delete_files', False)
    
    # Close any active downloads
    with queue_lock:
        for job in download_queue:
            if job['process']:
                job['process'].terminate()
        download_queue.clear()
    
    # Delete database
    if DB_PATH.exists():
        DB_PATH.unlink()
    
    # Delete cookies
    if COOKIES_DIR.exists():
        shutil.rmtree(COOKIES_DIR)
        COOKIES_DIR.mkdir()
        (COOKIES_DIR / 'instagram').mkdir(exist_ok=True)
    
    # Delete avatars
    if AVATARS_DIR.exists():
        shutil.rmtree(AVATARS_DIR)
        AVATARS_DIR.mkdir()
    
    # Reinitialize database
    init_db()
    
    # Optionally delete downloaded files
    if delete_files and DOWNLOADS_DIR.exists():
        shutil.rmtree(DOWNLOADS_DIR)
        DOWNLOADS_DIR.mkdir()
    
    return jsonify({'success': True, 'message': 'Factory reset complete'})

# Media serving
@app.route('/media/<path:filepath>')
def serve_media(filepath):
    """Serve media files - handles special characters in filenames"""
    import urllib.parse
    # Decode URL-encoded characters (Hebrew, emojis, spaces, etc.)
    decoded_path = urllib.parse.unquote(filepath)
    
    # Try the decoded path first, then original
    full_path = DOWNLOADS_DIR / decoded_path
    if full_path.exists():
        return send_from_directory(str(DOWNLOADS_DIR), decoded_path)
    
    # Fallback to original path
    return send_from_directory(str(DOWNLOADS_DIR), filepath)

@app.route('/avatars/<path:filename>')
def serve_avatar(filename):
    """Serve avatar files"""
    return send_from_directory(str(AVATARS_DIR), filename)

# Likes/Favorites API
@app.route('/api/likes', methods=['GET', 'POST'])
def api_likes():
    """Manage favorites"""
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        filename = data.get('filename', '').strip()
        
        if not filename:
            return jsonify({'error': 'Filename required'}), 400
        
        try:
            cursor.execute('INSERT INTO likes (media_filename) VALUES (?)', (filename,))
            conn.commit()
            conn.close()
            return jsonify({'success': True})
        except sqlite3.IntegrityError:
            # Already liked, so unlike
            cursor.execute('DELETE FROM likes WHERE media_filename = ?', (filename,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'action': 'unliked'})
    
    cursor.execute('SELECT * FROM likes ORDER BY liked_at DESC')
    likes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(likes)

# =============================================================================
# PWA Support
# =============================================================================

@app.route('/manifest.json')
def manifest():
    """PWA manifest"""
    return jsonify({
        "name": "TrackUI",
        "short_name": "TrackUI",
        "description": "Social Media Archiver & Tracker",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#121212",
        "theme_color": "#3b82f6",
        "icons": [
            {
                "src": "/static/icon-192.png",
                "sizes": "192x192",
                "type": "image/png"
            },
            {
                "src": "/static/icon-512.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    })

@app.route('/sw.js')
def service_worker():
    """Service worker for PWA"""
    return Response('''
const CACHE_NAME = 'trackui-v2';
const STATIC_ASSETS = [
    '/static/style.css',
    '/static/app.js',
    '/static/icon-192.png',
    '/static/icon-512.png'
];

self.addEventListener('install', event => {
    // Skip waiting to activate immediately
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
    );
});

self.addEventListener('activate', event => {
    // Clean up old caches
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            );
        })
    );
});

self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    
    // For HTML pages (navigation requests), always go network-first
    if (event.request.mode === 'navigate' || 
        event.request.headers.get('accept')?.includes('text/html')) {
        event.respondWith(
            fetch(event.request)
                .catch(() => caches.match(event.request))
        );
        return;
    }
    
    // For API requests, always use network
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(fetch(event.request));
        return;
    }
    
    // For static assets, use cache-first
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request).then(response => {
                return response || fetch(event.request).then(fetchResponse => {
                    // Cache new static assets
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(event.request, fetchResponse.clone());
                    });
                    return fetchResponse;
                });
            })
        );
        return;
    }
    
    // Default: network-first
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
''', mimetype='application/javascript')

# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == '__main__':
    # Initialize database
    init_db()
    
    # Start scheduler if enabled
    if get_setting('scheduler_enabled') == 'true':
        start_scheduler()
    
    # Initialize Telegram bot
    init_telegram_bot()
    
    # Run Flask
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
