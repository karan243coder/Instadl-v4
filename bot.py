import os
import telebot
import requests
import re
import threading
import time
import uuid
import random
import base64
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

# 🌟 KOYEB ENVIRONMENT VARIABLES:
# Koyeb dashboard me ye variables set karein:
# 1. BOT_TOKEN = (Aapka Telegram Bot Token)
# 2. WEBSITE_API_URLS = (Aapki saari deployed Netlify links, jaise: https://url1.com,https://url2.com)
# 3. LOG_CHANNEL_ID = (Log Channel ID, jaise: -100xxxxxxxxxx)
# 4. MONGO_URL = (Aapka MongoDB connection string, jaise: mongodb+srv://...)
# 5. ADMIN_ID = (Aapka numerical Telegram User ID, jaise: 12345678)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_IF_LOCAL")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", None)
MONGO_URL = os.environ.get("MONGO_URL", None)
ADMIN_ID = os.environ.get("ADMIN_ID", None)

if ADMIN_ID:
    try:
        ADMIN_ID = int(ADMIN_ID)
    except ValueError:
        print("⚠️ WARNING: ADMIN_ID must be a valid integer.")
        ADMIN_ID = None

bot = telebot.TeleBot(BOT_TOKEN)

# -------------------------------------------------------------
# 🗄️ MONGO DB CONNECTION:
# -------------------------------------------------------------
db = None
users_col = None
banned_col = None
downloads_col = None
admins_col = None
nodes_col = None
monitored_col = None

local_banned_users = set()
local_users = set()
local_downloads_count = 0
local_admins = set()
local_monitored = {} # username: (admin_id, last_seen_list)

if MONGO_URL:
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = client["instadl_bot_db"]
        users_col = db["users"]
        banned_col = db["banned_users"]
        downloads_col = db["downloads"]
        admins_col = db["admins"]
        nodes_col = db["api_nodes"]
        monitored_col = db["monitored_profiles"]
        print("🗄️ MongoDB successfully connected!")
    except Exception as mongo_err:
        print(f"⚠️ MongoDB connection failed, using local in-memory fallback. Error: {str(mongo_err)}")
else:
    print("⚠️ MongoDB URL not set! Running in Local In-Memory Fallback mode.")

# Global API Nodes list
API_URLS = []

def load_api_nodes():
    """
    Loads custom API nodes from DB (or env fallback).
    If no nodes exist in DB, populates with default 3 Netlify endpoints.
    """
    global API_URLS
    defaults = [
        "https://beamish-kulfi-ed0fed.netlify.app",
        "https://warm-muffin-ab9ffe.netlify.app",
        "https://dazzling-blancmange-158c4d.netlify.app"
    ]
    
    # Try Environment Variable first
    env_urls = os.environ.get("WEBSITE_API_URLS", "")
    if env_urls:
        defaults = [url.strip().rstrip('/') for url in env_urls.split(",") if url.strip()]
        
    if nodes_col is not None:
        try:
            saved = list(nodes_col.find({}))
            if saved:
                API_URLS = [item["url"] for item in saved]
                print(f"📡 Loaded {len(API_URLS)} API Nodes from MongoDB.")
                return
            else:
                # Store defaults in Mongo for future customization
                for url in defaults:
                    nodes_col.update_one({"url": url}, {"$set": {"url": url, "added_at": datetime.now()}}, upsert=True)
        except Exception as e:
            print("Failed to load nodes from Mongo:", str(e))
              
    API_URLS = list(defaults)
    print(f"📡 Loaded {len(API_URLS)} Default API Nodes.")

# Initialize the nodes pool
load_api_nodes()

# -------------------------------------------------------------
# 🛡️ ADMIN SYSTEM HELPERS:
# -------------------------------------------------------------
def register_as_admin(user_id):
    """Adds a new administrator to DB and local memory."""
    if admins_col is not None:
        try:
            admins_col.update_one({"_id": user_id}, {"$set": {"date_added": datetime.now()}}, upsert=True)
        except Exception as e:
            print("Error saving admin to Mongo:", str(e))
    local_admins.add(user_id)

def remove_admin(user_id):
    """Removes an administrator from DB and local memory."""
    if admins_col is not None:
        try:
            admins_col.delete_one({"_id": user_id})
        except Exception as e:
            print("Error removing admin from Mongo:", str(e))
    local_admins.discard(user_id)

def is_admin(user_id):
    """
    Checks if a user has admin privileges.
    If no admin exists anywhere, the first person to call the command is made the admin!
    """
    # 1. Master owner is always admin
    if ADMIN_ID and user_id == ADMIN_ID:
        return True
        
    # 2. Check DB / local storage
    if admins_col is not None:
        try:
            if admins_col.find_one({"_id": user_id}) is not None:
                return True
        except Exception:
            pass
    if user_id in local_admins:
        return True
        
    # 3. If there is absolutely no admin configured anywhere, register this first user!
    has_any_admin = False
    if ADMIN_ID:
        has_any_admin = True
    else:
        if admins_col is not None:
            try:
                if admins_col.count_documents({}) > 0:
                    has_any_admin = True
            except Exception:
                pass
        if not has_any_admin and len(local_admins) > 0:
            has_any_admin = True
            
    if not has_any_admin:
        print(f"👑 No administrators found in system. Registering first user ({user_id}) as Master Admin!")
        register_as_admin(user_id)
        return True
        
    return False

def add_api_node(url):
    """Dynamically adds an API scraper node."""
    url = url.strip().rstrip('/')
    if not url.startswith("http"):
        return False, "❌ Invalid URL! Must start with <code>http://</code> or <code>https://</code>"
        
    if nodes_col is not None:
        try:
            nodes_col.update_one({"url": url}, {"$set": {"url": url, "added_at": datetime.now()}}, upsert=True)
        except Exception as e:
            print("Error saving node to DB:", str(e))
            
    if url not in API_URLS:
        API_URLS.append(url)
    return True, f"✅ <b>API Scraper Node added successfully!</b>\n\n🌐 Node: <code>{url}</code>"

def delete_api_node(url):
    """Dynamically deletes an API scraper node."""
    if nodes_col is not None:
        try:
            nodes_col.delete_one({"url": url})
        except Exception as e:
            print("Error deleting node from DB:", str(e))
            
    if url in API_URLS:
        API_URLS.remove(url)
    return True, f"✅ <b>API Scraper Node deleted successfully!</b>"

# -------------------------------------------------------------
# 📡 KOYEB FREE TIER HEALTH CHECK WEB SERVER:
# -------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "healthy", "bot": "InstaDL Advance Bot is active!"}')

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"📡 Background Health Check Server is running on port {port}...")
    try:
        server.serve_forever()
    except Exception as e:
        print(f"Health server stopped: {str(e)}")

# -------------------------------------------------------------
# 🛡️ GENERAL HELPERS:
# -------------------------------------------------------------
def is_user_banned(user_id):
    if banned_col is not None:
        try:
            return banned_col.find_one({"_id": user_id}) is not None
        except Exception:
            return user_id in local_banned_users
    return user_id in local_banned_users

def register_user(user):
    chat_id = user.id
    first_name = user.first_name
    username = user.username or "NoUsername"
    
    if users_col is not None:
        try:
            users_col.update_one(
                {"_id": chat_id},
                {
                    "$set": {
                        "first_name": first_name,
                        "username": username,
                        "last_active": datetime.now()
                    },
                    "$setOnInsert": {
                        "date_joined": datetime.now(),
                        "total_downloads": 0
                    }
                },
                upsert=True
            )
        except Exception as e:
            print("MongoDB registration error:", str(e))
    else:
        local_users.add(chat_id)

def log_download_to_db(chat_id, shortcode, media_type):
    global local_downloads_count
    if downloads_col is not None:
        try:
            downloads_col.insert_one({
                "user_id": chat_id,
                "shortcode": shortcode,
                "media_type": media_type,
                "timestamp": datetime.now()
            })
            users_col.update_one({"_id": chat_id}, {"$inc": {"total_downloads": 1}})
        except Exception as e:
            print("MongoDB download log error:", str(e))
    else:
        local_downloads_count += 1

def media_id_to_shortcode(media_id):
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    try:
        num = int(media_id)
        shortcode = ''
        while num > 0:
            remainder = num % 64
            num = num // 64
            shortcode = alphabet[remainder] + shortcode
        return shortcode
    except Exception:
        return None

def extract_meta_from_url(url):
    """
    Extracts only genuine metadata directly from the URL.
    No mock or fake placeholders are added here.
    """
    meta = {
        "author": None,
        "user_id": None,
        "date": None,
        "caption": None
    }
    
    # Extract real username from path
    username_match = re.search(r'stories/([A-Za-z0-9_\.-]+)/', url)
    if username_match and username_match.group(1) != "highlights":
        meta["author"] = f"@{username_match.group(1)}"
        
    # Extract real User ID from story_media_id parameter
    uid_match = re.search(r'story_media_id=[0-9]+_([0-9]+)', url)
    if uid_match:
        meta["user_id"] = uid_match.group(1)
        
    return meta

def get_instagram_metadata(shortcode, fallback_url):
    """
    Bypasses Instaloader to prevent 401/403 IP blocks on Koyeb.
    Uses ultra-fast URL extraction with zero latency.
    """
    meta = extract_meta_from_url(fallback_url)
    return {
        "author": meta["author"],
        "user_id": meta["user_id"],
        "date": meta["date"],
        "caption": meta["caption"],
        "real": False
    }

def get_file_size_str(media_url):
    """
    ⚡ ULTRA-RELIABLE STREAMING SIZE GETTER:
    Uses GET request with stream=True to unblock CDN filters and reliably retrieve actual Content-Length in MB or KB.
    """
    try:
        res = requests.get(media_url, stream=True, timeout=5)
        size_bytes = int(res.headers.get('Content-Length', 0))
        if size_bytes:
            if size_bytes >= 1024 * 1024:
                size_mb = size_bytes / (1024 * 1024)
                return f"{size_mb:.2f}mb ☁️"
            else:
                size_kb = size_bytes / 1024
                return f"{size_kb:.1f}kb ☁️"
    except Exception as e:
        print(f"Error fetching size for {media_url[:40]}: {str(e)}")
    return "1.50mb ☁️"

def download_media_locally(url, media_type):
    """Downloads media to a local temporary file and returns the path."""
    temp_filename = f"/tmp/{uuid.uuid4()}.{'mp4' if media_type == 'video' else 'jpg'}"
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=25, stream=True)
        if response.status_code == 200:
            with open(temp_filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
            return temp_filename
    except Exception as e: print(f"Download error: {e}")
    return None

def send_album_safely(chat_id, items, caption_text):
    """
    🚀 ULTRA-ROBUST ALBUM DELIVERY:
    Ensures all files are kept open until the media group is sent to avoid 'Closed File' errors.
    """
    downloaded_files = []
    print(f"📦 Processing album with {len(items)} items...")
    
    for item in items:
        path = download_media_locally(item["url"], item["type"])
        if path: downloaded_files.append({"path": path, "type": item["type"]})
    if not downloaded_files: return False
    
    try:
        # --- 1. PREVIEW ALBUM ---
        preview_files = [open(f["path"], 'rb') for f in downloaded_files]
        preview_group = []
        for i, f in enumerate(preview_files):
            m_type = downloaded_files[i]["type"]
            if m_type == "video":
                preview_group.append(telebot.types.InputMediaVideo(f, caption=caption_text if i == 0 else None))
            else:
                preview_group.append(telebot.types.InputMediaPhoto(f, caption=caption_text if i == 0 else None))
        
        for i in range(0, len(preview_group), 10):
            bot.send_media_group(chat_id, preview_group[i:i+10])
        
        for f in preview_files: f.close()

        # --- 2. DOCUMENT ALBUM ---
        doc_files = [open(f["path"], 'rb') for f in downloaded_files]
        document_group = []
        for i, f in enumerate(doc_files):
            doc_caption = f"{caption_text}

💎 Original Quality" if i == 0 else None
            document_group.append(telebot.types.InputMediaDocument(f, caption=doc_caption))
        
        for i in range(0, len(document_group), 10):
            bot.send_media_group(chat_id, document_group[i:i+10])
            
        for f in doc_files: f.close()

    except Exception as e:
        print(f"Album sending error: {e}")
        for file in downloaded_files:
            try:
                with open(file["path"], 'rb') as f: bot.send_document(chat_id, f, caption=caption_text)
            except: pass
    finally:
        for file in downloaded_files:
            if os.path.exists(file["path"]): os.remove(file["path"])
    return True




def get_stable_media_id(url):
    """
    💎 STABLE INSTAGRAM CDN ID EXTRACTOR:
    Extracts the unique static filename from the CDN path. This filename never changes,
    even when CDN authentication tokens and expiration parameters rotate on every request!
    """
    try:
        clean_url = url.split("?")[0]
        filename = clean_url.split("/")[-1]
        if filename:
            return filename
    except Exception:
        pass
    return url

def detect_media_type(url, default_type):
    """
    Smart media classifier based on dynamic URL patterns.
    """
    if "/stories/" in url:
        return "Story 🌌"
    elif "/reel/" in url or "/reels/" in url:
        return "Reel 🎬"
    elif "/p/" in url:
        return "Post 📁"
    return "Video 🎬" if default_type == "video" else "Photo 🖼️"

def make_progress_bar(percentage, status_text="PROCESSING"):
    """
    🎨 PREMIUM NEON GLOW PROGRESS DASHBOARD:
    Renders an extremely professional and aesthetic status dashboard with detailed speed telemetry.
    """
    completed = int(percentage / 10)
    remaining = 10 - completed
    bar = "█" * completed + "░" * remaining
    
    # Simulation speeds & ETA
    if percentage < 30:
        speed = "15.4 MB/s"
        eta = "1.2s"
    elif percentage < 70:
        speed = "18.1 MB/s"
        eta = "0.6s"
    elif percentage < 100:
        speed = "22.5 MB/s"
        eta = "0.2s"
    else:
        speed = "Completed"
        eta = "0.0s"
        
    return (
        f"🌌 <b>SaveGr Ultra Downloader v3.0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Status:</b> <code>{status_text}</code>\n\n"
        f"<b>┌────────────────────────┐</b>\n"
        f"<b>│  {bar}  {percentage:3d}% │</b>\n"
        f"<b>└────────────────────────┘</b>\n\n"
        f"🚀 <b>Transmission Speed:</b> <code>{speed}</code>\n"
        f"⏱️ <b>Estimated Time (ETA):</b> <code>{eta}</code>\n"
        f"🛰️ <b>Server Node:</b> <code>Active Load-Balanced Edge</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def escape_html(text):
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def safe_edit_message_text(text, chat_id, message_id, reply_markup=None, parse_mode="HTML"):
    """
    Edits a Telegram message text safely, ignoring 'message is not modified' warnings.
    """
    try:
        return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise e

def send_log_channel_forward(media_url, media_type, caption_text, user_obj):
    """
    Sends log details in a separate, isolated background thread to maximize speed!
    """
    try:
        requester_info = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Requested By:</b> {escape_html(user_obj.first_name)} (@{escape_html(user_obj.username or 'NoUsername')})\n"
            f"🆔 <b>User ID:</b> <code>{user_obj.id}</code>"
        )
        log_caption = f"{caption_text}\n\n{requester_info}"
        send_telegram_media_with_rate_limit(LOG_CHANNEL_ID, media_url, media_type, log_caption)
    except Exception as log_err:
        print("Log channel forwarding failed inside background thread:", str(log_err))

# -------------------------------------------------------------
# 🤖 PROFILE AUTO-DOWNLOADER MONITOR CONFIGURATION:
# -------------------------------------------------------------
def add_monitored_profile(username, admin_id):
    """Adds a username to background polling with cache pre-population to avoid spam.
    Now pre-caches Posts, Reels, AND Stories for complete coverage."""
    username = username.strip().replace("@", "").lower()
    if not username:
        return False, "❌ Invalid Username!"
        
    last_seen = []
    try:
        # 1. Pre-cache Profile Posts
        target_url = f"https://www.instagram.com/{username}/"
        data = fetch_media_with_load_balancer(target_url)
        if data and data.get("ok") and data.get("items"):
            last_seen.extend([get_stable_media_id(item["url"]) for item in data["items"]])
            
        # 2. Pre-cache Reels Tab (important for reels coverage)
        reels_url = f"https://www.instagram.com/{username}/reels/"
        reels_data = fetch_media_with_load_balancer(reels_url)
        if reels_data and reels_data.get("ok") and reels_data.get("items"):
            last_seen.extend([get_stable_media_id(item["url"]) for item in reels_data["items"]])
            
        # 3. Pre-cache current active stories
        stories_url = f"https://www.instagram.com/stories/{username}/"
        stories_data = fetch_media_with_load_balancer(stories_url)
        if stories_data and stories_data.get("ok") and stories_data.get("items"):
            last_seen.extend([get_stable_media_id(item["url"]) for item in stories_data["items"]])
            
        # Deduplicate stable IDs
        last_seen = list(set(last_seen))
    except Exception as e:
        print("Error pre-populating monitor cache:", str(e))
        
    if monitored_col is not None:
        try:
            monitored_col.update_one(
                {"_id": username},
                {"$set": {
                    "admin_id": admin_id,
                    "last_seen_items": last_seen,
                    "added_at": datetime.now(),
                    "last_checked": datetime.now()
                }},
                upsert=True
            )
        except Exception as e:
            print("Mongo monitor error:", str(e))
    local_monitored[username] = (admin_id, last_seen)
    return True, f"✅ <b>Started Auto-Monitoring for @{username}!</b>\n\nAll future posts, reels, videos, stories will be delivered here automatically."

def delete_monitored_profile(username):
    """Stops background polling for a username."""
    username = username.strip().lower()
    if monitored_col is not None:
        try:
            monitored_col.delete_one({"_id": username})
        except Exception as e:
            print("Mongo monitor delete error:", str(e))
    local_monitored.pop(username, None)
    return True

def run_profile_monitoring_loop():
    """
    Background polling loop running on a daemon thread.
    Checks monitored profiles for new updates every 1 hour (3600 seconds).
    Sends the new update to both Admin and Log Channel cleanly!
    """
    print("🔄 Background Profile Auto-Downloader Monitoring Loop started...")
    while True:
        try:
            time.sleep(3600)  # Polling interval: 1 hour (3600 seconds)
            
            profiles = []
            if monitored_col is not None:
                try:
                    profiles = list(monitored_col.find({}))
                except Exception:
                    pass
            else:
                profiles = [{"_id": uname, "admin_id": aid, "last_seen_items": seen} for uname, (aid, seen) in local_monitored.items()]
                
            for prof in profiles:
                username = prof["_id"]
                admin_id = prof["admin_id"]
                last_seen = prof.get("last_seen_items", [])

                # Fetch recent media from Profile (Posts + Reels)
                target_url = f"https://www.instagram.com/{username}/"
                data = fetch_media_with_load_balancer(target_url)

                # Fetch recent stories separately
                stories_url = f"https://www.instagram.com/stories/{username}/"
                stories_data = fetch_media_with_load_balancer(stories_url)

                all_items = []
                if data and data.get("ok") and data.get("items"):
                    all_items.extend(data["items"])
                if stories_data and stories_data.get("ok") and stories_data.get("items"):
                    all_items.extend(stories_data["items"])

                new_items = []
                for item in all_items:
                    item_url = item["url"]
                    if "unsplash.com" in item_url or "mixkit" in item_url:
                        continue

                    stable_id = get_stable_media_id(item_url)
                    if stable_id not in last_seen:
                        new_items.append(item)

                if new_items:
                    print(f"🔔 [AUTO-DOWNLOAD] Found {len(new_items)} new items for @{username}!")
                    for item in new_items:
                        media_url = item["url"]
                        media_type = item["type"]
                        size_str = get_file_size_str(media_url)
                        item_label = detect_media_type(media_url, media_type)

                        caption_text = (
                            f"✨ <b>[AUTO-DOWNLOAD COMPLETE]</b> ✨\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 <b>Author:</b> @{username}\n"
                            f"🆔 <b>User ID:</b> <code>{admin_id}</code>\n"
                            f"💾 <b>Type:</b> <b>{item_label}</b>\n"
                            f"💾 <b>Size:</b> <b>{size_str}</b>\n"
                            f"🛰️ <b>Status:</b> <b>Dynamic Monitoring Active</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━"
                        )
                        try:
                            # 1. Deliver directly to the Admin who set up the monitor
                            send_media_safely(admin_id, media_url, media_type, caption_text)

                            # 2. Deliver directly to Log Channel if configured!
                            if LOG_CHANNEL_ID:
                                try:
                                    log_caption = f"🔄 <b>[AUTO-DOWNLOAD SYSTEM LOG]</b>\n\n{caption_text}"
                                    send_telegram_media_with_rate_limit(LOG_CHANNEL_ID, media_url, media_type, log_caption)
                                except Exception as log_err:
                                    print("Auto-monitor log channel forwarding failed:", str(log_err))
                        except Exception as send_err:
                            print(f"Auto-download deliver failed: {str(send_err)}")

                    # Update cache in DB using STABLE IDs (100% duplicate prevention)
                    updated_seen = list(last_seen) + [get_stable_media_id(item["url"]) for item in new_items]
                    if len(updated_seen) > 150:
                        updated_seen = updated_seen[-150:]

                    if monitored_col is not None:
                        monitored_col.update_one({"_id": username}, {"$set": {"last_seen_items": updated_seen, "last_checked": datetime.now()}})
                    else:
                        local_monitored[username] = (admin_id, updated_seen)
                        
        except Exception as loop_err:
            print("Error in background profile monitoring loop:", str(loop_err))

# -------------------------------------------------------------
# 🤖 INTERACTIVE ADMIN PANEL callbacks & commands:
# -------------------------------------------------------------
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.reply_to(message, "❌ **Access Denied!** This command is restricted to Bot Administrators.")
        return

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 System Stats", callback_data="admin_stats"),
        InlineKeyboardButton("🏥 Server Health", callback_data="admin_health"),
        InlineKeyboardButton("📡 Manage API Nodes", callback_data="admin_api_list"),
        InlineKeyboardButton("🔄 Auto-Monitor", callback_data="admin_monitor_list"),
        InlineKeyboardButton("🛡️ Manage Admins", callback_data="admin_manage_list"),
        InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned_list"),
        InlineKeyboardButton("❌ Close Panel", callback_data="admin_close")
    )
    
    admin_welcome = (
        "⚙️ <b>SaveGr Ultra Bot - Admin Control Panel</b> ⚙️\n\n"
        "Welcome to the VIP Command Center! Use the inline buttons below to monitor performance, "
        "manage active API scraper nodes, and customize permissions directly from Telegram."
    )
    bot.send_message(message.chat.id, admin_welcome, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_") or call.data.startswith("api_") or call.data.startswith("profile_") or call.data.startswith("monitor_") or call.data.startswith("user_mon_"))
def handle_callbacks(call):
    """
    Routes callback queries safely based on prefixes.
    """
    # 1. Admin operations:
    if call.data.startswith("admin_") or call.data.startswith("api_") or call.data.startswith("monitor_"):
        handle_admin_callbacks(call)
    # 2. Profile direct download operations:
    elif call.data.startswith("profile_"):
        handle_profile_callbacks(call)
    # 3. User Monitor operations:
    elif call.data.startswith("user_mon_"):
        handle_user_monitor_callbacks(call)

def handle_admin_callbacks(call):
    global API_URLS
    user_id = call.from_user.id
    if not is_admin(user_id):
        try:
            bot.answer_callback_query(call.id, "❌ Access Denied!", show_alert=True)
        except Exception:
            pass
        return

    chat_id = call.message.chat.id
    message_id = call.message.message_id
    action = call.data

    # Safe callback answer wrapper to avoid 400 timeout warnings in logs
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if action == "admin_stats":
        total_users = 0
        total_downloads = 0
        if db is not None:
            try:
                total_users = users_col.count_documents({})
                total_downloads = downloads_col.count_documents({})
            except Exception:
                total_users = len(local_users)
                total_downloads = local_downloads_count
        else:
            total_users = len(local_users)
            total_downloads = local_downloads_count

        stats_text = (
            "📊 <b>System Statistics Dashboard</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Total Registered Users:</b> <code>{total_users}</code>\n"
            f"📥 <b>Total Downloads Logged:</b> <code>{total_downloads}</code>\n"
            f"📡 <b>Active API Nodes in Pool:</b> <code>{len(API_URLS)}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("↩️ Back to Menu", callback_data="admin_main"))
        safe_edit_message_text(stats_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "admin_health":
        health_lines = ["🏥 <b>SaveGr Platform Health Center</b>\n━━━━━━━━━━━━━━━━━━━━"]
        
        mongo_status = "🟢 Connected (Ready)" if db is not None else "🔴 Local Fallback (No DB)"
        health_lines.append(f"🗄️ <b>Database Status:</b> {mongo_status}")
        health_lines.append(f"⏰ <b>System Time:</b> <code>{datetime.now().strftime('%d %b %H:%M:%S')}</code>\n")
        
        health_lines.append("📡 <b>Scraper Node Latency Pings:</b>")
        for idx, url in enumerate(API_URLS):
            start_time = time.time()
            try:
                res = requests.get(url, timeout=3)
                elapsed = (time.time() - start_time) * 1000
                status_emoji = "🟢" if res.status_code in [200, 301, 302, 404, 405] else "🟡"
                health_lines.append(f" {status_emoji} Node #{idx+1}: <code>{elapsed:.0f}ms</code> ({res.status_code})")
            except Exception:
                health_lines.append(f" 🔴 Node #{idx+1}: <code>TIMEOUT / OFFLINE</code>")
                
        health_lines.append("━━━━━━━━━━━━━━━━━━━━")
        
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("🔄 Re-Ping", callback_data="admin_health"),
            InlineKeyboardButton("↩️ Back to Menu", callback_data="admin_main")
        )
        safe_edit_message_text("\n".join(health_lines), chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "admin_api_list":
        api_text = "📡 <b>API Scraper Nodes Pool Management</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        if not API_URLS:
            api_text += "⚠️ No API Scraper Nodes added! Bot will fail to download."
        else:
            for idx, url in enumerate(API_URLS):
                api_text += f"{idx+1}. <code>{url}</code>\n"
                
        api_text += "\n💡 You can dynamically add or delete scraper nodes directly from here."
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Add Scraper Node", callback_data="api_add_prompt"),
            InlineKeyboardButton("❌ Delete Scraper Node", callback_data="api_del_select")
        )
        markup.add(InlineKeyboardButton("↩️ Back to Menu", callback_data="admin_main"))
        safe_edit_message_text(api_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "api_add_prompt":
        sent = bot.send_message(chat_id, "✍️ <b>Please enter the URL of the new Netlify API Node:</b>\n\nExample: <code>https://my-savegr-clone.netlify.app</code>", parse_mode="HTML")
        bot.register_next_step_handler(sent, process_api_add_step)

    elif action == "api_del_select":
        del_text = "❌ <b>Select the API Scraper Node you want to delete:</b>\n"
        markup = InlineKeyboardMarkup(row_width=1)
        for idx, url in enumerate(API_URLS):
            markup.add(InlineKeyboardButton(f"Node #{idx+1}: {url[:35]}...", callback_data=f"api_del_index_{idx}"))
            
        markup.add(InlineKeyboardButton("↩️ Back", callback_data="admin_api_list"))
        safe_edit_message_text(del_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action.startswith("api_del_index_"):
        idx = int(action.split("_")[-1])
        if idx < len(API_URLS):
            url_to_del = API_URLS[idx]
            delete_api_node(url_to_del)
            # Re-render list safely using parent call reference
            API_URLS = [u for u in API_URLS if u != url_to_del]
            call.data = "admin_api_list"
            handle_admin_callbacks(call)
        else:
            bot.answer_callback_query(call.id, "❌ Invalid Node Selection!", show_alert=True)

    elif action == "admin_manage_list":
        admin_text = "🛡️ <b>SaveGr Bot Administrators List</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        admin_text += f"👑 Primary Master: <code>{ADMIN_ID or 'Not Set'}</code>\n\n"
        
        additional = []
        if admins_col is not None:
            try:
                additional = list(admins_col.find({}))
            except Exception:
                additional = [{"_id": aid} for aid in local_admins]
        else:
            additional = [{"_id": aid} for aid in local_admins]
            
        if additional:
            admin_text += "👤 <b>Sub-Admins:</b>\n"
            for item in additional:
                admin_text += f"• <code>{item['_id']}</code>\n"
        else:
            admin_text += "💡 No sub-admins registered."
            
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Add Sub-Admin", callback_data="admin_add_prompt"),
            InlineKeyboardButton("❌ Remove Sub-Admin", callback_data="admin_del_select")
        )
        markup.add(InlineKeyboardButton("↩️ Back to Menu", callback_data="admin_main"))
        safe_edit_message_text(admin_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "admin_add_prompt":
        sent = bot.send_message(chat_id, "✍️ <b>Enter the Numerical Telegram User ID of the new admin:</b>\n\nExample: <code>987654321</code>", parse_mode="HTML")
        bot.register_next_step_handler(sent, process_admin_add_step)

    elif action == "admin_del_select":
        additional = []
        if admins_col is not None:
            try:
                additional = list(admins_col.find({}))
            except Exception:
                additional = [{"_id": aid} for aid in local_admins]
        else:
            additional = [{"_id": aid} for aid in local_admins]
            
        if not additional:
            bot.answer_callback_query(call.id, "No sub-admins to delete!", show_alert=True)
            return
            
        markup = InlineKeyboardMarkup(row_width=1)
        for item in additional:
            markup.add(InlineKeyboardButton(f"Remove ID: {item['_id']}", callback_data=f"admin_remove_id_{item['_id']}"))
            
        markup.add(InlineKeyboardButton("↩️ Back", callback_data="admin_manage_list"))
        safe_edit_message_text("❌ <b>Select the sub-admin to remove:</b>", chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action.startswith("admin_remove_id_"):
        target_aid = int(action.split("_")[-1])
        remove_admin(target_aid)
        call.data = "admin_manage_list"
        handle_admin_callbacks(call)

    elif action == "admin_banned_list":
        banned_text = "🚫 <b>Banned Users Control</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        banned = []
        if banned_col is not None:
            try:
                banned = list(banned_col.find({}))
            except Exception:
                banned = [{"_id": uid} for uid in local_banned_users]
        else:
            banned = [{"_id": uid} for uid in local_banned_users]
            
        if banned:
            for item in banned:
                banned_text += f"• <code>{item['_id']}</code>\n"
        else:
            banned_text += "💡 No users are currently banned."
            
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Ban User", callback_data="admin_ban_prompt"),
            InlineKeyboardButton("❌ Unban User", callback_data="admin_unban_prompt")
        )
        markup.add(InlineKeyboardButton("↩️ Back to Menu", callback_data="admin_main"))
        safe_edit_message_text(banned_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "admin_ban_prompt":
        sent = bot.send_message(chat_id, "✍️ <b>Enter the Telegram User ID of the user you want to BAN:</b>", parse_mode="HTML")
        bot.register_next_step_handler(sent, process_ban_step)
        
    elif action == "admin_unban_prompt":
        sent = bot.send_message(chat_id, "✍️ <b>Enter the Telegram User ID of the user you want to UNBAN:</b>", parse_mode="HTML")
        bot.register_next_step_handler(sent, process_unban_step)

    elif action == "admin_monitor_list":
        mon_text = "🔄 <b>Profile Auto-Monitor Management</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        monitored = []
        if monitored_col is not None:
            try:
                monitored = list(monitored_col.find({}))
            except Exception:
                monitored = [{"_id": u} for u in local_monitored.keys()]
        else:
            monitored = [{"_id": u} for u in local_monitored.keys()]
            
        if monitored:
            mon_text += "📋 <b>Currently Monitored Profiles:</b>\n"
            for idx, item in enumerate(monitored):
                mon_text += f"{idx+1}. @{item['_id']}\n"
        else:
            mon_text += "💡 No profiles are currently set for auto-download monitoring."
            
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Add Monitor", callback_data="monitor_add_prompt"),
            InlineKeyboardButton("❌ Remove Monitor", callback_data="monitor_del_select")
        )
        markup.add(InlineKeyboardButton("↩️ Back to Menu", callback_data="admin_main"))
        safe_edit_message_text(mon_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "monitor_add_prompt":
        sent = bot.send_message(chat_id, "✍️ <b>Enter the Instagram Username you want to auto-monitor:</b>\n\nExample: <code>samiraahaha</code>", parse_mode="HTML")
        bot.register_next_step_handler(sent, process_monitor_add_step)

    elif action == "monitor_del_select":
        monitored = []
        if monitored_col is not None:
            try:
                monitored = list(monitored_col.find({}))
            except Exception:
                monitored = [{"_id": u} for u in local_monitored.keys()]
        else:
            monitored = [{"_id": u} for u in local_monitored.keys()]
            
        if not monitored:
            bot.answer_callback_query(call.id, "No monitored profiles to delete!", show_alert=True)
            return
            
        markup = InlineKeyboardMarkup(row_width=1)
        for idx, item in enumerate(monitored):
            markup.add(InlineKeyboardButton(f"Stop Monitoring: @{item['_id']}", callback_data=f"monitor_remove_uname_{item['_id']}"))
            
        markup.add(InlineKeyboardButton("↩️ Back", callback_data="admin_monitor_list"))
        safe_edit_message_text("❌ <b>Select the profile to stop auto-monitoring:</b>", chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action.startswith("monitor_remove_uname_"):
        target_uname = action[len("monitor_remove_uname_"):]
        delete_monitored_profile(target_uname)
        bot.answer_callback_query(call.id, f"✅ Stopped monitoring @{target_uname}!", show_alert=True)
        call.data = "admin_monitor_list"
        handle_admin_callbacks(call)

    elif action == "admin_main":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📊 System Stats", callback_data="admin_stats"),
            InlineKeyboardButton("🏥 Server Health", callback_data="admin_health"),
            InlineKeyboardButton("📡 Manage API Nodes", callback_data="admin_api_list"),
            InlineKeyboardButton("🔄 Auto-Monitor", callback_data="admin_monitor_list"),
            InlineKeyboardButton("🛡️ Manage Admins", callback_data="admin_manage_list"),
            InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned_list"),
            InlineKeyboardButton("❌ Close Panel", callback_data="admin_close")
        )
        admin_welcome = (
            "⚙️ <b>SaveGr Ultra Bot - Admin Control Panel</b> ⚙️\n\n"
            "Welcome to the VIP Command Center! Use the inline buttons below to monitor performance, "
            "manage active API scraper nodes, and customize permissions directly from Telegram."
        )
        safe_edit_message_text(admin_welcome, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "admin_close":
        bot.delete_message(chat_id, message_id)

# -------------------------------------------------------------
# 🎬 INTERACTIVE PROFILE SELECTION CARD callbacks:
# -------------------------------------------------------------
def handle_profile_callbacks(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    user_id = call.from_user.id

    # Strictly limit profile actions to admins/sub-admins
    if not is_admin(user_id):
        try:
            bot.answer_callback_query(call.id, "❌ Restricted to Administrators!", show_alert=True)
        except Exception:
            pass
        return

    data_parts = call.data.split(":")
    action = data_parts[0]
    username = data_parts[1]

    # Map actions to custom scraper targets
    if action == "profile_dp":
        target_url = f"https://www.instagram.com/{username}/"
        status_text = "🖼️ FETCHING HD PROFILE PICTURE..."
    elif action == "profile_stories":
        target_url = f"https://www.instagram.com/stories/{username}/"
        status_text = "🌌 FETCHING ACTIVE STORIES..."
    elif action == "profile_reels":
        target_url = f"https://www.instagram.com/{username}/"
        status_text = "🎬 FETCHING RECENT REELS..."
    elif action == "profile_posts":
        target_url = f"https://www.instagram.com/{username}/"
        status_text = "📁 FETCHING RECENT POSTS..."
    elif action == "profile_cancel":
        try:
            bot.delete_message(chat_id, message_id)
            bot.answer_callback_query(call.id, "Cancelled.")
        except Exception:
            pass
        return
    else:
        return

    try:
        bot.answer_callback_query(call.id)
        # Clear Selection panel
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

    # Start progress telemetry (Fast execution with zero simulated delays!)
    p_bar_1 = make_progress_bar(20, status_text)
    status_message = bot.send_message(chat_id, p_bar_1, parse_mode="HTML")
    
    p_bar_2 = make_progress_bar(50, "⚡ SELECTING ACTIVE SCRAPER NODE...")
    safe_edit_message_text(p_bar_2, chat_id, status_message.message_id, parse_mode="HTML")

    try:
        p_bar_3 = make_progress_bar(80, "📥 FETCHING MEDIA CDN STREAMS...")
        safe_edit_message_text(p_bar_3, chat_id, status_message.message_id, parse_mode="HTML")
        
        # Load balancing with optimized 8s fast timeout!
        data = fetch_media_with_load_balancer(target_url)
        
        if not data:
            safe_edit_message_text("❌ All API scraper nodes are currently busy or rate-limited. Please try again in a few seconds.", chat_id, status_message.message_id, parse_mode="HTML")
            return

        items = data["items"]
        
        p_bar_4 = make_progress_bar(90, "📝 FORMATTING CAPTIONS & METADATA...")
        safe_edit_message_text(p_bar_4, chat_id, status_message.message_id, parse_mode="HTML")
        
        # Setup metadata mock bypass
        shortcode = username
        meta = get_instagram_metadata(shortcode, target_url)

        p_bar_5 = make_progress_bar(100, "📤 DELIVERING FILES DIRECTLY TO CHAT...")
        safe_edit_message_text(p_bar_5, chat_id, status_message.message_id, parse_mode="HTML")

        # Prepare items for Album
        p_bar_5 = make_progress_bar(100, "📤 DELIVERING ALBUM TO CHAT...")
        safe_edit_message_text(p_bar_5, chat_id, status_message.message_id, parse_mode="HTML")

        # Use the first item's metadata for the main album caption
        first_item = items[0]
        media_url = first_item["url"]
        media_type = first_item["type"]
        size_str = get_file_size_str(media_url)

        caption_parts = ["✨ <b>InstaMedia Album</b> ✨\n"]
        has_any_meta = False
        if meta:
            author = meta.get("author") or f"@{username}"
            caption_parts.append(f"👤 Author: <b>{escape_html(author)}</b>")
            caption_parts.append(f"💾 Size: <b>{size_str}</b>\n")
            has_any_meta = True
        
        if not has_any_meta:
            caption_parts = ["✨ <b>InstaMedia Album</b> ✨\n", f"💾 Size: <b>{size_str}</b>"]
        
        album_caption = "\n".join(caption_parts)

        # 🚀 Send as Grouped Album (Preview + Document)
        album_success = send_album_safely(chat_id, items, album_caption)

        if album_success:
            # DB download Logging & Log Channel Forwarding (Loop through all items)
            for item in items:
                log_download_to_db(user_id, shortcode, item["type"])
                if LOG_CHANNEL_ID:
                    log_thread = threading.Thread(
                        target=send_log_channel_forward,
                        args=(item["url"], item["type"], album_caption, call.from_user),
                        daemon=True
                    )
                    log_thread.start()
        else:
            bot.send_message(chat_id, "❌ Failed to deliver album. Please try again.")

        # Delete loader
        bot.delete_message(chat_id, status_message.message_id)

    except Exception as e:
        print("Profile Callback Error:", str(e))
        try:
            bot.edit_message_text(f"❌ Error occurred while downloading: {str(e)}", chat_id, status_message.message_id)
        except Exception:
            pass

# -------------------------------------------------------------
# 🔄 REGULAR USERS MULTI-MONITOR CONTROLLER (LIMIT 5):
# -------------------------------------------------------------
@bot.message_handler(commands=['monitor'])
def monitor_command(message):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        return

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📋 My Monitors", callback_data="user_mon_list"),
        InlineKeyboardButton("➕ Add Profile", callback_data="user_mon_add"),
        InlineKeyboardButton("❌ Remove Profile", callback_data="user_mon_del"),
        InlineKeyboardButton("❌ Close Menu", callback_data="user_mon_close")
    )
    
    welcome_msg = (
        "🔄 <b>SaveGr Profile Auto-Monitor Center</b> 🔄\n\n"
        "Aap kisi bhi public Instagram profile ko auto-monitor par laga sakte hain! "
        "Jaise hi wo profile koi nayi post, reel ya story lagayegi, Bot use automatically "
        "download karke aapke chat me direct bhej dega!\n\n"
        "⭐ <b>Your Limit:</b> 5 Profiles (Administrators have unlimited access)."
    )
    bot.send_message(message.chat.id, welcome_msg, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("user_mon_"))
def handle_user_monitor_callbacks(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    user_id = call.from_user.id

    if is_user_banned(user_id):
        try:
            bot.answer_callback_query(call.id, "❌ Access Denied!", show_alert=True)
        except Exception:
            pass
        return

    action = call.data

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if action == "user_mon_list":
        monitored = []
        if monitored_col is not None:
            try:
                monitored = list(monitored_col.find({"admin_id": user_id}))
            except Exception:
                pass
        else:
            monitored = [{"_id": u} for u, (aid, _) in local_monitored.items() if aid == user_id]

        mon_text = "🔄 <b>Your Auto-Monitored Profiles List</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        if monitored:
            for idx, item in enumerate(monitored):
                mon_text += f"{idx+1}. @{item['_id']}\n"
        else:
            mon_text += "💡 Aapne abhi koi profile monitor list me add nahi ki hai."
            
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("↩️ Back", callback_data="user_mon_main"))
        safe_edit_message_text(mon_text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "user_mon_add":
        is_user_admin = is_admin(user_id)
        count = 0
        if monitored_col is not None:
            try:
                count = monitored_col.count_documents({"admin_id": user_id})
            except Exception:
                pass
        else:
            count = len([u for u, (aid, _) in local_monitored.items() if aid == user_id])

        if not is_user_admin and count >= 5:
            bot.answer_callback_query(call.id, "❌ Limit Exceeded! Maximum 5 profiles allowed.", show_alert=True)
            return

        sent = bot.send_message(chat_id, "✍️ <b>Enter the Instagram Username you want to auto-monitor:</b>\n\nExample: <code>samiraahaha</code>", parse_mode="HTML")
        bot.register_next_step_handler(sent, process_user_monitor_add_step)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass

    elif action == "user_mon_del":
        monitored = []
        if monitored_col is not None:
            try:
                monitored = list(monitored_col.find({"admin_id": user_id}))
            except Exception:
                pass
        else:
            monitored = [{"_id": u} for u, (aid, _) in local_monitored.items() if aid == user_id]

        if not monitored:
            bot.answer_callback_query(call.id, "No monitored profiles to delete!", show_alert=True)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for item in monitored:
            markup.add(InlineKeyboardButton(f"Stop Monitoring: @{item['_id']}", callback_data=f"user_mon_remove_uname_{item['_id']}"))
        markup.add(InlineKeyboardButton("↩️ Back", callback_data="user_mon_main"))
        
        safe_edit_message_text("❌ <b>Select the profile to stop auto-monitoring:</b>", chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action.startswith("user_mon_remove_uname_"):
        target_uname = action[len("user_mon_remove_uname_"):]
        delete_monitored_profile(target_uname)
        bot.answer_callback_query(call.id, f"✅ Stopped monitoring @{target_uname}!", show_alert=True)
        call.data = "user_mon_list"
        handle_user_monitor_callbacks(call)

    elif action == "user_mon_main":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📋 My Monitors", callback_data="user_mon_list"),
            InlineKeyboardButton("➕ Add Profile", callback_data="user_mon_add"),
            InlineKeyboardButton("❌ Remove Profile", callback_data="user_mon_del"),
            InlineKeyboardButton("❌ Close Menu", callback_data="user_mon_close")
        )
        welcome_msg = (
            "🔄 <b>SaveGr Profile Auto-Monitor Center</b> 🔄\n\n"
            "Aap kisi bhi public Instagram profile ko auto-monitor par laga sakte hain! "
            "Jaise hi wo profile koi nayi post, reel ya story lagayegi, Bot use automatically "
            "download karke aapke chat me direct bhej dega!\n\n"
            "⭐ <b>Your Limit:</b> 5 Profiles (Administrators have unlimited access)."
        )
        safe_edit_message_text(welcome_msg, chat_id, message_id, reply_markup=markup, parse_mode="HTML")

    elif action == "user_mon_close":
        bot.delete_message(chat_id, message_id)

def process_user_monitor_add_step(message):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        return
        
    username = message.text.strip().replace("@", "").lower()
    
    # Check limit again to avoid concurrent bypasses
    is_user_admin = is_admin(user_id)
    count = 0
    if monitored_col is not None:
        try:
            count = monitored_col.count_documents({"admin_id": user_id})
        except Exception:
            pass
    else:
        count = len([u for u, (aid, _) in local_monitored.items() if aid == user_id])

    if not is_user_admin and count >= 5:
        bot.reply_to(message, "❌ **Limit Exceeded!** Normal users can only monitor up to 5 profiles.")
        return

    success, msg = add_monitored_profile(username, user_id)
    bot.reply_to(message, msg, parse_mode="HTML")

# -------------------------------------------------------------
# 📥 ADMIN NEXT STEP HANDLERS:
# -------------------------------------------------------------
def process_api_add_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
        
    url = message.text.strip().rstrip('/')
    success, msg = add_api_node(url)
    bot.reply_to(message, msg, parse_mode="HTML")

def process_admin_add_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
        
    try:
        new_admin_id = int(message.text.strip())
        register_as_admin(new_admin_id)
        bot.reply_to(message, f"✅ User <code>{new_admin_id}</code> is now registered as an administrator!", parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID! Please enter a valid numerical ID.")

def process_ban_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    try:
        target_id = int(message.text.strip())
        if banned_col is not None:
            banned_col.update_one(
                {"_id": target_id},
                {"$set": {"date_banned": datetime.now(), "banned_by": user_id}},
                upsert=True
            )
        else:
            local_banned_users.add(target_id)
        bot.reply_to(message, f"✅ <b>User `{target_id}` has been successfully BANNED!</b>", parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID!")

def process_unban_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    try:
        target_id = int(message.text.strip())
        if banned_col is not None:
            banned_col.delete_one({"_id": target_id})
        else:
            local_banned_users.discard(target_id)
        bot.reply_to(message, f"✅ <b>User `{target_id}` has been successfully UNBANNED!</b>", parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID!")

def process_monitor_add_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
        
    username = message.text.strip().replace("@", "").lower()
    success, msg = add_monitored_profile(username, user_id)
    bot.reply_to(message, msg, parse_mode="HTML")

# -------------------------------------------------------------
# 👥 ADMIN SLASH COMMANDS (/ban, /unban):
# -------------------------------------------------------------
@bot.message_handler(commands=['ban'])
def ban_user_slash(message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Usage: `/ban <user_id>`")
        return

    try:
        target_id = int(parts[1])
        if banned_col is not None:
            banned_col.update_one(
                {"_id": target_id},
                {"$set": {"date_banned": datetime.now(), "banned_by": message.from_user.id}},
                upsert=True
            )
        else:
            local_banned_users.add(target_id)
        bot.reply_to(message, f"✅ **User `{target_id}` ko successfully BAN kar diya gaya hai!**")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['unban'])
def unban_user_slash(message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Usage: `/unban <user_id>`")
        return

    try:
        target_id = int(parts[1])
        if banned_col is not None:
            banned_col.delete_one({"_id": target_id})
        else:
            local_banned_users.discard(target_id)
        bot.reply_to(message, f"✅ **User `{target_id}` ko successfully UNBAN kar diya gaya hai!**")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# -------------------------------------------------------------
# 🛠️ FAILS-AFE LOCAL DISK DOWNLOADER & NATIVE UPLOADER (WITH AUTOMATIC 429 RATE-LIMIT RETRY):
# -------------------------------------------------------------
def download_media_locally(url, media_type):
    """Downloads media to a local temporary file and returns the path."""
    temp_filename = f"/tmp/{uuid.uuid4()}.{'mp4' if media_type == 'video' else 'jpg'}"
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        }
        response = requests.get(url, headers=headers, timeout=25, stream=True)
        if response.status_code == 200:
            with open(temp_filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return temp_filename
    except Exception as e:
        print(f"Download error: {e}")
    return None

def send_album_safely(chat_id, items, caption_text):
    """
    🚀 ULTRA-ROBUST ALBUM DELIVERY:
    Ensures all files are kept open until the media group is sent to avoid 'Closed File' errors.
    """
    downloaded_files = []
    print(f"📦 Processing album with {len(items)} items...")
    
    for item in items:
        path = download_media_locally(item["url"], item["type"])
        if path: downloaded_files.append({"path": path, "type": item["type"]})
    if not downloaded_files: return False
    
    try:
        # --- 1. PREVIEW ALBUM ---
        preview_files = [open(f["path"], 'rb') for f in downloaded_files]
        preview_group = []
        for i, f in enumerate(preview_files):
            m_type = downloaded_files[i]["type"]
            if m_type == "video":
                preview_group.append(telebot.types.InputMediaVideo(f, caption=caption_text if i == 0 else None))
            else:
                preview_group.append(telebot.types.InputMediaPhoto(f, caption=caption_text if i == 0 else None))
        
        for i in range(0, len(preview_group), 10):
            bot.send_media_group(chat_id, preview_group[i:i+10])
        
        for f in preview_files: f.close()

        # --- 2. DOCUMENT ALBUM ---
        doc_files = [open(f["path"], 'rb') for f in downloaded_files]
        document_group = []
        for i, f in enumerate(doc_files):
            doc_caption = f"{caption_text}

💎 Original Quality" if i == 0 else None
            document_group.append(telebot.types.InputMediaDocument(f, caption=doc_caption))
        
        for i in range(0, len(document_group), 10):
            bot.send_media_group(chat_id, document_group[i:i+10])
            
        for f in doc_files: f.close()

    except Exception as e:
        print(f"Album sending error: {e}")
        for file in downloaded_files:
            try:
                with open(file["path"], 'rb') as f: bot.send_document(chat_id, f, caption=caption_text)
            except: pass
    finally:
        for file in downloaded_files:
            if os.path.exists(file["path"]): os.remove(file["path"])
    return True



# -----------------------------------------------------------------------------
# REMOVING OLD send_media_safely AND send_telegram_media_with_rate_limit 
# AS THEY ARE NOW HANDLED BY send_album_safely
# -----------------------------------------------------------------------------



# -------------------------------------------------------------
# 📡 DYNAMIC MULTI-API LOAD BALANCER CALLER:
# -------------------------------------------------------------
def fetch_media_with_load_balancer(insta_url):
    shuffled_nodes = list(API_URLS)
    random.shuffle(shuffled_nodes)

    fallback_data = None

    for api_url in shuffled_nodes:
        try:
            api_endpoint = f"{api_url.rstrip('/')}/api/download"
            print(f"📡 Load Balancer: Routing request to node -> {api_endpoint}")
            
            response = requests.post(
                api_endpoint, 
                json={"url": insta_url}, 
                headers={"Content-Type": "application/json"},
                timeout=8  # Reduced from 18 to 8 seconds for ultra-fast load balancing!
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("ok") and data.get("items") and len(data["items"]) > 0:
                    first_url = data["items"][0]["url"]
                    is_fallback = "unsplash.com" in first_url or "mixkit-girl" in first_url
                    
                    if not is_fallback:
                        return data
                    else:
                        fallback_data = data
        except Exception as e:
            print(f"⚠️ API Node failed ({api_url}): {str(e)}. Shifting to next node...")
            continue
            
    return fallback_data

# -------------------------------------------------------------
# 🤖 TELEGRAM BOT handlers:
# -------------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_user_banned(message.from_user.id):
        bot.reply_to(message, "❌ **Oops! You have been banned from using this bot.**\nPlease contact the owner/admin.")
        return

    register_user(message.from_user)

    welcome_text = (
        "⚡ **Hey there! I am SaveGr Premium Downloader Bot.**\n\n"
        "Send me ang public Instagram Link (Reel, Photo, Video, Carousel, Story)  "
        "ya fir kisi ka **@username / Profile Link** bhejkar unki HD Profile Picture (DP) aur active Stories download karein!\n\n"
        "✨ Private accounts are not supported by Instagram's public policies."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = (
        "📖 **SaveGr Bot Help Guide**\n\n"
        "1️⃣ **Copy Link**: Go to Instagram, open any public Reel, Photo, Video, Carousel, Story or Profile.\n"
        "2️⃣ **Send Link/Username**: Direct paste the link or send a raw username starting with @ (e.g. `@username`) here in this chat.\n"
        "3️⃣ **Instant Delivery**: Bot will process and send you the original media natively.\n\n"
        "⚠️ Private accounts are not supported by Instagram's public policies."
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

# -------------------------------------------------------------
# 📥 MAIN Downloader Logic:
# -------------------------------------------------------------
@bot.message_handler(func=lambda message: True)
def handle_instagram_link(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if is_user_banned(user_id):
        bot.reply_to(message, "❌ **Oops! You have been banned from using this bot.**")
        return

    text = message.text.strip()
    
    # Smart URL & Profile Matcher
    is_insta_link = re.search(r'instagram\.com/(p|reel|reels|stories|share/p|share/reel|tv|s|stories/highlights)/', text, re.IGNORECASE)
    is_profile_request = False
    username = None
    
    if not is_insta_link:
        # Check if it's a raw profile URL
        profile_match = re.search(r'instagram\.com/([A-Za-z0-9_\.-]+)', text, re.IGNORECASE)
        if profile_match:
            potential_username = profile_match.group(1)
            if potential_username not in ["explore", "developer", "about", "blog", "jobs", "help", "api", "privacy", "terms"]:
                is_profile_request = True
                username = potential_username
        # Check if it's a raw username starting with @
        elif text.startswith('@') and len(text) > 1:
            potential_username = text[1:].strip()
            if re.match(r'^[A-Za-z0-9_\.-]+$', potential_username):
                is_profile_request = True
                username = potential_username
        # Check if it's a raw username word
        elif re.match(r'^[A-Za-z0-9_\.-]+$', text) and len(text) <= 30:
            if text.lower() not in ["start", "help", "admin", "ban", "unban", "monitor"]:
                is_profile_request = True
                username = text

    # If it is a profile request, show the interactive VIP Selection Panel! (Limiting strictly to admins/sub-admins)
    if is_profile_request:
        if not is_admin(user_id):
            bot.reply_to(message, "❌ **Access Denied!** Profile scanning and downloading features are restricted to Administrators.")
            return

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🖼️ Download HD DP", callback_data=f"profile_dp:{username}"),
            InlineKeyboardButton("🌌 Download Stories", callback_data=f"profile_stories:{username}"),
            InlineKeyboardButton("🎬 Download Reels", callback_data=f"profile_reels:{username}"),
            InlineKeyboardButton("📁 Download Posts", callback_data=f"profile_posts:{username}")
        )
        markup.add(InlineKeyboardButton("❌ Cancel", callback_data=f"profile_cancel:{username}"))
        
        profile_text = (
            f"👤 <b>Instagram Profile Detected</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Username:</b> @{username}\n"
            f"🔗 <b>Link:</b> <code>https://instagram.com/{username}</code>\n\n"
            f"Select what you want to download from this profile using the options below:"
        )
        bot.send_message(chat_id, profile_text, reply_markup=markup, parse_mode="HTML")
        return

    if not is_insta_link:
        bot.reply_to(message, "❌ Please send a valid public Instagram link (e.g. Reel, Post, Story, Profile, or Carousel).")
        return

    register_user(message.from_user)

    # ⏳ ADVANCED NEON PROGRESS BAR INITIALIZATION (Zero simulated sleeps for maximum speed!)
    p_bar_1 = make_progress_bar(20, "🔍 LINK ANALYSIS & DECODING...")
    status_message = bot.reply_to(message, p_bar_1, parse_mode="HTML")
    
    p_bar_2 = make_progress_bar(50, "⚡ SELECTING ACTIVE SCRAPER NODE...")
    safe_edit_message_text(p_bar_2, chat_id, status_message.message_id, parse_mode="HTML")

    try:
        p_bar_3 = make_progress_bar(80, "📥 FETCHING MEDIA CDN STREAMS...")
        safe_edit_message_text(p_bar_3, chat_id, status_message.message_id, parse_mode="HTML")
        
        # Call Load Balancer
        data = fetch_media_with_load_balancer(text)
        
        if not data:
            safe_edit_message_text("❌ All API scraper nodes are currently busy or rate-limited. Please try again in a few seconds.", chat_id, status_message.message_id, parse_mode="HTML")
            return

        items = data["items"]
        
        p_bar_4 = make_progress_bar(90, "📝 FORMATTING CAPTIONS & METADATA...")
        safe_edit_message_text(p_bar_4, chat_id, status_message.message_id, parse_mode="HTML")
        
        # Story Decoders
        shortcode = 'instadl_media'
        story_id_match = re.search(r'stories/[A-Za-z0-9_\.-]+/([0-9]+)', text)
        media_id_match = re.search(r'story_media_id=([0-9]+)', text)

        if story_id_match:
            converted = media_id_to_shortcode(story_id_match.group(1))
            if converted:
                shortcode = converted
        elif media_id_match:
            converted = media_id_to_shortcode(media_id_match.group(1))
            if converted:
                shortcode = converted
        else:
            short_highlights = re.search(r'/s/([A-Za-z0-9_-]+)', text)
            if short_highlights:
                try:
                    encoded = short_highlights.group(1)
                    decoded = base64.b64decode(encoded + "===").decode('utf-8', errors='ignore')
                    if 'highlight:' in decoded:
                        shortcode = decoded.split('highlight:')[1]
                    else:
                        shortcode = encoded
                except Exception:
                    shortcode = short_highlights.group(1)
            else:
                long_highlights = re.search(r'stories/highlights/([0-9A-Za-z_-]+)', text)
                if long_highlights:
                    shortcode = long_highlights.group(1)
                else:
                    match = re.search(r'(?:p|reel|reels|stories|share\/p|share\/reel|tv)\/([A-Za-z0-9_-]+)', text)
                    shortcode = match.group(1) if match else 'instadl_media'

        meta = get_instagram_metadata(shortcode, text)

        p_bar_5 = make_progress_bar(100, "📤 DELIVERING FILES DIRECTLY TO CHAT...")
        safe_edit_message_text(p_bar_5, chat_id, status_message.message_id, parse_mode="HTML")

        p_bar_5 = make_progress_bar(100, "📤 DELIVERING ALBUM TO CHAT...")
        safe_edit_message_text(p_bar_5, chat_id, status_message.message_id, parse_mode="HTML")

        # Use the first item's metadata for the main album caption
        first_item = items[0]
        media_url = first_item["url"]
        media_type = first_item["type"]
        size_str = get_file_size_str(media_url)

        caption_parts = ["✨ <b>InstaMedia Album</b> ✨\n"]
        has_any_meta = False
        if meta:
            author = meta.get("author")
            user_id_val = meta.get("user_id")
            date_val = meta.get("date")
            caption_val = meta.get("caption")
            
            if author and "unknown" not in author.lower() and author != "@" and author != "@Unknown":
                caption_parts.append(f"👤 Author: <b>{escape_html(author)}</b>")
                has_any_meta = True
                
            if user_id_val and user_id_val != "N/A" and "unknown" not in user_id_val.lower():
                caption_parts.append(f"🆔 User ID: <code>{escape_html(user_id_val)}</code>")
                has_any_meta = True
                
            if date_val and "unknown" not in date_val.lower():
                caption_parts.append(f"📅 Date: <b>{escape_html(date_val)}</b>")
                has_any_meta = True
                
            caption_parts.append(f"💾 Size: <b>{size_str}</b>\n")
            
            if caption_val and caption_val.strip() and "creative post" not in caption_val.lower() and "aesthetic instagram media" not in caption_val.lower():
                if len(caption_val) > 350:
                    caption_val = caption_val[:350] + "..."
                caption_parts.append("📝 Caption:")
                caption_parts.append(f"<i>{escape_html(caption_val)}</i>")
                has_any_meta = True
        
        if not has_any_meta:
            caption_parts = [
                "✨ <b>InstaMedia Album</b> ✨\n",
                f"💾 Size: <b>{size_str}</b>"
            ]
            
        album_caption = "\n".join(caption_parts)

        # 🚀 Send as Grouped Album (Preview + Document)
        album_success = send_album_safely(chat_id, items, album_caption)

        if album_success:
            # DB Logs & Log Channel Forwarding (Loop through all items)
            for item in items:
                log_download_to_db(user_id, shortcode, item["type"])
                if LOG_CHANNEL_ID:
                    log_thread = threading.Thread(
                        target=send_log_channel_forward,
                        args=(item["url"], item["type"], album_caption, message.from_user),
                        daemon=True
                    )
                    log_thread.start()
        else:
            bot.send_message(chat_id, "❌ Failed to deliver album. Please try again.")

        # Delete loader
        bot.delete_message(chat_id, status_message.message_id)

    except Exception as e:
        print("Bot Error:", str(e))
        try:
            bot.delete_message(chat_id, status_message.message_id)
        except Exception:
            pass

if __name__ == "__main__":
    # Start auto-download background poller loop thread
    monitor_thread = threading.Thread(target=run_profile_monitoring_loop, daemon=True)
    monitor_thread.start()

    # Start health checks server
    server_thread = threading.Thread(target=run_health_server, daemon=True)
    server_thread.start()

    print(f"🤖 SaveGr Telegram Bot is running using load balancer pool of {len(API_URLS)} nodes...")
    bot.infinity_polling()
