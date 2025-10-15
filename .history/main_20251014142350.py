"""
தமிழ் திரைப்பட பாட் - தொழில்முறை பதிப்பு
Professional Tamil Movie Bot with Enterprise-Grade Architecture

இந்த பாட் Telegram மூலம் தமிழ் திரைப்படங்களை வழங்குகிறது.
Supabase database மற்றும் advanced fuzzy search-ஐ பயன்படுத்துகிறது.
"""

import logging
import asyncio
import nest_asyncio
import unicodedata
import re
import sys
import os
import time
from functools import wraps
from datetime import datetime, timezone

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from rapidfuzz import process
from supabase.client import create_client, Client
from dotenv import load_dotenv

# ------------------------- #
# நிலைமை மற்றும் Configuration
# ------------------------- #
nest_asyncio.apply()
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(funcName)s - %(message)s"
)

TOKEN = os.getenv("TOKEN")
PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SKMOVIES_GROUP_ID = int(os.getenv("SKMOVIES_GROUP_ID"))
SKMOVIESDISCUSSION_GROUP_ID = int(os.getenv("SKMOVIESDISCUSSION_GROUP_ID"))
MOVIE_UPDATE_CHANNEL_ID = int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))
MOVIE_UPDATE_CHANNEL_URL = PRIVATE_CHANNEL_LINK

admin_ids_str = os.getenv("ADMIN_IDS", "")
admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))

# ------------------------- #
# Supabase Client உருவாக்கல்
# ------------------------- #
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info(f"✅ Supabase Connected: {SUPABASE_URL}")
except Exception as e:
    logging.error(f"❌ Supabase client உருவாக்க முடியவில்லை: {e}")
    sys.exit(1)

# ------------------------- #
# நிலைமைச் சேமிப்பு
# ------------------------- #
user_files = {}          # Admins file upload tracking
pending_file_requests = {}  # Start payload tracking
pending_post = {}        # /post command temporary storage
movies_data = {}         # Cached movie data

# ------------------------- #
# உதவி செயல்பாடுகள்
# ------------------------- #
def restricted(func):
    """Admin-only commands."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in admin_ids:
            await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def extract_title(filename: str) -> str:
    """மூலப்பெயரிலிருந்து movie title எடுக்கிறது."""
    filename = re.sub(r"@\S+", "", filename)
    filename = re.sub(
        r"\b(480p|720p|1080p|x264|x265|HEVC|HDRip|WEBRip|AAC|10bit|DS4K|UNTOUCHED|mkv|mp4|HD|HQ|Tamil|Telugu|Hindi|English|Dubbed|Org|Original|Proper)\b",
        "", filename, flags=re.IGNORECASE
    )
    filename = re.sub(r"[\[\]\(\)\{\}]", " ", filename)
    filename = re.sub(r"\s+", " ", filename).strip()

    match = re.search(r"([a-zA-Z\s]+)(?:\(?)(20\d{2})(?:\)?)", filename)
    if match:
        return f"{match.group(1).strip()} ({match.group(2)})"

    title = re.split(r"[-0-9]", filename)[0].strip()
    return title

def clean_title(title: str) -> str:
    """Movie title-ஐ normalized செய்கிறது."""
    cleaned = title.lower()
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def time_diff(dt: datetime) -> str:
    """தற்போதைய நேரத்துடன் dt இடையிலான நேர வேறுபாடு."""
    now = datetime.now(timezone.utc)
    diff = now - dt.replace(tzinfo=timezone.utc)
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds} வினாடிகள் முன்பு"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} நிமிடங்கள் முன்பு"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} மணிநேரம் முன்பு"
    days = hours // 24
    return f"{days} நாட்கள் முன்பு"

async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 600):
    """நிரல்பூர்வமாக Message-ஐ குறிப்பிட்ட நேரத்திற்குப் பிறகு அழிக்கிறது."""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"Message {message_id} in chat {chat_id} deleted after delay.")
    except Exception as e:
        logging.warning(f"Error deleting message {message_id} in chat {chat_id}: {e}")

# ------------------------- #
# Database Operations
# ------------------------- #
def படங்களை_ஏற்று() -> dict:
    """Supabase-இலிருந்து Movies தரவை ஏற்றுகிறது."""
    try:
        response = supabase.table("movies").select("*").execute()
        movies = response.data or []
        movies_data_local = {}
        for movie in movies:
            cleaned_title_key = clean_title(movie['title'])
            movies_data_local[cleaned_title_key] = {
                'poster_url': movie['poster_url'],
                'files': {
                    '480p': movie.get('file_480p'),
                    '720p': movie.get('file_720p'),
                    '1080p': movie.get('file_1080p'),
                }
            }
        logging.info(f"✅ {len(movies_data_local)} திரைப்படங்கள் ஏற்றப்பட்டன.")
        return movies_data_local
    except Exception as e:
        logging.error(f"❌ திரைப்பட தரவை ஏற்ற முடியவில்லை: {e}")
        return {}

def save_movie_to_db(title: str, poster_id: str, file_ids: list) -> bool:
    """Movie-ஐ Supabase-ல் சேமிக்கிறது."""
    try:
        cleaned_title_for_db = clean_title(title)
        data = {
            "title": cleaned_title_for_db,
            "poster_url": poster_id,
            "file_480p": file_ids[0] if len(file_ids) > 0 else None,
            "file_720p": file_ids[1] if len(file_ids) > 1 else None,
            "file_1080p": file_ids[2] if len(file_ids) > 2 else None,
            "uploaded_at": datetime.utcnow().isoformat()
        }
        response = supabase.table("movies").insert(data).execute()
        if response.data:
            logging.info(f"✅ திரைப்படம் '{cleaned_title_for_db}' சேமிக்கப்பட்டது.")
            return True
        logging.error(f"❌ Insert தோல்வியடைந்தது: {getattr(response, 'error', 'Unknown')}")
        return False
    except Exception as e:
        logging.error(f"❌ save_movie_to_db பிழை: {e}")
        return False

# ------------------------- #
# User Management
# ------------------------- #
async def பயனரை_பதிவு_செய்(user: telegram.User):
    """User-ஐ Database-இல் பதிவு செய்கிறது அல்லது message_count-ஐ புதுப்பிக்கிறது."""
    try:
        response = supabase.table("users").select("user_id, message_count").eq("user_id", user.id).limit(1).execute()
        if not response.data:
            data = {
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "joined_at": datetime.utcnow().isoformat(),
                "message_count": 0
            }
            supabase.table("users").insert(data).execute()
            logging.info(f"✅ புதிய பயனர் பதிவு செய்யப்பட்டது: {user.id}")
        else:
            count = response.data[0].get("message_count", 0) + 1
            supabase.table("users").update({"message_count": count}).eq("user_id", user.id).execute()
            logging.info(f"பயனர் {user.id} மெசேஜ் கவுண்ட் புதுப்பிக்கப்பட்டது: {count}")
    except Exception as e:
        logging.error(f"❌ பயனர் பதிவு பிழை: {e}")

# ------------------------- #
# Fuzzy Search
# ------------------------- #
def தேடு(query: str) -> dict:
    """RapidFuzz fuzzy search மூலம் movie தேடுகிறது."""
    if not movies_data:
        logging.warning("Movies data இல்லை, ஏற்றுகிறது...")
        global movies_data
        movies_data = படங்களை_ஏற்று()
    cleaned_query = clean_title(query)
    if not movies_data:
        return {}

    choices = list(movies_data.keys())
    results = process.extract(cleaned_query, choices, limit=5, score_cutoff=60)
    if results:
        matched_key = results[0][0]
        return {matched_key: movies_data[matched_key]}
    return {}

# ------------------------- #
# Bot Command Handlers
# ------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await பயனரை_பதிவு_செய்(user)
    text = "வணக்கம்! உங்கள் தமிழ் திரைப்பட பயணத்தை தொடங்குங்கள் 🎬"
    await update.message.reply_text(text)

# ------------------------- #
# Main Function
# ------------------------- #
async def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Command Handlers
    application.add_handler(CommandHandler("start", start))

    # Message Handlers
    # Add more handlers for movies, callbacks, admin commands, etc.

    logging.info("✅ Bot தொடங்கியுள்ளேன்")
    await application.start()
    await application.updater.start_polling()
    await application.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
