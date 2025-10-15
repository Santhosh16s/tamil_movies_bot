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

# =========================
# --- Section 3: User Tracking & Database Operations ---
# =========================

# --- பயனரை Supabase-இல் பதிவு செய்தல் ---
async def பயனரை_பதிவு_செய்(user: telegram.User) -> None:
    """
    புதிய பயனரை Supabase database-இல் பதிவு செய்கிறது.
    """
    try:
        user_exists = supabase.table("users").select("*").eq("user_id", user.id).execute()
        if user_exists.data:
            logger.debug(f"User {user.id} ஏற்கனவே பதிவு செய்யப்பட்டுள்ளார்.")
            return

        data = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "joined_at": datetime.utcnow().isoformat()
        }
        supabase.table("users").insert(data).execute()
        logger.info(f"✅ புதிய பயனர் {user.id} Supabase-இல் பதிவு செய்யப்பட்டது.")
    except Exception as e:
        logger.error(f"❌ பயனரை {user.id} பதிவு செய்ய முடியவில்லை: {e}")


# --- பயனர் சந்திப்பு பதிப்பித்தல் (Message Tracker) ---
async def பதிவுகளை_பார்த்து_பதிவு_செய்(update: Update) -> None:
    """
    ஒவ்வொரு message வந்தவுடன் பயனரை பதிவு செய்து, message log செய்வது.
    """
    user = update.effective_user
    await பயனரை_பதிவு_செய்(user)

    try:
        data = {
            "user_id": user.id,
            "chat_id": update.effective_chat.id,
            "message_id": update.message.message_id,
            "text": update.message.text or "",
            "timestamp": datetime.utcnow().isoformat()
        }
        supabase.table("messages").insert(data).execute()
        logger.debug(f"Message {data['message_id']} logged for user {user.id}.")
    except Exception as e:
        logger.warning(f"❌ Message logging failed for user {user.id}: {e}")


# --- திரைப்படத்தை Supabase-இல் சேமித்தல் ---
def save_movie_to_db(movie_title: str, file_urls: dict[str, str], poster_url: str) -> None:
    """
    புதிய திரைப்படத்தை Supabase-இல் சேர்க்கும்.
    file_urls = {'480p': url, '720p': url, '1080p': url}
    """
    try:
        cleaned_title = clean_title(movie_title)

        existing = supabase.table("movies").select("*").eq("title", movie_title).execute()
        if existing.data:
            logger.info(f"Movie '{movie_title}' ஏற்கனவே உள்ளதாக கண்டறியப்பட்டது.")
            return

        data = {
            "title": movie_title,
            "poster_url": poster_url,
            "file_480p": file_urls.get("480p", ""),
            "file_720p": file_urls.get("720p", ""),
            "file_1080p": file_urls.get("1080p", ""),
            "created_at": datetime.utcnow().isoformat()
        }
        supabase.table("movies").insert(data).execute()
        movies_cache[cleaned_title] = {
            "poster_url": poster_url,
            "files": file_urls
        }
        logger.info(f"✅ Movie '{movie_title}' successfully saved in database.")
    except Exception as e:
        logger.error(f"❌ Movie '{movie_title}' save failed: {e}")


# --- கோப்பை அனுப்புதல் (Send File) ---
async def கோப்பை_அனுப்பு(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    movie_title: str,
    resolution: str = "720p"
):
    """
    பயனருக்கு குறிப்பிட்ட resolution-இல் திரைப்பட கோப்பை அனுப்புகிறது.
    """
    cleaned_title = clean_title(movie_title)
    movie_data = movies_cache.get(cleaned_title)

    if not movie_data:
        await update.message.reply_text("❌ Sorry, திரைப்படம் கிடைக்கவில்லை.")
        logger.warning(f"Movie '{movie_title}' not found in cache.")
        return

    file_url = movie_data['files'].get(resolution)
    if not file_url:
        await update.message.reply_text(f"❌ Sorry, {resolution} resolution கிடைக்கவில்லை.")
        logger.warning(f"{resolution} file not found for '{movie_title}'.")
        return

    try:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=file_url,
            caption=f"🎬 {movie_title} ({resolution})"
        )
        logger.info(f"Sent '{movie_title}' [{resolution}] to user {update.effective_user.id}.")
    except Exception as e:
        await update.message.reply_text("❌ கோப்பை அனுப்ப முடியவில்லை.")
        logger.error(f"Failed to send '{movie_title}' [{resolution}] to user {update.effective_user.id}: {e}")


# --- அதிகபட்ச message வரிசை ---
MAX_RECENT_MESSAGES = 50
recent_messages: dict[int, list[int]] = {}  # chat_id -> list of message_ids


# --- Message history update ---
def update_recent_messages(chat_id: int, message_id: int):
    """
    ஒவ்வொரு புதிய message-ஐ recent_messages cache-இல் சேர்க்கிறது.
    """
    if chat_id not in recent_messages:
        recent_messages[chat_id] = []

    recent_messages[chat_id].append(message_id)
    if len(recent_messages[chat_id]) > MAX_RECENT_MESSAGES:
        removed = recent_messages[chat_id].pop(0)
        logger.debug(f"Removed old message {removed} from recent_messages cache for chat {chat_id}.")

# =========================
# --- Section 4: Command Handlers & Callbacks ---
# =========================

# --- /start கட்டளை ---
async def தொடங்கு(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start கட்டளை: பயனரை வரவேற்கிறது மற்றும் பதிவு செய்கிறது
    """
    user = update.effective_user
    await பயனரை_பதிவு_செய்(user)
    
    welcome_text = (
        f"வணக்கம் {user.first_name}!\n"
        "நான் தமிழ் திரைப்படங்களை அனுப்பும் Bot.\n"
        "நீங்கள் தேட /search கட்டளை பயன்படுத்தலாம்."
    )
    await update.message.reply_text(welcome_text)
    logger.info(f"/start executed by user {user.id}")


# --- /help கட்டளை ---
async def உதவி(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /help கட்டளை: Bot-ஐப் பயன்படுத்தும் வழிமுறைகள்
    """
    help_text = (
        "🎬 Tamil Movie Bot உதவி:\n\n"
        "/start - Bot-ஐ தொடங்கு\n"
        "/search <படத்தின் பெயர்> - திரைப்படம் தேடு\n"
        "Inline buttons மூலம் resolution தேர்வு செய்து கோப்பை பெறலாம்."
    )
    await update.message.reply_text(help_text)
    logger.info(f"/help executed by user {update.effective_user.id}")


# --- /search கட்டளை ---
async def தேடு(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /search <movie_name> கட்டளை: fuzzy search மூலம் திரைப்படம் தேடுகிறது
    """
    user_input = " ".join(context.args).strip()
    if not user_input:
        await update.message.reply_text("❌ தயவுசெய்து தேட விரும்பும் திரைப்படத்தின் பெயரை உள்ளிடவும்.")
        return

    cleaned_input = clean_title(user_input)
    results = fuzzy_search(cleaned_input, movies_cache.keys(), limit=5)

    if not results:
        await update.message.reply_text("❌ எந்த திரைப்படமும் காணப்படவில்லை.")
        return

    # Inline buttons உருவாக்குதல்
    buttons = []
    for title in results:
        buttons.append([InlineKeyboardButton(title, callback_data=f"movie|{title}")])

    reply_markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "📽️ பின்வரும் திரைப்படங்களில் ஒன்றைத் தேர்வு செய்யவும்:",
        reply_markup=reply_markup
    )
    logger.info(f"Search performed by user {update.effective_user.id} for '{user_input}'")


# --- Callback Query Handler ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inline button callback query கையாளும் function
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data.startswith("movie|"):
            movie_title = data.split("|")[1]
            # Resolution buttons
            buttons = [
                [
                    InlineKeyboardButton("480p", callback_data=f"resolution|{movie_title}|480p"),
                    InlineKeyboardButton("720p", callback_data=f"resolution|{movie_title}|720p"),
                    InlineKeyboardButton("1080p", callback_data=f"resolution|{movie_title}|1080p"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await query.edit_message_text(
                text=f"🎬 {movie_title} கோப்பை எது resolution-இல் வேண்டுமோ தேர்வு செய்க:",
                reply_markup=reply_markup
            )

        elif data.startswith("resolution|"):
            _, movie_title, resolution = data.split("|")
            await கோப்பை_அனுப்பு(update, context, movie_title, resolution)
    except Exception as e:
        logger.error(f"Callback handling failed: {e}")
        await query.edit_message_text("❌ தவறு ஏற்பட்டுள்ளது. தயவுசெய்து மீண்டும் முயற்சி செய்யவும்.")


# --- Fuzzy Search Utility ---
def fuzzy_search(query: str, choices: list[str], limit: int = 5) -> list[str]:
    """
    rapidfuzz-ஐ பயன்படுத்தி fuzzy search செய்து மேலான results திருப்புகிறது
    """
    results = process.extract(query, choices, limit=limit, score_cutoff=60)
    return [match[0] for match in results]


# --- Command Registration Function ---
def register_handlers(application: Application):
    """
    Bot-இல் அனைத்து command மற்றும் callback handlers பதிவு செய்கிறது
    """
    application.add_handler(CommandHandler("start", தொடங்கு))
    application.add_handler(CommandHandler("help", உதவி))
    application.add_handler(CommandHandler("search", தேடு))
    application.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("✅ Handlers registered successfully.")


# --- Admin-only Command Example ---
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only /stats command: பயனர்கள் மற்றும் messages பற்றிய தகவல்கள்
    """
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ நீங்கள் admin அல்ல.")
        return

    try:
        users_count = len(supabase.table("users").select("*").execute().data)
        messages_count = len(supabase.table("messages").select("*").execute().data)
        await update.message.reply_text(f"👤 Users: {users_count}\n💬 Messages: {messages_count}")
        logger.info(f"Admin {user_id} retrieved stats.")
    except Exception as e:
        logger.error(f"Failed to fetch stats for admin {user_id}: {e}")
        await update.message.reply_text("❌ Stats fetch failed.")
