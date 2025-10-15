"""
தமிழ் திரைப்பட பாட் - தொழில்முறை பதிப்பு
Professional Tamil Movie Bot with Enterprise-Grade Architecture

இது Telegram மூலம் 2025 HD தமிழ் திரைப்படங்களை வழங்கும் பாட்டாகும்.
Supabase Database மற்றும் fuzzy search-ஐ பயன்படுத்துகிறது.
"""

import os
import sys
import re
import time
import asyncio
import logging
import unicodedata
from functools import wraps
from datetime import datetime, timezone

import nest_asyncio
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

from dotenv import load_dotenv
from rapidfuzz import process
from supabase.client import create_client, Client

# --- Apply nest_asyncio for running in nested loops (e.g., Jupyter, certain servers) ---
nest_asyncio.apply()

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(funcName)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()

TOKEN: str = os.getenv("TOKEN")
if not TOKEN:
    logger.critical("❌ Telegram TOKEN not set in environment.")
    sys.exit(1)

ADMIN_IDS_STR: str = os.getenv("ADMIN_IDS", "")
admin_ids: set[int] = set(map(int, filter(None, ADMIN_IDS_STR.split(","))))

PRIVATE_CHANNEL_LINK: str = os.getenv("PRIVATE_CHANNEL_LINK", "")
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

# Group / Channel IDs
try:
    SKMOVIES_GROUP_ID: int = int(os.getenv("SKMOVIES_GROUP_ID"))
    SKMOVIESDISCUSSION_GROUP_ID: int = int(os.getenv("SKMOVIESDISCUSSION_GROUP_ID"))
    MOVIE_UPDATE_CHANNEL_ID: int = int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))
except Exception as e:
    logger.critical(f"❌ Invalid group/channel IDs: {e}")
    sys.exit(1)

MOVIE_UPDATE_CHANNEL_URL: str = PRIVATE_CHANNEL_LINK  # Reuse private channel link

# --- Supabase Client Initialization ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info(f"✅ Supabase client initialized: URL={SUPABASE_URL}, KEY={SUPABASE_KEY[:5]}***")
except Exception as e:
    logger.critical(f"❌ Supabase client initialization failed: {e}")
    sys.exit(1)

# --- Global State ---
user_files: dict[int, dict] = {}           # Temporary storage for /addmovie workflow
pending_file_requests: dict[int, dict] = {}  # Pending file requests per user
pending_post: dict[int, dict] = {}          # Pending /post messages per user
movies_data: dict[str, dict] = {}           # Cached movies from Supabase

logger.info("🚀 Initial configuration and environment loaded successfully.")

# =========================
# --- Section 2: Utilities & Helpers ---
# =========================

# --- திரைக்கதை பெயரை Extract செய்வது ---
def extract_title(filename: str) -> str:
    """
    கோப்பின் பெயரிலிருந்து திரைப்படத்தின் தலைப்பை எடுக்கிறது.
    """
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

    # Fallback: Take first part before dash or number
    title = re.split(r"[-0-9]", filename)[0].strip()
    return title


# --- தலைப்பை சுத்தப்படுத்துதல் ---
def clean_title(title: str) -> str:
    """
    Title-ஐ lowercase மற்றும் special characters இல்லாமல் மாற்றுகிறது.
    """
    cleaned = title.lower()
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


# --- Supabase-இலிருந்து திரைப்படங்களை ஏற்றுவது ---
def load_movies_data() -> dict[str, dict]:
    """
    Supabase-இலிருந்து அனைத்து திரைப்படங்களையும் ஏற்றிக் cache செய்யும்.
    """
    try:
        response = supabase.table("movies").select("*").execute()
        movies = response.data or []
        movies_cache = {}

        for movie in movies:
            cleaned_title = clean_title(movie['title'])
            movies_cache[cleaned_title] = {
                'poster_url': movie['poster_url'],
                'files': {
                    '480p': movie['file_480p'],
                    '720p': movie['file_720p'],
                    '1080p': movie['file_1080p'],
                }
            }
        logger.info(f"✅ {len(movies_cache)} திரைப்படங்கள் Supabase-இலிருந்து ஏற்றப்பட்டன.")
        return movies_cache
    except Exception as e:
        logger.error(f"❌ Supabase இலிருந்து திரைப்படத் தரவைப் பதிவேற்ற முடியவில்லை: {e}")
        return {}


# --- Restricted decorator (Admin-only commands) ---
def restricted(func):
    """
    Admin-மட்டுமே இந்த function-ஐ இயக்க அனுமதிக்கிறது.
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in admin_ids:
            await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped


# --- நேரம் வித்தியாசம் காட்டும் உதவி ---
def time_diff(dt: datetime) -> str:
    """
    datetime இருந்து தற்போது வரை வித்தியாசத்தை வாசகமான வடிவில் காட்டுகிறது.
    """
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


# --- 10 நிமிடங்களில் Message-ஐ delete செய்யும் async function ---
async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 600):
    """
    குறிப்பிட்ட காலத்திற்குப் பிறகு Message-ஐ நீக்கும்.
    Default: 600 seconds (10 நிமிடங்கள்)
    """
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Message {message_id} in chat {chat_id} deleted after {delay} seconds.")
    except Exception as e:
        logger.warning(f"Error deleting message {message_id} in chat {chat_id}: {e}")


# --- Fuzzy search helper ---
def fuzzy_search_movie(query: str, movies_list: list[str], threshold: int = 80, limit: int = 5) -> list[tuple[str, int]]:
    """
    RapidFuzz மூலம் search செய்கிறது, score_cutoff-ஐ பயன்படுத்தி relevant results தருகிறது.
    """
    return process.extract(query, movies_list, score_cutoff=threshold, limit=limit)
