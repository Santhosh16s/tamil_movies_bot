import os
import re
import sys
import time
import asyncio
import logging
import unicodedata
from functools import wraps
from datetime import datetime, timezone

import nest_asyncio
from dotenv import load_dotenv
from rapidfuzz import process
from supabase.client import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ----------------------------------------
# INITIAL SETUP
# ----------------------------------------
load_dotenv()
nest_asyncio.apply()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

TOKEN = os.getenv("TOKEN")
admin_ids_str = os.getenv("ADMIN_IDS", "")
admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))

PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SKMOVIES_GROUP_ID = int(os.getenv("SKMOVIES_GROUP_ID"))
SKMOVIESDISCUSSION_GROUP_ID = int(os.getenv("SKMOVIESDISCUSSION_GROUP_ID"))
MOVIE_UPDATE_CHANNEL_ID = int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info(f"✅ Supabase client initialized")
except Exception as e:
    logging.error(f"❌ Failed to create Supabase client: {e}")
    sys.exit(1)

# Temporary in-memory storage for ongoing actions
user_files = {}
pending_file_requests = {}
pending_post = {}  # user_id -> {'message': Message, 'task': asyncio.Task}
movies_data = {}  # Cleaned title -> movie dict


# ----------------------------------------
# UTILITIES
# ----------------------------------------
def restricted(func):
    """Decorator to restrict command access to admins only."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id not in admin_ids:
            await update.message.reply_text("❌ இந்த command admins மட்டுமே பயன்படுத்த முடியும்")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped


def clean_title(title: str) -> str:
    """Normalize and clean movie title for search and DB storage."""
    cleaned = title.lower()
    cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
    cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def extract_title(filename: str) -> str:
    """Extract a clean movie title from a filename."""
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
    return re.split(r"[-0-9]", filename)[0].strip()


def time_diff(dt: datetime) -> str:
    """Return human-readable time difference."""
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


async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay=600):
    """Delete a Telegram message after a delay."""
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.warning(f"Failed to delete message {message_id} in chat {chat_id}: {e}")


async def is_user_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is subscribed to the update channel."""
    try:
        status = await context.bot.get_chat_member(MOVIE_UPDATE_CHANNEL_ID, user_id=user_id)
        return status.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Subscription check failed: {e}")
        return False


# ----------------------------------------
# DATABASE FUNCTIONS
# ----------------------------------------
def load_movies_data() -> dict:
    """Load movies from Supabase and cache them in-memory."""
    try:
        response = supabase.table("movies").select("*").execute()
        movies = response.data or []
        data = {}
        for movie in movies:
            key = clean_title(movie['title'])
            data[key] = {
                "poster_url": movie['poster_url'],
                "files": {
                    "480p": movie.get("file_480p"),
                    "720p": movie.get("file_720p"),
                    "1080p": movie.get("file_1080p")
                }
            }
        logging.info(f"✅ Loaded {len(data)} movies from Supabase")
        return data
    except Exception as e:
        logging.error(f"Failed to load movies: {e}")
        return {}


def save_movie_to_db(title: str, poster_id: str, file_ids: list) -> bool:
    """Insert a movie record in Supabase."""
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
            logging.info(f"✅ Movie '{cleaned_title_for_db}' saved to DB")
            return True
        logging.error(f"Failed to insert movie: {response.error if hasattr(response, 'error') else 'Unknown error'}")
        return False
    except Exception as e:
        logging.error(f"DB insert error: {e}")
        return False


async def track_user(user):
    """Insert or update user message count."""
    user_id = user.id
    try:
        resp = supabase.table("users").select("user_id, message_count").eq("user_id", user_id).limit(1).execute()
        if not resp.data:
            user_data = {
                "user_id": user_id,
                "username": getattr(user, "username", None),
                "first_name": getattr(user, "first_name", None),
                "last_name": getattr(user, "last_name", None),
                "joined_at": datetime.utcnow().isoformat(),
                "message_count": 1
            }
            supabase.table("users").insert(user_data).execute()
            logging.info(f"✅ New user {user_id} registered")
        else:
            count = resp.data[0].get("message_count", 0) + 1
            supabase.table("users").update({"message_count": count}).eq("user_id", user_id).execute()
            logging.info(f"User {user_id} message count updated: {count}")
    except Exception as e:
        logging.error(f"Failed to track user {user_id}: {e}")


# ----------------------------------------
# TELEGRAM HANDLERS
# ----------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start and register user."""
    await track_user(update.effective_user)
    await update.message.reply_text(
        f"வணக்கம் {update.effective_user.first_name}! 👋\n\n"
        "🎬 2025 HD தமிழ் படங்கள் இங்கே!\n"
        "திரைப்படத்தின் பெயரை டைப் செய்யவும்."
    )


@restricted
async def addmovie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start movie addition process."""
    user_files[update.effective_user.id] = {"poster": None, "movies": []}
    await update.message.reply_text("போஸ்டர் மற்றும் 3 movie files அனுப்பவும் (480p,720p,1080p)")


async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save movie poster or files sent by admin."""
    user_id = update.message.from_user.id
    if user_id not in user_files:
        await update.message.reply_text("❗ முதலில் /addmovie அனுப்பவும்.")
        return

    user_data = user_files[user_id]
    msg = update.message

    # Handle poster
    if msg.photo:
        user_data["poster"] = msg.photo[-1].file_id
        await msg.reply_text("🖼️ Poster received.")
        asyncio.create_task(delete_after_delay(context, msg.chat.id, msg.message_id))
        return

    # Handle movie documents
    if msg.document:
        if len(user_data["movies"]) >= 3:
            await msg.reply_text("❗ Already received 3 movie files.")
            return
        user_data["movies"].append({
            "file_id": msg.document.file_id,
            "file_name": msg.document.file_name
        })
        await msg.reply_text(f"🎥 Movie file {len(user_data['movies'])} received.")
        asyncio.create_task(delete_after_delay(context, msg.chat.id, msg.message_id))

    # Check if ready to save
    if user_data["poster"] and len(user_data["movies"]) == 3:
        title_raw = extract_title(user_data["movies"][0]["file_name"])
        file_ids = [m["file_id"] for m in user_data["movies"]]
        if save_movie_to_db(title_raw, user_data["poster"], file_ids):
            global movies_data
            movies_data = load_movies_data()
            await msg.reply_text(f"✅ Movie '{title_raw}' saved successfully")
        else:
            await msg.reply_text("❌ Failed to save movie.")
        user_files[user_id] = {"poster": None, "movies": []}


async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user movie search."""
    query = update.message.text.strip()
    cleaned_query = clean_title(query)

    global movies_data
    if not movies_data:
        movies_data = load_movies_data()
    if not movies_data:
        await update.message.reply_text("Database empty.")
        return

    matches = process.extract(cleaned_query, list(movies_data.keys()), score_cutoff=80)
    if not matches:
        await update.message.reply_text("Movie not found.")
        return

    movie_key = matches[0][0]  # Directly take best match
    movie = movies_data.get(movie_key)
    if not movie:
        await update.message.reply_text("Movie data missing.")
        return

    caption = f"🎬 {movie_key.title()}"
    keyboard = [[
        InlineKeyboardButton("480p", callback_data=f"res|{movie_key}|480p"),
        InlineKeyboardButton("720p", callback_data=f"res|{movie_key}|720p"),
        InlineKeyboardButton("1080p", callback_data=f"res|{movie_key}|1080p")
    ]]
    await update.message.reply_photo(movie["poster_url"], caption=caption, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_resolution_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle movie resolution selection."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    if len(parts) != 3:
        return

    _, movie_key, res = parts
    if not await is_user_subscribed(query.from_user.id, context):
        await query.message.reply_text(
            "⚠️ Subscribe to channel first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Join Channel", url=PRIVATE_CHANNEL_LINK)]
            ])
        )
        return

    movie = movies_data.get(movie_key)
    if not movie:
        await query.message.reply_text("Movie not found.")
        return

    file_id = movie["files"].get(res)
    if not file_id:
        await query.message.reply_text("Resolution not available.")
        return

    caption = f"🎬 {movie_key.title()} - {res}p"
    sent_msg = await context.bot.send_document(chat_id=query.message.chat_id, document=file_id, caption=caption)
    asyncio.create_task(delete_after_delay(context, sent_msg.chat.id, sent_msg.message_id))


# ----------------------------------------
# MAIN
# ----------------------------------------
async def main():
    global movies_data
    movies_data = load_movies_data()

    app = ApplicationBuilder().token(TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addmovie", addmovie))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, save_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, send_movie))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_resolution_click, pattern=r"^res\|"))

    logging.info("🚀 Bot started...")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
